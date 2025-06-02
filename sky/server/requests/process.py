"""ProcessPoolExecutor with additional supports for skypilot."""
import concurrent.futures
import logging
import multiprocessing
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from sky.utils import atomic
from sky.utils import subprocess_utils

logger = logging.getLogger(__name__)


class PoolExecutor(concurrent.futures.ProcessPoolExecutor):
    """A custom ProcessPoolExecutor with additional supports for skypilot.

    The additional supports include:
    1. Disposable workers: support control whether the worker process should
       exit after complete a task.
    2. Idle check: support check if there are any idle workers.
    3. Proactive shutdown: SIGTERM worker processes when the executor is
       shutting down instead of indefinitely waiting.
    """

    def __init__(self, max_workers: int, **kwargs):
        super().__init__(max_workers=max_workers, **kwargs)
        self.max_workers: int = max_workers
        # The number of workers that are handling tasks, atomicity across
        # multiple threads is sufficient since the idleness check is
        # best-effort and does not affect the correctness.
        # E.g. the following case is totally fine:
        # 1. Thread 1 checks running == max_workers
        # 2. Thread 2 decrements running
        # 3. Thread 1 schedules the task to other pool even if the pool is
        #    currently idle.
        self.running: atomic.AtomicInt = atomic.AtomicInt(0)

    def submit(self, fn, *args, **kwargs) -> concurrent.futures.Future:
        """Submit a task for execution.

        If reuse_worker is False, wraps the function to exit after completion.
        """
        self.running.increment()
        future = super().submit(fn, *args, **kwargs)
        future.add_done_callback(lambda _: self.running.decrement())
        return future

    def has_idle_workers(self) -> bool:
        """Check if there are any idle workers."""
        return self.running.get() < self.max_workers

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the executor."""
        # Here wait means wait for the proactive cancellation complete.
        # TODO(aylei): we may support wait=True in the future if needed.
        assert wait is True, 'wait=False is not supported'
        executor_processes = list(self._processes.values())
        # Shutdown the executor so that executor process can exit once the
        # running task is finished or interrupted.
        super().shutdown(wait=False)
        # Proactively interrupt the running task to avoid indefinite waiting.
        subprocess_utils.run_in_parallel(
            subprocess_utils.kill_process_with_grace_period,
            executor_processes,
            num_threads=len(executor_processes))


# Define the worker function outside of the class to avoid pickling self
def _disposable_worker(fn, initializer: Optional[Callable], initargs: Tuple,
                       args, kwargs):
    try:
        if initializer is not None:
            initializer(*initargs)
        fn(*args, **kwargs)
    except BaseException as e:  # pylint: disable=broad-except
        return e


class DisposableExecutor:
    """A simple wrapper that creates a new process for each task.

    This is a workaround for Python 3.10 since `max_tasks_per_child` of
    ProcessPoolExecutor was introduced in 3.11. There is no way to control
    the worker lifetime in 3.10.
    Ref: https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.ProcessPoolExecutor # pylint: disable=line-too-long
    TODO(aylei): use the official `max_tasks_per_child` when upgrade to 3.11
    """

    def __init__(self,
                 max_workers: Optional[int] = None,
                 initializer: Optional[Callable] = None,
                 initargs: Tuple = ()):
        self.max_workers: Optional[int] = max_workers
        self.workers: Dict[int, multiprocessing.Process] = {}
        self._shutdown: bool = False
        self._lock: threading.Lock = threading.Lock()
        self._initializer: Optional[Callable] = initializer
        self._initargs: Tuple = initargs

    def _monitor_worker(self, process: multiprocessing.Process) -> None:
        """Monitor the worker process and cleanup when it's done."""
        process.join()
        if process.pid:
            with self._lock:
                if process.pid in self.workers:
                    del self.workers[process.pid]

    # Submit is not compatible with ProcessPoolExecutor because we does not
    # bother to return a Future. Can be improved if needed.
    def submit(self, fn, *args, **kwargs) -> bool:
        """Submit a task for execution."""
        if self._shutdown:
            return False
        with self._lock:
            if (self.max_workers is not None and
                    len(self.workers) >= self.max_workers):
                return False

        process = multiprocessing.Process(target=_disposable_worker,
                                          args=(fn, self._initializer,
                                                self._initargs, args, kwargs))
        process.start()

        with self._lock:
            pid = process.pid or 0
            if pid == 0:
                raise RuntimeError('Failed to start process')
            self.workers[pid] = process

        # Start monitor thread to cleanup the worker process when it's done.
        monitor_thread = threading.Thread(target=self._monitor_worker,
                                          args=(process,),
                                          daemon=True)
        monitor_thread.start()

        return True

    def has_idle_workers(self) -> bool:
        """Check if there are any idle workers."""
        if self.max_workers is None:
            return True
        with self._lock:
            return len(self.workers) < self.max_workers

    def shutdown(self):
        """Shutdown the executor."""
        with self._lock:
            self._shutdown = True
        subprocess_utils.run_in_parallel(
            subprocess_utils.kill_process_with_grace_period,
            list(self.workers.values()),  # Convert dict values to list
            num_threads=len(self.workers))


class BurstableExecutor:
    """An multiprocessing executor that supports bursting worker processes."""

    # _executor is a PoolExecutor that is used to run guaranteed requests.
    _executor: Optional[PoolExecutor] = None
    # _burst_executor is a ProcessPoolExecutor that is used to run burst
    # requests.
    _burst_executor: Optional[DisposableExecutor] = None

    def __init__(self,
                 garanteed_workers: int,
                 burst_workers: int = 0,
                 **kwargs):
        if garanteed_workers > 0:
            self._executor = PoolExecutor(max_workers=garanteed_workers,
                                          **kwargs)
        if burst_workers > 0:
            self._burst_executor = DisposableExecutor(max_workers=burst_workers,
                                                      **kwargs)

    def submit_until_success(self, fn, *args, **kwargs):
        """Submit a task for execution until success.

        Prioritizes submitting to the guaranteed pool. If no idle workers
        are available in the guaranteed pool, it will submit to the burst
        pool.
        TODO(aylei): this is coupled with executor.RequestWorker since we
        know the worker is dedicated to request scheduling and it either
        blocks on request polling or request submitting. So it is no harm
        to make submit blocking here. But for general cases, we need an
        internal queue to decouple submit and run.
        """

        while True:
            if self._executor is not None and self._executor.has_idle_workers():
                self._executor.submit(fn, *args, **kwargs)
                break
            if (self._burst_executor is not None and
                    self._burst_executor.has_idle_workers()):
                self._burst_executor.submit(fn, *args, **kwargs)
                break
            if self._executor is not None:
                # No idle workers in either pool, still queue the request
                # to the guaranteed pool to keep behavior consistent.
                self._executor.submit(fn, *args, **kwargs)
                break
            logger.debug('No guaranteed pool set and the burst pool is full, '
                         'retry later.')
            time.sleep(0.1)

    def shutdown(self) -> None:
        """Shutdown the executor."""

        if self._burst_executor is not None:
            self._burst_executor.shutdown()
        if self._executor is not None:
            self._executor.shutdown(wait=True)
