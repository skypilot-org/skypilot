"""Request execution threads management."""

import asyncio
import concurrent.futures
import itertools
import os
import sys
import threading
import time
from typing import (Callable, Dict, FrozenSet, List, NamedTuple, Optional,
                    Tuple, TypeVar)
import urllib.parse
import weakref

import prometheus_client as prom

from sky import exceptions
from sky import sky_logging
from sky.metrics import utils as metrics_utils
from sky.server import hung_threads
from sky.utils import atomic
from sky.utils.db import db_utils

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

_P = ParamSpec('_P')
_T = TypeVar('_T')

logger = sky_logging.init_logger(__name__)

# How often the per-process watchdog refreshes the stuck/oldest-age metrics
# of every executor and looks for threads past their executor's threshold.
_WATCHDOG_INTERVAL_SECONDS = 10.0
# Thread dumps are written when the set of stuck threads changes (a new
# thread crossed the threshold) or when an exhausted executor's slots are
# held by a set not yet reported, never more often than the minimum interval
# per executor. While nothing changes, a heartbeat dump repeats the picture
# at intervals that double from the minimum up to the maximum, so a thread
# pinned for a day costs a handful of dumps rather than one per minute.
_DUMP_MIN_INTERVAL_SECONDS = 60.0
_DUMP_MAX_INTERVAL_SECONDS = 3600.0
# Full descriptions (stack + kernel view) are written for at most this many
# threads per dump, oldest first; the rest are counted. Keeps one dump to a
# few tens of KB even for an executor with hundreds of workers.
_MAX_DESCRIBED_THREADS = 8
# The ownerless-socket scan reads /proc/net/tcp and walks every readable
# /proc/<pid>/fd; it runs at most this often per process, and only as part
# of a dump. It is the only in-process evidence of a descriptor closed under
# a sleeping thread (see hung_threads.scan_ownerless_sockets).
_SCAN_INTERVAL_SECONDS = 300.0

_executors: 'weakref.WeakSet[OnDemandThreadExecutor]' = weakref.WeakSet()
_watchdog_lock = threading.Lock()
_watchdog_pid: Optional[int] = None
_scan_lock = threading.Lock()
_last_scan: float = float('-inf')
_db_peers: Optional[List[Tuple[str, int]]] = None


def _ensure_watchdog() -> None:
    """Start the per-process watchdog thread once (again after a fork)."""
    global _watchdog_pid
    pid = os.getpid()
    with _watchdog_lock:
        if _watchdog_pid == pid:
            return
        _watchdog_pid = pid
        threading.Thread(target=_watchdog_loop,
                         name='thread-executor-watchdog',
                         daemon=True).start()


def _watchdog_loop() -> None:
    while True:
        time.sleep(_WATCHDOG_INTERVAL_SECONDS)
        for executor in list(_executors):
            try:
                executor.observe()
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(f'Executor [{executor.name}] watchdog: {e!r}')


def _state_db_peers() -> List[Tuple[str, int]]:
    """(host, port) of the state database as this process reaches it: the
    pooler if one is configured, and the database itself. A connection to
    one of these that no process owns any more is exactly what a stray close
    under a sleeping database call leaves behind, and it can hold locks."""
    global _db_peers
    if _db_peers is None:
        peers: List[Tuple[str, int]] = []
        for direct in (False, True):
            try:
                # pylint: disable-next=protected-access
                conn_string = db_utils._resolve_conn_string(direct)
                if not conn_string:
                    continue
                parts = urllib.parse.urlsplit(conn_string)
                if parts.hostname:
                    peers.append((parts.hostname, parts.port or 5432))
            except Exception:  # pylint: disable=broad-except
                continue
        _db_peers = peers
    return _db_peers


def _maybe_scan_ownerless_sockets() -> List[str]:
    """Run the ownerless-socket scan if none ran recently in this process."""
    global _last_scan
    now = time.monotonic()
    with _scan_lock:
        if now - _last_scan < _SCAN_INTERVAL_SECONDS:
            return []
        _last_scan = now
    return hung_threads.scan_ownerless_sockets(_state_db_peers())


_task_seq = itertools.count(1)


class _Task(NamedTuple):
    started: float
    deadline: Optional[float]
    name: str
    # Process-wide unique. Thread identifiers (pthread_t) are reused as soon
    # as a thread exits, so they cannot tell "the same threads are still
    # stuck" from "different threads are stuck now".
    seq: int


class _RunningTask(NamedTuple):
    age: float
    thread: threading.Thread
    task: _Task

    @property
    def over_deadline(self) -> bool:
        return (self.task.deadline is not None and
                time.monotonic() > self.task.deadline)


