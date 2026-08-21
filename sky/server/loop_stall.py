"""Source attribution for API server event loop stalls.

`loop_lag_monitor` in `sky/server/server.py` answers *how long* the event loop
stalled. This module answers *where*.

The mechanism is inverted relative to asyncio's own debug mode. Rather than
instrumenting every callback so that a slow one can be reported after it
returns, a watchdog thread watches a heartbeat that the loop refreshes on a
timer. When the heartbeat goes stale the loop thread is - by definition - stuck
inside whatever it is currently running, while the watchdog thread is not, so
the watchdog can read the loop thread's Python stack via
`sys._current_frames()` and see the frame that is actually blocking, mid-stall.

Two properties follow from that:

- The cost is on the stall path only. Nothing is instrumented per callback, so
  unlike `loop.set_debug(True)` (which captures a source traceback on every
  handle creation) this can be left on by default, which means it is already
  running when an incident starts instead of being switched on afterwards.
- The blocking frame is reported while the loop is still stuck, so a stall in
  progress is visible rather than only being reported once it ends.

The stack is reduced to a `source` label - the innermost frame that is not
framework dispatch plumbing - which is stable enough to aggregate on. The full
elided stack goes to the log so the actual call site is never lost.

When the loop thread's innermost frame is the loop's own poll, the loop was
parked and the lag came from outside it - GIL contention with another thread in
the process, or the process not being scheduled. The other threads' stacks are
dumped in that case, because the loop thread's own stack has nothing to say: it
would otherwise attribute every such stall to whichever frame happened to call
`run_forever`.
"""
import asyncio
import collections
import json
import math
import re
import sys
import threading
import time
import types
from typing import Deque, Dict, List, Mapping, Optional, Tuple

from sky import sky_logging
from sky.metrics import utils as metrics_utils
from sky.utils import perf_utils

logger = sky_logging.init_logger(__name__)

# How often the loop refreshes the heartbeat. One no-op callback per interval;
# matches the cadence of loop_lag_monitor.
_HEARTBEAT_INTERVAL = 0.1
# How often the watchdog thread checks the heartbeat. Each check is a float
# read and a subtraction, so this can be tighter than the heartbeat itself and
# bounds how late a stall is noticed.
_POLL_INTERVAL = 0.05

# Frames deeper than this are dropped. A stack this deep is pathological on its
# own and we only need the innermost frames to attribute the stall.
_MAX_STACK_DEPTH = 200
# Application frames kept in the logged stack, after same-module runs have been
# collapsed. If even that is exceeded, both *ends* are kept: the innermost
# frames say what was blocking, the outermost ones say which request got there,
# and dropping either end loses half the answer.
_MAX_LOGGED_FRAMES = 24
_KEEP_OUTERMOST = 8
# A consecutive run of at least this many frames from one top-level module is
# collapsed to its first and last frame. Library internals (a dozen SQLAlchemy
# layers, say) would otherwise crowd out the caller that matters.
_COLLAPSE_RUN = 4

# At most one dump per source per this many seconds, so a source that stalls
# repeatedly is reported without filling the log.
_DEDUP_SECONDS = 60.0
# Ceiling on dumps across all sources within _DUMP_WINDOW_SECONDS, so a stall
# storm involving many distinct sources cannot amplify into a logging storm
# either.
_MAX_DUMPS_PER_MINUTE = 10
_DUMP_WINDOW_SECONDS = 60.0
# The all-thread dump is much larger than a single stack, so it is rate limited
# separately and far more aggressively.
_ALL_THREAD_DUMP_INTERVAL = 300.0
_MAX_THREADS_SCANNED = 512
_MAX_THREAD_GROUPS = 5

# Distinct `source` label values a process will emit before collapsing the
# rest into _OVERFLOW_SOURCE, so the metric's cardinality stays bounded no
# matter how many distinct call sites stall.
_MAX_SOURCE_LABELS = 32
_OVERFLOW_SOURCE = 'other'
# Used when the loop was parked in its own poll - see module docstring.
_STARVED_SOURCE = 'starved'

