"""Tests for event loop stall attribution (sky/server/loop_stall.py)."""
import asyncio
import gzip
import io
import logging
import threading
import time
from typing import List, Optional

import pytest

from sky.server import loop_stall
from sky.skylet import constants
from sky.utils import perf_utils


def _run_with_watchdog(
        stall_fn,
        threshold: float = 0.2,
        settle: float = 0.6,
        recover: float = 0.5,
        use_uvloop: bool = False) -> loop_stall.LoopStallWatchdog:
    """Runs `stall_fn` on an event loop guarded by a watchdog.

    `settle` gives the heartbeat time to establish itself before the stall, and
    `recover` gives the watchdog time to notice the loop coming back so the
    recovery path is exercised too.
    """
    watchdog: List[loop_stall.LoopStallWatchdog] = []

    async def main():
        wd = loop_stall.LoopStallWatchdog(asyncio.get_running_loop(),
                                          threshold=threshold,
                                          heartbeat_interval=0.05,
                                          poll_interval=0.02)
        watchdog.append(wd)
        wd.start()
        try:
            await asyncio.sleep(settle)
            stall_fn()
            await asyncio.sleep(recover)
        finally:
            wd.stop()

    if use_uvloop:
        import uvloop  # pylint: disable=import-outside-toplevel
        uvloop.run(main())
    else:
        asyncio.run(main())
    return watchdog[0]


def _blocking_business_code(seconds: float) -> None:
    """A named, non-framework function that blocks the loop."""
    time.sleep(seconds)


def _messages_matching(records: List[logging.LogRecord],
                       needle: str) -> List[str]:
    messages = [r.getMessage() for r in records]
    return [m for m in messages if needle in m]


@pytest.fixture(name='stall_logs')
def stall_logs_fixture():
    """Collects the watchdog's log records.

    SkyPilot disables logger propagation (sky_logging.py), so caplog never sees
    these; attach a handler to the module's own logger instead. Records arrive
    from the watchdog thread, but logging serializes emit() and every test
    joins that thread before asserting.
    """
    records: List[logging.LogRecord] = []

    class _Collector(logging.Handler):

        def emit(self, record):
            records.append(record)

    handler = _Collector(level=logging.WARNING)
    previous_level = loop_stall.logger.level
    loop_stall.logger.addHandler(handler)
    loop_stall.logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        loop_stall.logger.removeHandler(handler)
        loop_stall.logger.setLevel(previous_level)


# -- attribution -----------------------------------------------------------


def test_synthetic_stall_names_the_blocking_function(stall_logs):
    """A stall in a known function is attributed to that function."""
    _run_with_watchdog(lambda: _blocking_business_code(0.5))

    stalls = _messages_matching(stall_logs, 'Event loop stalled')
    assert stalls, 'no stall was reported'
    message = stalls[0]
    assert 'test_loop_stall:_blocking_business_code' in message
    # file:line of the frame that was actually blocking, not of the asyncio
    # handle that scheduled it.
    assert 'test_loop_stall.py:' in message
    assert 'in _blocking_business_code' in message
    # The innermost frame is time.sleep's caller, so the blocking frame and
    # the innermost frame coincide here.
    assert 'blocking frame:' in message
    assert 'in-flight task:' in message

    assert _messages_matching(
        stall_logs, 'Event loop recovered'), ('recovery was not reported')


@pytest.mark.parametrize('use_uvloop', [False, True])
def test_attribution_works_under_both_loop_implementations(
        stall_logs, use_uvloop):
    """sys._current_frames is CPython level, so uvloop changes nothing."""
    if use_uvloop:
        pytest.importorskip('uvloop')
    _run_with_watchdog(lambda: _blocking_business_code(0.5),
                       use_uvloop=use_uvloop)

    stalls = _messages_matching(stall_logs, 'Event loop stalled')
    assert stalls
    assert 'test_loop_stall:_blocking_business_code' in stalls[0]


