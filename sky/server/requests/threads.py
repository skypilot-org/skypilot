"""Request execution threads management."""

import asyncio
import concurrent.futures
import functools
import os
import queue
import sys
import threading
from typing import Any, Callable, Dict, Optional, Set, Tuple, TypeVar

import prometheus_client as prom

from sky import exceptions
from sky import sky_logging
from sky.metrics import utils as metrics_utils
from sky.utils import atomic

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

_P = ParamSpec('_P')
_T = TypeVar('_T')

logger = sky_logging.init_logger(__name__)

_thread_start_counter_installed = False

# AnyIO prunes worker threads idle for 10 s by default; its thread limiter
# (40 by default) still bounds how many idle threads stay.
_ANYIO_WORKER_MAX_IDLE_SECONDS = 3600


def count_thread_starts() -> None:
    """Counts the threads started in this process, split by whether they
    were started on a running event loop. No-op when metrics are off."""
    global _thread_start_counter_installed
    if _thread_start_counter_installed or not metrics_utils.METRICS_ENABLED:
        return
    _thread_start_counter_installed = True
    on_loop = metrics_utils.SKY_APISERVER_THREADS_STARTED_TOTAL.labels(
        on_loop='true')
    off_loop = metrics_utils.SKY_APISERVER_THREADS_STARTED_TOTAL.labels(
        on_loop='false')
    original_start = threading.Thread.start

    @functools.wraps(original_start)
    def start(self: threading.Thread) -> None:
        try:
            asyncio.get_running_loop()
            counter = on_loop
        except RuntimeError:
            counter = off_loop
        original_start(self)
        counter.inc()

    threading.Thread.start = start  # type: ignore[method-assign]


def keep_anyio_worker_threads() -> None:
    """Keeps idle AnyIO worker threads, which run sync routes and
    anyio.to_thread calls, alive between bursts."""
    # Imported here: WorkerThread lives in a private AnyIO module that may move.
    try:
        # pylint: disable=import-outside-toplevel
        from anyio._backends import _asyncio as anyio_asyncio
    except ImportError:
        return
    worker_thread = getattr(anyio_asyncio, 'WorkerThread', None)
    if worker_thread is not None and hasattr(worker_thread, 'MAX_IDLE_TIME'):
        worker_thread.MAX_IDLE_TIME = _ANYIO_WORKER_MAX_IDLE_SECONDS


_WorkItem = Tuple[Callable, concurrent.futures.Future, Tuple[Any, ...],
                  Dict[str, Any]]


class OnDemandThreadExecutor(concurrent.futures.Executor):
    """An executor that runs each task on a reusable thread.

    Threads are started on demand up to max_workers and stay alive after
    their task completes, so a warm executor hands a task over without
    starting a thread. Call prestart() off the event loop to warm it up.
    Threads are daemons so that long-running tasks (e.g. log streaming) do
    not block interpreter exit.

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

    def __init__(self, name: str, max_workers: int):
        self.name: str = name
        self.max_workers: int = max_workers
        self.running: atomic.AtomicInt = atomic.AtomicInt(0)
        self._shutdown: bool = False
        self._shutdown_lock: threading.Lock = threading.Lock()
        self._threads: Set[threading.Thread] = set()
        self._threads_lock: threading.Lock = threading.Lock()
        self._work_queue: 'queue.SimpleQueue[Optional[_WorkItem]]' = (
            queue.SimpleQueue())
        # One token per idle thread.
        self._idle: threading.Semaphore = threading.Semaphore(0)
        # Cache the labeled metric children to avoid the label lookup on
        # every submit/complete.
        self._active_gauge: Optional[prom.Gauge] = None
        self._exhausted_counter: Optional[prom.Counter] = None
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

    def _worker(self, idle_at_start: bool) -> None:
        if idle_at_start:
            self._idle.release()
        while True:
            work = self._work_queue.get()
            if work is None:
                return
            self._run_task(*work)
            # Do not keep the last task's arguments alive while idle.
            del work

    def _start_thread(self, idle_at_start: bool) -> None:
        """Starts a worker thread; the caller must hold self._threads_lock."""
        thread = threading.Thread(target=self._worker,
                                  name=f'{self.name}-{len(self._threads) + 1}',
                                  args=(idle_at_start,),
                                  daemon=True)
        thread.start()
        self._threads.add(thread)

    def prestart(self, count: int) -> None:
        """Starts idle threads until the executor has `count` threads.

        Thread start waits for the GIL, so call this off the event loop.
        """
        while True:
            with self._threads_lock:
                if (self._shutdown or
                        len(self._threads) >= min(count, self.max_workers)):
                    return
                self._start_thread(idle_at_start=True)

    def _run_task(self, fn: Callable, fut: concurrent.futures.Future,
                  args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> None:
        result: Any = None
        error: Optional[BaseException] = None
        cancelled = False
        try:
            result = fn(*args, **kwargs)
        except asyncio.CancelledError:
            logger.debug(f'Executor [{self.name}] cancelled {fn}')
            cancelled = True
        except BaseException as e:  # pylint: disable=broad-except
            logger.debug(f'Executor [{self.name}] error executing {fn}: {e}')
            error = e
        finally:
            # Free the slot before resolving the future, so that a caller
            # that submits again right after the result sees a free thread.
            self.running.decrement()
            if self._active_gauge is not None:
                self._active_gauge.dec()
            self._idle.release()
        try:
            if cancelled:
                # Cancellation is an expected terminal state, not an error.
                # Put the future into CANCELLED state instead of FINISHED with
                # a CancelledError exception, so that the asyncio-side future
                # (via asyncio.wrap_future / loop.run_in_executor) is also
                # cancelled and does not trigger the "Future exception was
                # never retrieved" warning when the awaiter was itself
                # cancelled and never consumes the exception.
                # fut.cancel() succeeds only when the future is in PENDING
                # state. OnDemandThreadExecutor does not call
                # set_running_or_notify_cancel(), so the future stays PENDING
                # here. If a future refactor changes that, fall back to
                # set_exception to preserve the cancellation signal.
                if not fut.cancel() and not fut.done():
                    fut.set_exception(asyncio.CancelledError())
            elif error is not None:
                fut.set_exception(error)
            else:
                fut.set_result(result)
        except concurrent.futures.InvalidStateError:
            # The caller cancelled the future while the task ran.
            pass

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
            self.check_available(borrow=True)
            if self._active_gauge is not None:
                self._active_gauge.inc()
            fut: concurrent.futures.Future = concurrent.futures.Future()
            if not self._idle.acquire(blocking=False):
                try:
                    with self._threads_lock:
                        if len(self._threads) < self.max_workers:
                            self._start_thread(idle_at_start=False)
                except Exception as e:
                    self.running.decrement()
                    if self._active_gauge is not None:
                        self._active_gauge.dec()
                    fut.set_exception(e)
                    raise
            self._work_queue.put((fn, fut, args, kwargs))
            return fut

    def shutdown(self,
                 wait: bool = True,
                 *,
                 cancel_futures: bool = False) -> None:
        with self._shutdown_lock:
            self._shutdown = True
        with self._threads_lock:
            threads = list(self._threads)
        # Each thread exits after running the tasks queued before its sentinel.
        for _ in threads:
            self._work_queue.put(None)
        if not wait:
            return
        for t in threads:
            t.join()