class OnDemandThreadExecutor(concurrent.futures.Executor):
    """An executor that creates a new thread for each task and destroys it
    after the task is completed.

    Note(dev):
    We raise an error instead of queuing the request if the limit is reached, so
    that:
    1. the request might be handled by other processes that have idle workers
       upon retry;
    2. if not, then users can be clearly hinted that they need to scale the API
       server to support higher concurrency.
    So this executor is only suitable for carefully selected cases where the
    error can be properly handled by caller. To make this executor general, we
    need to support configuring the queuing behavior (exception or queueing).
    """

    def __init__(self,
                 name: str,
                 max_workers: int,
                 stuck_after_seconds: Optional[float] = None):
        """Create an executor.

        Args:
            name: Executor name, used in thread names, logs and metrics.
            max_workers: Concurrent tasks allowed; further submits raise.
            stuck_after_seconds: A task still running after this long is
                reported as stuck: counted in
                ``sky_apiserver_threads_stuck`` and described (Python stack
                plus the syscall it sleeps in) in the log. Set it for pools
                whose tasks have a known short bound, such as the auth
                lookups; leave None for pools where a task may legitimately
                run for hours (a log stream), in which case only the
                exhaustion dump reports the oldest tasks.
        """
        self.name: str = name
        self.max_workers: int = max_workers
        self.stuck_after_seconds: Optional[float] = stuck_after_seconds
        self.running: atomic.AtomicInt = atomic.AtomicInt(0)
        self._shutdown: bool = False
        self._shutdown_lock: threading.Lock = threading.Lock()
        # Running thread -> its task (start time, deadline, name). The start
        # time is what lets the watchdog and the exhaustion path tell a
        # thread that is merely busy from one that never came back.
        self._threads: Dict[threading.Thread, _Task] = {}
        self._threads_lock: threading.Lock = threading.Lock()
        # Dump bookkeeping, guarded by _dump_lock: when the last dump was
        # written, which stuck threads it covered, the current heartbeat
        # interval, and which set the last exhaustion dump covered.
        self._dump_lock: threading.Lock = threading.Lock()
        self._last_dump: float = float('-inf')
        self._dumped_stuck: FrozenSet[int] = frozenset()
        self._dumped_exhausted: FrozenSet[int] = frozenset()
        self._heartbeat_interval: float = _DUMP_MIN_INTERVAL_SECONDS
        # Cache the labeled metric children to avoid the label lookup on
        # every submit/complete.
        self._active_gauge: Optional[prom.Gauge] = None
        self._exhausted_counter: Optional[prom.Counter] = None
        self._stuck_gauge: Optional[prom.Gauge] = None
        self._oldest_age_gauge: Optional[prom.Gauge] = None
        if metrics_utils.METRICS_ENABLED:
            pid = os.getpid()
            self._active_gauge = (
                metrics_utils.SKY_APISERVER_THREADS_ACTIVE.labels(pid=pid,
                                                                  name=name))
            self._exhausted_counter = (
                metrics_utils.SKY_APISERVER_THREADS_EXHAUSTED_TOTAL.labels(
                    name=name))
            metrics_utils.SKY_APISERVER_THREADS_MAX.labels(
                pid=pid, name=name).set(max_workers)
            self._stuck_gauge = (
                metrics_utils.SKY_APISERVER_THREADS_STUCK.labels(pid=pid,
                                                                 name=name))
            self._oldest_age_gauge = (
                metrics_utils.SKY_APISERVER_THREADS_OLDEST_AGE_SECONDS.labels(
                    pid=pid, name=name))
        _executors.add(self)
        _ensure_watchdog()

    def _cleanup_thread(self, thread: threading.Thread):
        with self._threads_lock:
            self._threads.pop(thread, None)

    def _snapshot(self) -> List[_RunningTask]:
        """Every running task, oldest first.

        Threads that are no longer alive are dropped: after a fork the child
        inherits the parent's table but none of its threads.
        """
        now = time.monotonic()
        items: List[_RunningTask] = []
        with self._threads_lock:
            dead = [t for t in self._threads if not t.is_alive()]
            for t in dead:
                del self._threads[t]
            for thread, task in self._threads.items():
                items.append(_RunningTask(now - task.started, thread, task))
        items.sort(key=lambda item: item.age, reverse=True)
        return items

    @staticmethod
    def _stuck(snapshot: List[_RunningTask]) -> List[_RunningTask]:
        return [item for item in snapshot if item.over_deadline]

    @staticmethod
    def _keys(items: List[_RunningTask]) -> FrozenSet[int]:
        return frozenset(item.task.seq for item in items)

    def observe(self) -> Tuple[int, float]:
        """Refresh the stuck/oldest-age metrics; dump stuck threads.

        Called by the per-process watchdog every _WATCHDOG_INTERVAL_SECONDS.
        Returns (stuck thread count, oldest running task age in seconds).
        """
        snapshot = self._snapshot()
        oldest = snapshot[0].age if snapshot else 0.0
        stuck = self._stuck(snapshot)
        if self._stuck_gauge is not None:
            self._stuck_gauge.set(len(stuck))
        if self._oldest_age_gauge is not None:
            self._oldest_age_gauge.set(oldest)
        if stuck:
            self._dump_if_due(
                snapshot,
                stuck,
                reason=(f'{len(stuck)} thread(s) running longer than '
                        f'{self.stuck_after_seconds:g}s'))
        else:
            with self._dump_lock:
                self._dumped_stuck = frozenset()
                self._heartbeat_interval = _DUMP_MIN_INTERVAL_SECONDS
        return len(stuck), oldest

    def _dump_if_due(self, snapshot: List[_RunningTask],
                     stuck: List[_RunningTask], reason: str) -> bool:
        """Watchdog path: dump when a thread newly crossed the threshold, or
        when the heartbeat interval elapsed with the same threads stuck."""
        now = time.monotonic()
        keys = self._keys(stuck)
        with self._dump_lock:
            since_last = now - self._last_dump
            if since_last < _DUMP_MIN_INTERVAL_SECONDS:
                return False
            if keys - self._dumped_stuck:
                self._heartbeat_interval = _DUMP_MIN_INTERVAL_SECONDS
            elif since_last < self._heartbeat_interval:
                return False
            else:
                self._heartbeat_interval = min(self._heartbeat_interval * 2,
                                               _DUMP_MAX_INTERVAL_SECONDS)
                reason += (f' (unchanged for {since_last:.0f}s; next report '
                           f'in {self._heartbeat_interval:.0f}s)')
            self._last_dump = now
            self._dumped_stuck = keys
        self._dump(snapshot, stuck, reason)
        return True

    def _dump(self, snapshot: List[_RunningTask], describe: List[_RunningTask],
              reason: str) -> None:
        """Log what the longest-running threads are doing."""
        to_describe = (describe or snapshot)[:_MAX_DESCRIBED_THREADS]
        parts = [
            f'Executor [{self.name}]: {reason}; {len(snapshot)} of '
            f'{self.max_workers} workers running, describing the '
            f'{len(to_describe)} longest-running (oldest first):'
        ]
        for item in to_describe:
            parts.append(
                hung_threads.describe(
                    item.thread,
                    item.age,
                    item.task.name,
                    deadline_seconds=self.stuck_after_seconds))
        parts.extend(_maybe_scan_ownerless_sockets())
        logger.warning('\n'.join(parts))

    def _dump_on_exhaustion(self) -> None:
        """Exhaustion path: describe the threads holding the slots.

        Runs in its own short-lived thread so the rejected caller (often the
        event loop) is not held for the /proc reads. A saturated executor
        rejects every submit, so this dumps only when the threads holding
        the slots (the stuck ones, or the oldest if the executor has no
        threshold) differ from the last exhaustion dump, and never more than
        once per _DUMP_MIN_INTERVAL_SECONDS. The watchdog heartbeat keeps
        repeating an unchanged picture at its backed-off interval.
        """
        if time.monotonic() - self._last_dump < _DUMP_MIN_INTERVAL_SECONDS:
            return

        def _dump():
            try:
                snapshot = self._snapshot()
                stuck = self._stuck(snapshot)
                holders = stuck or snapshot[:_MAX_DESCRIBED_THREADS]
                keys = self._keys(holders)
                now = time.monotonic()
                with self._dump_lock:
                    if (now - self._last_dump < _DUMP_MIN_INTERVAL_SECONDS or
                            keys == self._dumped_exhausted):
                        return
                    self._last_dump = now
                    self._dumped_exhausted = keys
                    self._dumped_stuck = self._keys(stuck)
                self._dump(snapshot, stuck, 'all workers busy, submit rejected')
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(f'Executor [{self.name}] dump failed: {e!r}')

        threading.Thread(target=_dump,
                         name=f'{self.name}-exhaustion-dump',
                         daemon=True).start()

    def _task_wrapper(self, fn: Callable, fut: concurrent.futures.Future, /,
                      *args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            fut.set_result(result)
        except asyncio.CancelledError:
            # Cancellation is an expected terminal state, not an error.
            # Put the future into CANCELLED state instead of FINISHED with a
            # CancelledError exception, so that the asyncio-side future (via
            # asyncio.wrap_future / loop.run_in_executor) is also cancelled
            # and does not trigger the "Future exception was never retrieved"
            # warning when the awaiter was itself cancelled and never consumes
            # the exception.
            logger.debug(f'Executor [{self.name}] cancelled {fn}')
            if not fut.cancelled():
                # fut.cancel() succeeds only when the future is in PENDING
                # state. OnDemandThreadExecutor does not call
                # set_running_or_notify_cancel(), so the future stays PENDING
                # here. If a future refactor changes that, fall back to
                # set_exception to preserve the cancellation signal.
                # Also guard with fut.done() in case of a race where the future
                # transitioned to a terminal state between our cancelled() check
                # and cancel() call — set_exception on a done future raises
                # InvalidStateError.
                if not fut.cancel() and not fut.done():
                    fut.set_exception(asyncio.CancelledError())
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Executor [{self.name}] error executing {fn}: {e}')
            if not fut.cancelled():
                # Only set the exception if the future is not cancelled to avoid
                # setting the exception twice leading to another exception.
                fut.set_exception(e)
        finally:
            self.running.decrement()
            if self._active_gauge is not None:
                self._active_gauge.dec()
            self._cleanup_thread(threading.current_thread())

    def check_available(self, borrow: bool = False) -> int:
        """Check if there are available workers.

        Args:
            borrow: If True, the caller borrow a worker from the executor.
                The caller is responsible for returning the worker to the
                executor after the task is completed.
        """
        count = self.running.increment()
        if count > self.max_workers:
            self.running.decrement()
            if self._exhausted_counter is not None:
                self._exhausted_counter.inc()
            self._dump_on_exhaustion()
            raise exceptions.ConcurrentWorkerExhaustedError(
                f'Maximum concurrent workers {self.max_workers} of threads '
                f'executor [{self.name}] reached')
        if not borrow:
            self.running.decrement()
        return count

    def submit(self, fn: Callable[_P, _T], /, *args: _P.args,
               **kwargs: _P.kwargs) -> 'concurrent.futures.Future[_T]':
        with self._shutdown_lock:
            if self._shutdown:
                raise RuntimeError(
                    'Cannot submit task after executor is shutdown')
            count = self.check_available(borrow=True)
            if self._active_gauge is not None:
                self._active_gauge.inc()
            fut: concurrent.futures.Future = concurrent.futures.Future()
            # Name is assigned for debugging purpose, duplication is fine
            thread = threading.Thread(target=self._task_wrapper,
                                      name=f'{self.name}-{count}',
                                      args=(fn, fut, *args),
                                      kwargs=kwargs,
                                      daemon=True)
            started = time.monotonic()
            deadline = (None if self.stuck_after_seconds is None else started +
                        self.stuck_after_seconds)
            with self._threads_lock:
                self._threads[thread] = _Task(started, deadline,
                                              _describe_task(fn),
                                              next(_task_seq))
            try:
                thread.start()
            except Exception as e:
                self.running.decrement()
                if self._active_gauge is not None:
                    self._active_gauge.dec()
                self._cleanup_thread(thread)
                fut.set_exception(e)
                raise
            assert thread.ident is not None, 'Thread should be started'
            return fut

    def shutdown(self,
                 wait: bool = True,
                 *,
                 cancel_futures: bool = False) -> None:
        with self._shutdown_lock:
            self._shutdown = True
        if not wait:
            return
        with self._threads_lock:
            threads = list(self._threads)
        for t in threads:
            t.join()


def _describe_task(fn: Callable) -> str:
    """Short name of a submitted callable for the dumps.

    ``to_thread_with_executor`` wraps everything in
    ``functools.partial(contextvars.Context.run, func, ...)``; unwrap that so
    the dump names the function that was actually submitted.
    """
    seen = 0
    while seen < 4:
        seen += 1
        args = getattr(fn, 'args', None)
        inner = getattr(fn, 'func', None)
        if inner is None:
            break
        if getattr(inner, '__name__', '') == 'run' and args:
            fn = args[0]
            continue
        fn = inner
    module = getattr(fn, '__module__', None) or ''
    name = getattr(fn, '__qualname__', None) or getattr(fn, '__name__',
                                                        None) or repr(fn)
    return f'{module}.{name}' if module else name