def test_gzip_stall_names_the_compression_call_site(stall_logs):
    """Replays the shape of the gzip-on-the-event-loop stall.

    zlib releases the GIL while compressing, which is what made this invisible
    to a sampling profiler run without --idle. The watchdog does not care: the
    loop thread's innermost Python frame is the caller of the C code.
    """

    # Varied keys, so level 9 actually has to work for its matches. A payload
    # of repeated bytes compresses an order of magnitude faster and would not
    # stall at all.
    chunk = b'{' + b','.join(
        f'"{i:08d}":"value-{i}"'.encode() for i in range(200000)) + b'}'

    def compress_on_loop():
        buffer = io.BytesIO()
        # compresslevel=9 is what Starlette's GZipMiddleware defaults to, and
        # the chunked writes are the streaming response path.
        with gzip.GzipFile(fileobj=buffer, mode='wb', compresslevel=9) as f:
            for _ in range(8):
                f.write(chunk)

    _run_with_watchdog(compress_on_loop, threshold=0.2, recover=0.6)

    gzip_stalls = _messages_matching(stall_logs, 'gzip:write')
    assert gzip_stalls, ('gzip stall not attributed, got: '
                         f'{[r.getMessage() for r in stall_logs]}')
    message = gzip_stalls[0]
    # gzip is not framework plumbing, so it is named directly rather than
    # being collapsed into its caller.
    assert 'gzip.py:' in message
    # The chain back to the code that chose to compress on the loop is kept.
    assert 'in compress_on_loop' in message


def test_long_stall_is_sampled_more_than_once(stall_logs):
    """One stall yields progressively deeper snapshots, not just the first.

    The per-source dedup must not swallow these: they are the rest of one
    story, and a stall that moves through several blocking calls is only
    visible if it is sampled more than once.
    """
    _run_with_watchdog(lambda: _blocking_business_code(1.6),
                       threshold=0.15,
                       recover=0.5)

    stalls = _messages_matching(stall_logs, 'Event loop stalled')
    assert len(stalls) >= 2, f'expected repeated samples, got {len(stalls)}'
    # Sampling doubles, so the reported lag grows.
    lags = [float(m.split('stalled ')[1].split('s ')[0]) for m in stalls]
    assert lags == sorted(lags), lags
    assert lags[-1] > lags[0] * 1.5, lags


def _wait_for_another_thread(event: threading.Event, seconds: float) -> None:
    """Blocks the loop on a cross-thread wait, the way Future.result() does."""
    event.wait(seconds)


def test_loop_blocked_on_a_thread_names_both_sides(stall_logs):
    """A loop waiting on another thread reports the caller *and* the threads.

    Attributing this to the caller alone answers where the loop is stuck but
    not why - the reason is in whichever thread is not releasing it.
    """
    event = threading.Event()
    marker = threading.Event()

    def holder_thread():
        marker.wait(2)

    holder = threading.Thread(target=holder_thread,
                              name='fake-holder',
                              daemon=True)
    holder.start()
    try:
        _run_with_watchdog(lambda: _wait_for_another_thread(event, 0.6),
                           threshold=0.2)
    finally:
        marker.set()
        holder.join(timeout=5)

    stalls = _messages_matching(stall_logs, 'Event loop stalled')
    assert stalls, 'no stall was reported'
    message = stalls[0]
    # The call site that chose to block the loop.
    assert 'test_loop_stall:_wait_for_another_thread' in message
    # The innermost frame is the wait itself, which is kept verbatim.
    assert 'threading.py:' in message
    # And the other threads, since one of them is the reason for the wait.
    assert 'waiting on another thread' in message
    assert 'fake-holder' in message


def test_bootstrap_frames_are_trimmed():
    """Frames outside the outermost asyncio frame are process bootstrap."""
    stack = [
        _frame('sky.server.server', 'api_get'),
        _frame('starlette.routing', 'app'),
        _frame('asyncio.events', '_run'),
        _frame('asyncio.base_events', '_run_once'),
        _frame('asyncio.runners', 'run'),
        _frame('sky.server.uvicorn', 'run'),
        _frame('__main__', '<module>'),
    ]
    trimmed = loop_stall._trim_to_current_callback(stack)  # pylint: disable=protected-access
    modules = [f.module for f in trimmed]
    assert 'sky.server.server' in modules
    # The uvicorn entry point is on the stack for the worker's whole life and
    # is not what stalled.
    assert 'sky.server.uvicorn' not in modules
    assert '__main__' not in modules


def test_trimming_keeps_a_stack_with_no_loop_frames():
    stack = [_frame('sky.server.server', 'api_get'), _frame('gzip', 'write')]
    assert loop_stall._trim_to_current_callback(stack) == stack  # pylint: disable=protected-access