# Modules that only dispatch, never block on their own behalf. Frames from
# these are skipped when picking the source, because attributing a stall to
# them is what the asyncio handle repr already does and it is exactly the
# non-answer this module exists to replace.
#
# Note that this is deliberately not an allowlist of first-party packages:
# anything not listed here is fair game as a source, so a stall inside a
# third-party library or the standard library (`gzip`, `ssl`, `yaml`, ...) is
# named directly, and so are frames from code loaded into the server through
# plugins, without this list having to know about any of it.
_PLUMBING_ROOTS = (
    'asyncio',
    'uvloop',
    'uvicorn',
    'anyio',
    'concurrent.futures',
)
_PLUMBING_MODULES = frozenset({
    'threading',
    'selectors',
    'contextlib',
    'functools',
    'starlette.applications',
    'starlette.routing',
    'starlette.concurrency',
    'starlette._exception_handler',
    'starlette._utils',
    'starlette.middleware.base',
    'starlette.middleware.errors',
    'starlette.middleware.exceptions',
    'fastapi.applications',
    'fastapi.routing',
    __name__,
})

# Innermost-frame locations that mean the loop is not running anything at all -
# it is sitting in its own poll. Lag observed with the loop here was not caused
# by work on the loop, so the loop thread's stack has nothing to offer and the
# other threads are dumped instead.
_PARKED_ROOTS = (
    'asyncio',
    'uvloop',
    'selectors',
)

# Innermost-frame locations that mean the loop is blocked waiting on another
# thread rather than doing work of its own. The frame that entered the wait is
# still the right attribution - blocking the loop on a lock is a bug at the
# call site - but the reason it is waiting lives in another thread, so those
# get dumped as well. Observed in practice: `run_in_executor` on a loop whose
# executor thread then holds the GIL inside a single long C call, which leaves
# the loop stuck in `Thread.start`'s `Event.wait`.
_WAITING_ROOTS = (
    'threading',
    'concurrent.futures',
    'multiprocessing.synchronize',
    'multiprocessing.connection',
    'queue',
)

_FrameInfo = collections.namedtuple(
    '_FrameInfo', ['module', 'function', 'filename', 'lineno'])


def _module_matches(module: str, roots: Tuple[str, ...]) -> bool:
    return any(
        module == root or module.startswith(f'{root}.') for root in roots)


def _is_plumbing(module: str) -> bool:
    return (module in _PLUMBING_MODULES or
            _module_matches(module, _PLUMBING_ROOTS))


def _is_parked(innermost: _FrameInfo) -> bool:
    """Whether the loop was polling rather than executing something."""
    return _module_matches(innermost.module, _PARKED_ROOTS)


def _is_waiting_on_another_thread(innermost: _FrameInfo) -> bool:
    """Whether the loop is blocked on a cross-thread synchronization wait."""
    return _module_matches(innermost.module, _WAITING_ROOTS)


def _trim_to_current_callback(stack: List[_FrameInfo]) -> List[_FrameInfo]:
    """Drops the frames that got the loop running in the first place.

    Everything outside the outermost asyncio frame is process bootstrap - the
    uvicorn entry point, multiprocessing's spawn machinery, `<module>` - which
    is on the stack for the entire life of the worker and says nothing about
    this stall. Keeping it would push the frames that matter off the end of a
    depth-capped log line.
    """
    boundary = None
    for index, frame in enumerate(stack):
        if _module_matches(frame.module, _PARKED_ROOTS):
            boundary = index
    if boundary is None:
        return stack
    return stack[:boundary]


def _walk_stack(frame: Optional[types.FrameType],
                max_depth: int = _MAX_STACK_DEPTH) -> List[_FrameInfo]:
    """Summarizes a frame's stack, innermost frame first.

    Deliberately avoids `traceback.extract_stack`, which resolves source lines
    through `linecache` - that is filesystem I/O, and this runs while holding a
    reference into another thread's live stack.

    The stack being walked belongs to a running thread, so it can shift under
    us and yield a slightly torn view. That is acceptable for attribution: the
    innermost frames are the ones we care about and they are read first.
    """
    stack: List[_FrameInfo] = []
    while frame is not None and len(stack) < max_depth:
        code = frame.f_code
        module = frame.f_globals.get('__name__') or '<unknown>'
        stack.append(
            _FrameInfo(module=module,
                       function=code.co_name,
                       filename=code.co_filename,
                       lineno=frame.f_lineno))
        frame = frame.f_back
    return stack


