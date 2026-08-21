"""Tests for event loop stall attribution (sky/server/loop_stall.py)."""
import asyncio
import gzip
import io
import json
import logging
import subprocess
import sys
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


def _details(records: List[logging.LogRecord]) -> List[dict]:
    """Parses the trailing JSON detail out of each stall record.

    Each stall is one log line: a human-readable head plus a JSON object. The
    tests assert on the parsed object rather than on the head, so wording
    changes do not break them and the payload is checked for real.
    """
    out = []
    for record in records:
        message = record.getMessage()
        if 'Event loop stalled' not in message:
            continue
        start = message.find('{')
        assert start != -1, f'stall record carries no JSON: {message}'
        out.append(json.loads(message[start:]))
    return out


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
    detail = _details(stall_logs)[0]
    assert detail['kind'] == 'on_loop'
    assert detail['source'] == 'test_loop_stall:_blocking_business_code'
    assert 'in _blocking_business_code' in detail['blocking']
    assert detail['task']
    # The stack is reported outermost-first, so the blocking frame is last.
    assert 'in _blocking_business_code' in detail['frames'][-1]

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


def test_deduped_stall_emits_no_orphan_recovery(stall_logs):
    """A stall whose dump was deduped must not log a recovery either.

    The recovery line has no rate limit of its own, so without this a loop
    stalling repeatedly in one place would emit a stream of `recovered` lines
    with no matching `stalled` line - reading as a phantom stall.
    """

    async def main():
        watchdog = loop_stall.LoopStallWatchdog(asyncio.get_running_loop(),
                                                threshold=0.2,
                                                heartbeat_interval=0.05,
                                                poll_interval=0.02)
        watchdog.start()
        try:
            await asyncio.sleep(0.4)
            _blocking_business_code(0.5)
            await asyncio.sleep(0.4)
            # Same source, well inside the dedup window.
            _blocking_business_code(0.5)
            await asyncio.sleep(0.4)
        finally:
            watchdog.stop()

    asyncio.run(main())

    assert _messages_matching(stall_logs, 'Event loop stalled')
    recoveries = _messages_matching(stall_logs, 'Event loop recovered')
    assert len(recoveries) == 1, (
        f'expected one recovery for the one reported stall, got {recoveries}')


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
    detail = _details(stall_logs)[0]
    assert detail['kind'] == 'waiting'
    holders = [t['example_thread'] for t in detail['other_threads']]
    assert any('fake-holder' in h for h in holders), holders


def test_one_log_line_per_stall_with_parsable_json(stall_logs):
    """A stall is one greppable record, not a dozen interleaved lines."""
    _run_with_watchdog(lambda: _blocking_business_code(0.5))

    stalls = [r for r in stall_logs if 'Event loop stalled' in r.getMessage()]
    assert stalls
    for record in stalls:
        message = record.getMessage()
        assert '\n' not in message, f'record spans lines: {message!r}'
        detail = json.loads(message[message.find('{'):])
        assert set(detail) >= {
            'lag_s', 'threshold_s', 'kind', 'source', 'blocking', 'innermost',
            'task', 'frames'
        }, sorted(detail)
        # The head stays human-readable and carries the source for grepping.
        assert message.startswith('Event loop stalled')
        assert detail['source'] in message[:message.find('{')]


def test_library_internals_collapse_but_the_caller_survives():
    """The frame budget must not be spent on library plumbing.

    Regression: the cap used to keep the innermost N frames, so a deep
    SQLAlchemy stack filled the budget with its own internals and dropped the
    outermost frames -- which are the request handler that got there. Both ends
    have to survive.
    """
    site = '/usr/local/lib/python3.10/site-packages/'
    frames = [_frame('sqlalchemy.engine.default', 'do_execute')]
    frames[0] = frames[0]._replace(filename=site +
                                   'sqlalchemy/engine/default.py')
    # Ten more SQLAlchemy layers between the leaf and our code.
    for i in range(10):
        frames.append(
            loop_stall._FrameInfo(  # pylint: disable=protected-access
                module='sqlalchemy.orm.session',
                function=f'_layer{i}',
                filename=site + 'sqlalchemy/orm/session.py',
                lineno=100 + i))
    frames.append(
        loop_stall._FrameInfo(  # pylint: disable=protected-access
            module='sky.global_user_state',
            function='get_all_users',
            filename='/sky/global_user_state.py',
            lineno=657))
    frames.append(
        loop_stall._FrameInfo(  # pylint: disable=protected-access
            module='sky.server.server',
            function='all_contexts',
            filename='/sky/server/server.py',
            lineno=3701))

    rendered = loop_stall._collapse_frames(frames)  # pylint: disable=protected-access
    joined = '\n'.join(rendered)
    # The endpoint that triggered it is the whole point and must be present.
    assert 'all_contexts' in joined, rendered
    assert 'get_all_users' in joined, rendered
    # So must the frame that was actually blocking.
    assert 'do_execute' in joined, rendered
    # The intermediate library layers are summarised, not enumerated.
    assert any(
        'more sqlalchemy frame(s)' in line for line in rendered), rendered
    assert '_layer5' not in joined
    # Outermost first, so it reads like a traceback.
    assert 'all_contexts' in rendered[0]
    assert 'do_execute' in rendered[-1]