def test_starved_loop_dumps_other_threads(stall_logs):
    """A parked loop is reported as starved, with the other threads' stacks.

    The stall is simulated rather than provoked: a loop that is genuinely
    parked in its selector holds only asyncio frames, which is the condition
    under test, and a real GIL-starvation race would be flaky.
    """
    loop = asyncio.new_event_loop()
    loop_ready = threading.Event()

    def park_the_loop():
        asyncio.set_event_loop(loop)
        loop.call_soon(loop_ready.set)
        loop.run_forever()

    loop_thread = threading.Thread(target=park_the_loop,
                                   name='parked-loop',
                                   daemon=True)
    loop_thread.start()
    assert loop_ready.wait(timeout=5)

    stop = threading.Event()
    workers_ready = threading.Barrier(4, timeout=5)

    def worker_thread():
        workers_ready.wait()
        stop.wait()

    workers = [
        threading.Thread(target=worker_thread,
                         name=f'fake-worker-{i}',
                         daemon=True) for i in range(3)
    ]
    for worker in workers:
        worker.start()
    workers_ready.wait()

    watchdog = loop_stall.LoopStallWatchdog(loop, threshold=0.1)
    try:
        watchdog._loop_thread_id = loop_thread.ident  # pylint: disable=protected-access
        # Give the loop a moment to settle into the selector.
        time.sleep(0.2)
        watchdog._capture(5.0, first_of_stall=True)  # pylint: disable=protected-access
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)
        loop.close()

    starved = _messages_matching(stall_logs, 'parked in its own poll')
    assert starved, ('starved stall not reported, got: '
                     f'{[r.getMessage() for r in stall_logs]}')
    assert 'busiest other threads' in starved[0]
    assert 'fake-worker-' in starved[0]
    # Threads doing the same thing collapse into one line with a count.
    assert '3 thread(s) in' in starved[0]
    # The frame that called run_forever is still on the loop thread's stack.
    # Classifying on "does the stack hold any application frame" would blame
    # it for every starvation stall; classification is on the innermost frame.
    assert 'park_the_loop' not in starved[0]


# -- rate limiting and cardinality ----------------------------------------


def _watchdog_for_limits() -> loop_stall.LoopStallWatchdog:
    return loop_stall.LoopStallWatchdog(asyncio.new_event_loop(), threshold=1.0)


def test_repeated_stalls_from_one_source_are_deduped():
    watchdog = _watchdog_for_limits()
    assert watchdog._should_log('a:b')  # pylint: disable=protected-access
    assert not watchdog._should_log('a:b')  # pylint: disable=protected-access
    # A different source is not suppressed by the first one's dedup window.
    assert watchdog._should_log('c:d')  # pylint: disable=protected-access


def test_stall_storm_is_capped_per_minute(stall_logs):
    watchdog = _watchdog_for_limits()
    allowed = sum(1 for i in range(50) if watchdog._should_log(f'mod{i}:fn')  # pylint: disable=protected-access
                 )
    assert allowed == loop_stall._MAX_DUMPS_PER_MINUTE  # pylint: disable=protected-access
    assert _messages_matching(stall_logs, 'suppressing further dumps')


def test_source_labels_are_capped():
    watchdog = _watchdog_for_limits()
    labels = {
        watchdog._label_for(f'mod{i}:fn')  # pylint: disable=protected-access
        for i in range(loop_stall._MAX_SOURCE_LABELS * 4)  # pylint: disable=protected-access
    }
    assert len(labels) == loop_stall._MAX_SOURCE_LABELS + 1  # pylint: disable=protected-access
    assert loop_stall._OVERFLOW_SOURCE in labels  # pylint: disable=protected-access
    # A source already seen keeps its own label rather than being collapsed.
    assert watchdog._label_for('mod0:fn') == 'mod0:fn'  # pylint: disable=protected-access


def test_no_dumps_when_the_loop_is_healthy(stall_logs):
    """A healthy loop is never sampled at all.

    This is what makes the watchdog safe to leave on by default: the stack
    walk, and everything else that costs more than a float comparison, happens
    only once the loop is already stalled.
    """
    captures = []

    class CountingWatchdog(loop_stall.LoopStallWatchdog):

        def _capture(self, lag, first_of_stall):
            captures.append(lag)
            return super()._capture(lag, first_of_stall)

    async def main():
        watchdog = CountingWatchdog(asyncio.get_running_loop(),
                                    threshold=0.3,
                                    heartbeat_interval=0.05,
                                    poll_interval=0.02)
        watchdog.start()
        try:
            # Plenty of loop iterations, none of them blocking.
            for _ in range(60):
                await asyncio.sleep(0.01)
        finally:
            watchdog.stop()

    asyncio.run(main())
    assert not captures, f'sampled a healthy loop {len(captures)} time(s)'
    assert not _messages_matching(stall_logs, 'Event loop stalled')