_SITE_PACKAGES = 'site-packages/'
_STDLIB_RE = re.compile(r'/lib/python3\.\d+/')


def _short_path(filename: str) -> str:
    """Trims install-location noise from a filename.

    `/usr/local/lib/python3.10/site-packages/sqlalchemy/engine/default.py`
    becomes `sqlalchemy/engine/default.py`, which is both unambiguous and short
    enough that the interesting part of a stack is not pushed off the line.
    """
    index = filename.rfind(_SITE_PACKAGES)
    if index != -1:
        return filename[index + len(_SITE_PACKAGES):]
    match = _STDLIB_RE.search(filename)
    if match is not None:
        return filename[match.end():]
    return filename


def _format_frame(frame: _FrameInfo) -> str:
    return f'{_short_path(frame.filename)}:{frame.lineno} in {frame.function}'


def _top_level(module: str) -> str:
    return module.split('.', 1)[0]


def _collapse_frames(frames: List[_FrameInfo]) -> List[str]:
    """Renders frames outermost-first, collapsing same-module runs.

    Takes frames innermost-first (as sampled) and returns display strings
    outermost-first, so it reads like a traceback. A run of consecutive frames
    from one top-level module is reduced to its first and last frame plus a
    marker, because a dozen intermediate library layers say nothing that the
    two ends do not.
    """
    ordered = list(reversed(frames))
    out: List[str] = []
    i = 0
    while i < len(ordered):
        module = _top_level(ordered[i].module)
        j = i + 1
        while j < len(ordered) and _top_level(ordered[j].module) == module:
            j += 1
        run = ordered[i:j]
        if len(run) >= _COLLAPSE_RUN:
            out.append(_format_frame(run[0]))
            out.append(f'... {len(run) - 2} more {module} frame(s) ...')
            out.append(_format_frame(run[-1]))
        else:
            out.extend(_format_frame(f) for f in run)
        i = j
    if len(out) > _MAX_LOGGED_FRAMES:
        head = out[:_KEEP_OUTERMOST]
        tail = out[_KEEP_OUTERMOST - _MAX_LOGGED_FRAMES:]
        dropped = len(out) - len(head) - len(tail)
        out = head + [f'... {dropped} more frame(s) ...'] + tail
    return out


def _as_json(detail: Dict[str, object]) -> str:
    """Serializes the stall detail as one compact line.

    A stall report used to span a dozen log lines, which meant every line
    carried its own timestamp prefix, the record interleaved with other
    workers' output, and no single grep could pull one report out whole.
    Putting the detail in a trailing JSON object keeps the record greppable as
    one line and lets `... | grep -o '{.*}' | jq` expand it on demand.
    """
    try:
        return json.dumps(detail, separators=(',', ':'), default=str)
    except (TypeError, ValueError) as e:  # pragma: no cover - defensive
        return f'{{"json_error":"{e}"}}'


def _source_of(frame: _FrameInfo) -> str:
    """Renders a stable, low-cardinality label for a frame.

    The line number is deliberately excluded: it changes whenever the file
    above it changes, which would churn the metric's label values on every
    release. It is kept in the log line, where it is useful and costs nothing.
    """
    return f'{frame.module}:{frame.function}'


def _describe_current_task(loop: asyncio.AbstractEventLoop) -> str:
    """Best-effort name of the task the loop is currently running.

    `asyncio.current_task(loop)` is documented to take an explicit loop, which
    makes it readable from another thread. It only narrows the stall down to a
    coroutine - the stack is what identifies the blocking frame - so every
    failure here is swallowed.
    """
    try:
        task = asyncio.current_task(loop)
        if task is None:
            return 'none'
        name = task.get_name()
        coro = task.get_coro()
        qualname = str(
            getattr(coro, '__qualname__', None) or
            getattr(getattr(coro, 'cr_code', None), 'co_name', None) or
            '<unknown>')
        if qualname in name:
            # Some frameworks name their tasks after the coroutine.
            return name
        return f'{name} coro={qualname}'
    except Exception:  # pylint: disable=broad-except
        return 'unavailable'