@pytest.mark.parametrize('path,expected', [
    ('/usr/local/lib/python3.10/site-packages/sqlalchemy/engine/default.py',
     'sqlalchemy/engine/default.py'),
    ('/usr/local/lib/python3.10/asyncio/runners.py', 'asyncio/runners.py'),
    ('/skypilot/sky/server/server.py', '/skypilot/sky/server/server.py'),
])
def test_install_prefixes_are_trimmed(path, expected):
    assert loop_stall._short_path(path) == expected  # pylint: disable=protected-access


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


def _piped_subprocess_on_the_loop() -> None:
    """Blocks the loop in a way that bottoms out in the selector."""
    subprocess.run([sys.executable, '-c', 'import time; time.sleep(0.6)'],
                   stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE,
                   check=False)


@pytest.mark.parametrize('use_uvloop', [False, True])
def test_blocking_call_that_ends_in_the_selector_is_not_called_parked(
        stall_logs, use_uvloop):
    """A poll frame alone does not mean the loop is idle.

    `subprocess.run` with pipes waits in `selectors.select`, the same frame a
    parked loop sits in. It is on-loop work and must be attributed to its
    caller, not reported as the loop being starved from outside.
    """
    if use_uvloop:
        pytest.importorskip('uvloop')
    _run_with_watchdog(_piped_subprocess_on_the_loop,
                       threshold=0.2,
                       recover=0.6,
                       use_uvloop=use_uvloop)

    details = _details(stall_logs)
    assert details, 'no stall was reported'
    kinds = {d['kind'] for d in details}
    assert 'parked' not in kinds, details
    on_loop = [d for d in details if d['kind'] == 'on_loop']
    assert on_loop, details
    joined = '\n'.join('\n'.join(d['frames']) for d in on_loop)
    assert '_piped_subprocess_on_the_loop' in joined, joined


def test_watchdog_can_be_restarted_after_stop(stall_logs):
    """A second start() must produce a live watchdog, not a dead thread."""

    async def main():
        watchdog = loop_stall.LoopStallWatchdog(asyncio.get_running_loop(),
                                                threshold=0.2,
                                                heartbeat_interval=0.05,
                                                poll_interval=0.02)
        watchdog.start()
        watchdog.stop()
        watchdog.start()
        try:
            await asyncio.sleep(0.3)
            _blocking_business_code(0.5)
            await asyncio.sleep(0.4)
        finally:
            watchdog.stop()

    asyncio.run(main())
    assert _messages_matching(
        stall_logs,
        'Event loop stalled'), ('the restarted watchdog reported nothing')


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
    detail = _details(stall_logs)[0]
    assert detail['kind'] == 'parked'
    assert detail['source'] == 'starved'
    groups = {t['source']: t for t in detail['other_threads']}
    worker = groups['test_loop_stall:worker_thread']
    # Threads doing the same thing collapse into one entry with a count.
    assert worker['count'] == 3, detail['other_threads']
    assert 'fake-worker-' in worker['example_thread']
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


def _counting_watchdog_class(captures: List[float]):
    """A watchdog subclass that records every sample it takes.

    The override's signature must match the production call in `_watch`
    exactly. If it did not, the TypeError would be swallowed by `_watch`'s
    except clause and `captures` would stay empty - which would make
    test_no_dumps_when_the_loop_is_healthy pass even if a healthy loop were
    being sampled on every poll.
    """

    class CountingWatchdog(loop_stall.LoopStallWatchdog):

        def _capture(self, lag, first_of_stall, allow_repeat=False):
            captures.append(lag)
            return super()._capture(lag, first_of_stall, allow_repeat)

    return CountingWatchdog


def _run_counting_watchdog(captures: List[float], stall_fn,
                           threshold: float) -> None:
    watchdog_cls = _counting_watchdog_class(captures)

    async def main():
        watchdog = watchdog_cls(asyncio.get_running_loop(),
                                threshold=threshold,
                                heartbeat_interval=0.05,
                                poll_interval=0.02)
        watchdog.start()
        try:
            await asyncio.sleep(0.3)
            stall_fn()
            await asyncio.sleep(0.3)
        finally:
            watchdog.stop()

    asyncio.run(main())


def test_no_dumps_when_the_loop_is_healthy(stall_logs):
    """A healthy loop is never sampled at all.

    This is what makes the watchdog safe to leave on by default: the stack
    walk, and everything else that costs more than a float comparison, happens
    only once the loop is already stalled.
    """
    captures: List[float] = []

    async def main():
        watchdog = _counting_watchdog_class(captures)(
            asyncio.get_running_loop(),
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


def test_counting_harness_does_observe_a_real_stall(stall_logs):
    """Positive control for the test above.

    Without this, `assert not captures` would also hold if the override were
    never reached at all, and the healthy-loop test would prove nothing.
    """
    del stall_logs  # Only here to keep the watchdog's output off the console.
    captures: List[float] = []
    _run_counting_watchdog(captures,
                           lambda: _blocking_business_code(0.5),
                           threshold=0.2)
    assert captures, 'the counting override was never reached'


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