# -- plumbing classification ----------------------------------------------


@pytest.mark.parametrize('module', [
    'asyncio',
    'asyncio.base_events',
    'uvloop',
    'uvloop.loop',
    'uvicorn.protocols.http.httptools_impl',
    'anyio.to_thread',
    'starlette.middleware.base',
    'starlette.routing',
    'fastapi.routing',
    'concurrent.futures.thread',
    'threading',
    'selectors',
    'contextlib',
])
def test_dispatch_modules_are_not_used_as_a_source(module):
    assert loop_stall._is_plumbing(module)  # pylint: disable=protected-access


@pytest.mark.parametrize('module', [
    'sky.server.server',
    'sky.jobs.utils',
    'gzip',
    'ssl',
    'sqlalchemy.engine.base',
    'casbin.core_enforcer',
    'starlette.middleware.gzip',
    'some_plugin_package.middleware',
])
def test_application_and_library_modules_are_used_as_a_source(module):
    """Not an allowlist: third-party, stdlib and plugin frames all count."""
    assert not loop_stall._is_plumbing(module)  # pylint: disable=protected-access


def _frame(module: str, function: str = 'fn') -> 'loop_stall._FrameInfo':
    return loop_stall._FrameInfo(  # pylint: disable=protected-access
        module=module,
        function=function,
        filename=f'{module}.py',
        lineno=1)


@pytest.mark.parametrize('module,function', [
    ('selectors', 'select'),
    ('asyncio.base_events', '_run_once'),
    ('asyncio.runners', 'run'),
    ('uvloop.loop', 'run_forever'),
])
def test_poll_frames_mean_the_loop_was_parked(module, function):
    assert loop_stall._is_parked(_frame(module, function))  # pylint: disable=protected-access


@pytest.mark.parametrize(
    'module,function',
    [
        # Blocking on a lock or a thread result from the loop is a real on-loop
        # stall and belongs to the caller, not to "the loop was idle".
        ('threading', 'wait'),
        ('concurrent.futures._base', 'result'),
        ('gzip', 'write'),
        ('sky.server.server', 'api_get'),
    ])
def test_blocking_frames_are_not_treated_as_parked(module, function):
    assert not loop_stall._is_parked(_frame(module, function))  # pylint: disable=protected-access


def test_module_matching_does_not_match_on_a_name_prefix():
    """`asyncio_helpers` is not asyncio."""
    assert not loop_stall._is_parked(_frame('asyncio_helpers'))  # pylint: disable=protected-access
    assert not loop_stall._is_plumbing('anyio_extras')  # pylint: disable=protected-access
    assert not loop_stall._is_plumbing('uvicornish')  # pylint: disable=protected-access


# -- configuration ---------------------------------------------------------


def test_threshold_defaults_to_on(monkeypatch):
    monkeypatch.delenv(constants.ENV_VAR_LOOP_STALL_THRESHOLD_MS, raising=False)
    expected = constants.DEFAULT_LOOP_STALL_THRESHOLD_MS / 1000.0
    assert perf_utils.get_loop_stall_threshold() == expected


@pytest.mark.parametrize('value,expected', [
    ('2500', 2.5),
    ('250', 0.25),
    ('0', None),
    ('-1', None),
    ('not-a-number', None),
])
def test_threshold_parsing(monkeypatch, value: str, expected: Optional[float]):
    monkeypatch.setenv(constants.ENV_VAR_LOOP_STALL_THRESHOLD_MS, value)
    assert perf_utils.get_loop_stall_threshold() == expected


def test_start_watchdog_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv(constants.ENV_VAR_LOOP_STALL_THRESHOLD_MS, '0')

    async def main():
        return loop_stall.start_watchdog()

    assert asyncio.run(main()) is None


def test_start_watchdog_is_idempotent(monkeypatch):
    monkeypatch.setenv(constants.ENV_VAR_LOOP_STALL_THRESHOLD_MS, '1000')

    async def main():
        watchdog = loop_stall.start_watchdog()
        assert watchdog is not None
        thread = watchdog._thread  # pylint: disable=protected-access
        watchdog.start()
        assert watchdog._thread is thread  # pylint: disable=protected-access
        watchdog.stop()
        assert not thread.is_alive()

    asyncio.run(main())
