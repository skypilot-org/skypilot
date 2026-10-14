"""Request execution threads management."""

import asyncio
import concurrent.futures
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

_WorkItem = Tuple[Callable[..., Any], Tuple[Any, ...], Dict[str, Any],
                  concurrent.futures.Future]


class _BoundedAdmission:
    """Admits at most `max_workers` in-flight tasks and rejects the rest with
    `ConcurrentWorkerExhaustedError` instead of queueing them."""

    def _init_admission(self, name: str, max_workers: int) -> None:
        self.name: str = name
        self.max_workers: int = max_workers
        self.running: atomic.AtomicInt = atomic.AtomicInt(0)
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

    def _admit(self) -> int:
        count = self.check_available(borrow=True)
        if self._active_gauge is not None:
            self._active_gauge.inc()
        return count

    def _release(self, _: Optional[concurrent.futures.Future] = None) -> None:
        self.running.decrement()
        if self._active_gauge is not None:
            self._active_gauge.dec()


class OnDemandThreadExecutor(_BoundedAdmission, concurrent.futures.Executor):
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

    def __init__(self, name: str, max_workers: int):
        self._init_admission(name, max_workers)
        self._shutdown: bool = False
        self._shutdown_lock: threading.Lock = threading.Lock()
        self._threads: Set[threading.Thread] = set()
        self._threads_lock: threading.Lock = threading.Lock()

    def _cleanup_thread(self, thread: threading.Thread):
        with self._threads_lock:
            self._threads.discard(thread)

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
            self._release()
            self._cleanup_thread(threading.current_thread())

    def submit(self, fn: Callable[_P, _T], /, *args: _P.args,
               **kwargs: _P.kwargs) -> 'concurrent.futures.Future[_T]':
        with self._shutdown_lock:
            if self._shutdown:
                raise RuntimeError(
                    'Cannot submit task after executor is shutdown')
            count = self._admit()
            fut: concurrent.futures.Future = concurrent.futures.Future()
            # Name is assigned for debugging purpose, duplication is fine
            thread = threading.Thread(target=self._task_wrapper,
                                      name=f'{self.name}-{count}',
                                      args=(fn, fut, *args),
                                      kwargs=kwargs,
                                      daemon=True)
            with self._threads_lock:
                self._threads.add(thread)
            try:
                thread.start()
            except Exception as e:
                self._release()
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


class PooledThreadExecutor(_BoundedAdmission, concurrent.futures.Executor):
    """Like `OnDemandThreadExecutor`, but keeps finished threads for reuse.

    For short tasks on the request hot path, where starting a thread per task
    costs more than the task itself. Threads are daemons, so one stuck in a
    call never holds up interpreter exit.
    """

    def __init__(self, name: str, max_workers: int):
        self._init_admission(name, max_workers)
        self._work: 'queue.SimpleQueue[Optional[_WorkItem]]' = (
            queue.SimpleQueue())
        self._lock = threading.Lock()
        self._idle = 0
        self._num_threads = 0
        self._shutdown = False

    def submit(self, fn: Callable[_P, _T], /, *args: _P.args,
               **kwargs: _P.kwargs) -> 'concurrent.futures.Future[_T]':
        with self._lock:
            if self._shutdown:
                raise RuntimeError(
                    'Cannot submit task after executor is shutdown')
            self._admit()
            if self._idle > 0:
                self._idle -= 1
            else:
                thread = threading.Thread(
                    target=self._worker,
                    name=f'{self.name}-{self._num_threads + 1}',
                    daemon=True)
                try:
                    thread.start()
                except Exception:
                    self._release()
                    raise
                self._num_threads += 1
            fut: concurrent.futures.Future = concurrent.futures.Future()
            self._work.put((fn, args, kwargs, fut))
        return fut

    def _worker(self) -> None:
        while True:
            item = self._work.get()
            if item is None:
                return
            fn, args, kwargs, fut = item
            if fut.set_running_or_notify_cancel():
                try:
                    fut.set_result(fn(*args, **kwargs))
                except BaseException as e:  # pylint: disable=broad-except
                    fut.set_exception(e)
            # Counted idle before the slot is freed, so a submit admitted by
            # this slot reuses this thread instead of starting one.
            with self._lock:
                self._idle += 1
            self._release()

    def shutdown(self,
                 wait: bool = True,
                 *,
                 cancel_futures: bool = False) -> None:
        del wait, cancel_futures  # Daemon threads; nothing to join.
        with self._lock:
            self._shutdown = True
            for _ in range(self._num_threads):
                self._work.put(None)