class LoopStallWatchdog:
    """Watches an event loop's heartbeat and attributes stalls to a frame."""

    def __init__(self,
                 loop: asyncio.AbstractEventLoop,
                 threshold: float,
                 heartbeat_interval: float = _HEARTBEAT_INTERVAL,
                 poll_interval: float = _POLL_INTERVAL) -> None:
        self._loop = loop
        self._threshold = threshold
        self._heartbeat_interval = heartbeat_interval
        self._poll_interval = poll_interval
        # Written only by the loop thread, read only by the watchdog thread.
        # A float attribute store and load are each atomic under the GIL, so
        # the heartbeat needs no lock - which is the point, since the heartbeat
        # is the only part of this that runs on the hot path.
        self._last_beat = time.monotonic()
        self._loop_thread_id: Optional[int] = None
        # Not named `_stop`: that shadows a private attribute of
        # threading.Thread on some versions and only fails at join() time.
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Everything below is touched by the watchdog thread only. The rate
        # limit timestamps start at -inf rather than 0 because time.monotonic()
        # has an arbitrary origin - on a freshly booted host it can be smaller
        # than the intervals below, which would suppress the very first dump.
        self._last_dump_at: Dict[str, float] = {}
        self._recent_dumps: Deque[float] = collections.deque()
        self._suppressing = False
        self._last_all_thread_dump_at = -math.inf
        self._known_sources: Dict[str, None] = {}

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Starts the watchdog thread.

        The heartbeat it watches is driven from outside, by whichever timer the
        loop already runs to measure its own lag; `beat` is what feeds it.
        """
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._watch,
                                        name='loop-stall-watchdog',
                                        daemon=True)
        self._thread.start()
        logger.debug('Event loop stall watchdog started with threshold '
                     f'{self._threshold}s')

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            # Bounded: the watchdog only ever sleeps for one poll interval.
            thread.join(timeout=self._poll_interval * 10)
        self._thread = None

    # -- loop thread -------------------------------------------------------

    def beat(self) -> None:
        """Marks the loop as alive. Must be called from the loop thread.

        Called from the loop's lag timer, which is also where the loop thread's
        id gets recorded without having to guess at loop internals.
        """
        if self._loop_thread_id is None:
            self._loop_thread_id = threading.get_ident()
        self._last_beat = time.monotonic()

    # -- watchdog thread ---------------------------------------------------

    def _current_lag(self) -> float:
        # The heartbeat is refreshed every _heartbeat_interval, so a healthy
        # loop leaves `now - last_beat` anywhere in [0, interval]. Only the
        # excess is lag, which matches how loop_lag_monitor measures it.
        return max(
            0.0,
            time.monotonic() - self._last_beat - self._heartbeat_interval)

    def _watch(self) -> None:
        in_stall = False
        logged_this_stall = False
        peak_lag = 0.0
        # Within one stall, sample at the threshold and then at each doubling,
        # so a 30s stall produces a handful of progressively deeper snapshots
        # instead of one snapshot or hundreds. This matters when the loop moves
        # through several blocking calls inside one stall - decode, then
        # render, then compress - which a single snapshot cannot show.
        next_sample_at = self._threshold
        while not self._stop_event.wait(self._poll_interval):
            if self._loop_thread_id is None:
                continue
            lag = self._current_lag()
            if lag <= self._threshold:
                # Only paired with a stall we actually reported: unlike the
                # dumps this line has no rate limit of its own, and a loop
                # oscillating around the threshold would otherwise emit
                # recoveries for stalls that were deduped away.
                if in_stall and logged_this_stall:
                    logger.warning(
                        f'Event loop recovered after stalling {peak_lag:.3f}s.')
                in_stall = False
                logged_this_stall = False
                peak_lag = 0.0
                next_sample_at = self._threshold
                continue
            peak_lag = max(peak_lag, lag)
            if lag < next_sample_at:
                continue
            next_sample_at = lag * 2
            first_of_stall = not in_stall
            in_stall = True
            try:
                # Later samples of a stall we are already reporting bypass the
                # per-source dedup - they are the rest of one story, not a
                # repeat of it - but still count against the global cap.
                logged = self._capture(lag,
                                       first_of_stall,
                                       allow_repeat=logged_this_stall)
            except Exception as e:  # pylint: disable=broad-except
                # A diagnostic must never be able to take down the server.
                logger.debug(f'Event loop stall capture failed: {e}')
                continue
            logged_this_stall = logged_this_stall or logged

    def _capture(self,
                 lag: float,
                 first_of_stall: bool,
                 allow_repeat: bool = False) -> bool:
        """Samples the loop thread's stack and reports the stall.

        Returns whether anything was logged, so the caller can tell a stall it
        is already reporting from one it has not reported at all.
        """
        loop_thread_id = self._loop_thread_id
        assert loop_thread_id is not None
        # pylint: disable-next=protected-access
        all_frames = sys._current_frames()
        try:
            loop_frame = all_frames.get(loop_thread_id)
            if loop_frame is None:
                return False
            stack = _walk_stack(loop_frame)
            # Do not keep the frame alive past the walk.
            del loop_frame
            if not stack:
                return False
            innermost = stack[0]
            callback_stack = _trim_to_current_callback(stack)
            app_frames = [
                f for f in callback_stack if not _is_plumbing(f.module)
            ]
            # A poll frame only means the loop is idle when there is nothing of
            # ours above it. Blocking calls bottom out in the same selector -
            # `subprocess.run` with a pipe, for one - and those are on-loop work
            # belonging to their caller.
            parked = _is_parked(innermost) and not app_frames
            if parked:
                source = _STARVED_SOURCE
            elif app_frames:
                source = _source_of(app_frames[0])
            else:
                # Every frame is plumbing, yet the loop is not polling: the
                # innermost frame is the most specific answer available.
                source = _source_of(innermost)
            if first_of_stall:
                metrics_utils.record_event_loop_stall(self._label_for(source))
            if not self._should_log(source, allow_repeat):
                return False
            if parked:
                self._log_starved(lag, innermost, all_frames, loop_thread_id)
            else:
                # A loop blocked on a lock or on another thread's result is
                # held up by whoever owns it, so name them too.
                other_threads = (
                    self._render_other_threads(all_frames, loop_thread_id)
                    if _is_waiting_on_another_thread(innermost) else None)
                self._log_stall(lag, innermost, app_frames or callback_stack,
                                source, other_threads)
            return True
        finally:
            # Frame objects keep their locals alive; drop the whole mapping as
            # soon as we are done with it.
            del all_frames

    def _label_for(self, source: str) -> str:
        if source in self._known_sources:
            return source
        if len(self._known_sources) >= _MAX_SOURCE_LABELS:
            return _OVERFLOW_SOURCE
        self._known_sources[source] = None
        return source

    def _should_log(self, source: str, allow_repeat: bool = False) -> bool:
        now = time.monotonic()
        if (not allow_repeat and
                now - self._last_dump_at.get(source, -math.inf) <
                _DEDUP_SECONDS):
            return False
        while (self._recent_dumps and
               now - self._recent_dumps[0] > _DUMP_WINDOW_SECONDS):
            self._recent_dumps.popleft()
        if len(self._recent_dumps) >= _MAX_DUMPS_PER_MINUTE:
            if not self._suppressing:
                self._suppressing = True
                logger.warning(
                    f'Event loop stalled; more than {_MAX_DUMPS_PER_MINUTE} '
                    'stack dumps in the last minute, suppressing further '
                    'dumps. Stall counts are still recorded in '
                    'sky_apiserver_event_loop_stall_total.')
            return False
        self._suppressing = False
        self._last_dump_at[source] = now
        self._recent_dumps.append(now)
        return True

    def _log_stall(self, lag: float, innermost: _FrameInfo,
                   app_frames: List[_FrameInfo], source: str,
                   other_threads: Optional[List[Dict[str, object]]]) -> None:
        detail: Dict[str, object] = {
            'lag_s': round(lag, 3),
            'threshold_s': round(self._threshold, 3),
            'kind': 'waiting' if other_threads is not None else 'on_loop',
            'source': source,
            'blocking': _format_frame(app_frames[0]),
            'innermost': _format_frame(innermost),
            'task': _describe_current_task(self._loop),
            'frames': _collapse_frames(app_frames),
        }
        if other_threads is not None:
            detail['other_threads'] = other_threads
        logger.warning(
            f'Event loop stalled {lag:.3f}s (threshold {self._threshold:.3f}s) '
            f'in {source} {_as_json(detail)}')

    def _log_starved(self, lag: float, innermost: _FrameInfo,
                     all_frames: Mapping[int, types.FrameType],
                     loop_thread_id: int) -> None:
        detail: Dict[str, object] = {
            'lag_s': round(lag, 3),
            'threshold_s': round(self._threshold, 3),
            'kind': 'parked',
            'source': _STARVED_SOURCE,
            'innermost': _format_frame(innermost),
            'note': ('loop was parked in its own poll; the delay came from '
                     'outside the loop - GIL contention with another thread '
                     'in this process, or the process not being scheduled'),
        }
        other_threads = self._render_other_threads(all_frames, loop_thread_id)
        if other_threads is not None:
            detail['other_threads'] = other_threads
        logger.warning(
            f'Event loop stalled {lag:.3f}s (threshold {self._threshold:.3f}s) '
            f'but the loop was parked in its own poll {_as_json(detail)}')

    def _render_other_threads(
            self, all_frames: Mapping[int, types.FrameType],
            loop_thread_id: int) -> Optional[List[Dict[str, object]]]:
        """Renders the other threads' stacks, subject to its own rate limit.

        Much larger than a single stack, so it is throttled separately from the
        per-source dedup. Returns None when throttled or when there is nothing
        to report.
        """
        now = time.monotonic()
        if now - self._last_all_thread_dump_at < _ALL_THREAD_DUMP_INTERVAL:
            return None
        groups = self._group_other_threads(all_frames, loop_thread_id)
        if not groups:
            return None
        self._last_all_thread_dump_at = now
        return [{
            'count': count,
            'source': source,
            'example_thread': thread_name,
            'at': location,
        } for source, count, thread_name, location in groups]

    def _group_other_threads(
            self, all_frames: Mapping[int, types.FrameType],
            loop_thread_id: int) -> List[Tuple[str, int, str, str]]:
        """Buckets the other threads in this process by their innermost frame.

        Returns (source, thread count, one thread name, one location), for the
        largest buckets. Threads doing the same thing collapse into one line,
        which is what makes this readable on a server with dozens of worker
        threads.
        """
        thread_names = {t.ident: t.name for t in threading.enumerate()}
        watchdog_thread_id = threading.get_ident()
        buckets: Dict[str, Tuple[int, str, str]] = {}
        scanned = 0
        for thread_id, frame in all_frames.items():
            if thread_id in (loop_thread_id, watchdog_thread_id):
                continue
            scanned += 1
            if scanned > _MAX_THREADS_SCANNED:
                break
            stack = _walk_stack(frame)
            if not stack:
                continue
            app_frames = [f for f in stack if not _is_plumbing(f.module)]
            leaf = app_frames[0] if app_frames else stack[0]
            source = _source_of(leaf)
            existing = buckets.get(source)
            if existing is None:
                buckets[source] = (1, thread_names.get(thread_id, 'unknown'),
                                   _format_frame(leaf))
            else:
                buckets[source] = (existing[0] + 1, existing[1], existing[2])
        ranked = sorted(buckets.items(), key=lambda kv: -kv[1][0])
        return [(source, count, thread_name, location)
                for source, (count, thread_name,
                             location) in ranked[:_MAX_THREAD_GROUPS]]


def start_watchdog(
    heartbeat_interval: float = _HEARTBEAT_INTERVAL
) -> Optional[LoopStallWatchdog]:
    """Starts stall attribution for the running loop, if it is enabled.

    Returns None when disabled, so the caller has nothing to stop and nothing
    to feed. `heartbeat_interval` must match the cadence of the timer that
    calls `beat`, since it is what separates normal scheduling slack from lag.
    """
    threshold = perf_utils.get_loop_stall_threshold()
    if threshold is None:
        return None
    watchdog = LoopStallWatchdog(asyncio.get_running_loop(),
                                 threshold,
                                 heartbeat_interval=heartbeat_interval)
    watchdog.start()
    return watchdog
