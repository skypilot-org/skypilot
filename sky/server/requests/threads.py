"""Request execution threads management."""

import concurrent.futures
import threading
from typing import Callable, List

from sky import exceptions
from sky import sky_logging
from sky.utils import atomic

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
        self._threads: List[threading.Thread] = []

    def _task_wrapper(self, fn: Callable, fut: concurrent.futures.Future, /,
                      *args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            fut.set_result(result)
        except Exception as e:  # pylint: disable=broad-except
            fut.set_exception(e)
        finally:
            self.running.decrement()

    def submit(self, fn, /, *args, **kwargs):
        with self._shutdown_lock:
            if self._shutdown:
                raise RuntimeError(
                    'Cannot submit task after executor is shutdown')
            count = self.running.increment()
            logger.info('Submitting task to thread executor',
                        extra={
                            'executor': self.name,
                            'count': count,
                            'max_workers': self.max_workers,
                        })
            if count > self.max_workers:
                self.running.decrement()
                raise exceptions.ConcurrentWorkerExhaustedError(
                    f'Maximum concurrent workers {self.max_workers} of threads '
                    f'executor [{self.name}] reached')
            fut: concurrent.futures.Future = concurrent.futures.Future()
            logger.info('Running task in thread executor',
                        extra={
                            'executor': self.name,
                            'count': count,
                            'max_workers': self.max_workers,
                        })
            thread = threading.Thread(target=self._task_wrapper,
                                      args=(fn, fut, args, kwargs),
                                      daemon=True)
            # Protected by GIL
            self._threads.append(thread)
            thread.start()

    def shutdown(self, wait=True):
        with self._shutdown_lock:
            self._shutdown = True
        if wait:
            for t in self._threads:
                t.join()
