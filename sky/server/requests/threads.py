"""Request execution threads management."""

import concurrent.futures
import sys
import threading
from typing import Callable, Set, TypeVar

from sky import exceptions
from sky import sky_logging
from sky.utils import atomic

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

_P = ParamSpec('_P')
_T = TypeVar('_T')

logger = sky_logging.init_logger(__name__)


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

    def __init__(self, name: str, max_workers: int):
        self.name: str = name
        self.max_workers: int = max_workers
        self.running: atomic.AtomicInt = atomic.AtomicInt(0)
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
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Executor [{self.name}] error executing {fn}: {e}')
            fut.set_exception(e)
        finally:
            self.running.decrement()
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
                self.running.decrement()
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
