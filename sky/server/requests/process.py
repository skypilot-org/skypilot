"""ProcessPoolExecutor with additional supports for skypilot."""
import concurrent.futures
import concurrent.futures.process
import logging
import multiprocessing
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from sky import exceptions
from sky.utils import atomic
from sky.utils import subprocess_utils

logger = logging.getLogger(__name__)

# Warm start: how many workers may be starting (spawned but not yet done with
# their initializer) at the same time. Each one imports SkyPilot, so starting
# all of them at once would compete for the CPU the server needs.
_WARM_START_CONCURRENCY = 2
# How often the warm-start thread checks whether it can start another worker.
_WARM_START_POLL_SECONDS = 0.05
# Stop warm start if no starting worker becomes ready for this long, e.g. when
# the initializer fails. Workers then start on demand, as without warm start.
_WARM_START_STALL_TIMEOUT_SECONDS = 300


def _initializer_with_ready_signal(ready_count, initializer: Optional[Callable],
                                   initargs: Tuple) -> None:
    """Runs the pool's initializer, then counts this worker as ready."""
    if initializer is not None:
        initializer(*initargs)
    with ready_count.get_lock():
        ready_count.value += 1


class PoolExecutor(concurrent.futures.ProcessPoolExecutor):
    """A custom ProcessPoolExecutor with additional supports for skypilot.

    The additional supports include:
    1. Disposable workers: support control whether the worker process should
       exit after complete a task.
    2. Idle check: support check if there are any idle workers.
    3. Proactive shutdown: SIGTERM worker processes when the executor is
       shutting down instead of indefinitely waiting.
    4. Warm start (opt-in): start every worker up front, a few at a time,
       instead of on the first submits that find no idle worker.
    """

    def __init__(self, max_workers: int, warm_start: bool = False, **kwargs):
        self._warm_start = warm_start
        if warm_start:
            # Warm start spawns workers from a background thread, which is
            # only safe without fork (cpython gh-90622); with fork, Python
            # 3.11+ would also start every worker at once. The API server
            # already uses spawn (see executor.py).
            if kwargs.get('mp_context') is None:
                kwargs['mp_context'] = multiprocessing.get_context('spawn')
            if kwargs['mp_context'].get_start_method() == 'fork':
                raise ValueError('warm_start requires a non-fork mp_context.')
            # Each worker signals readiness once its initializer has run, so
            # warm start can limit how many are starting at the same time.
            self._ready_count = kwargs['mp_context'].Value('i', 0)
            kwargs['initargs'] = (self._ready_count, kwargs.get('initializer'),
                                  kwargs.get('initargs', ()))
            kwargs['initializer'] = _initializer_with_ready_signal
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
        # Set by shutdown, under _shutdown_lock, to stop warm start.
        self._warm_start_stopped = False
        self._warm_start_thread: Optional[threading.Thread] = None
        if warm_start:
            self._warm_start_thread = threading.Thread(target=self._warm_up,
                                                       name='pool-warm-start',
                                                       daemon=True)
            self._warm_start_thread.start()

    # Warm start drives ProcessPoolExecutor's private state (_shutdown_lock,
    # _processes, _spawn_process, ...), which differs across Python versions;
    # `_pool` gives untyped access to it in one place.
    @property
    def _pool(self) -> Any:
        return self

    def _start_worker(self) -> None:
        """Starts one worker process. Call with _shutdown_lock held."""
        # pylint: disable=protected-access
        pool = self._pool
        if hasattr(concurrent.futures.ProcessPoolExecutor, '_spawn_process'):
            # Python 3.11+.
            pool._spawn_process()
            return
        # Python 3.9 and 3.10 start workers inline in _adjust_process_count;
        # this is the same code, without its idle-worker check.
        worker_fn: Any = getattr(concurrent.futures.process, '_process_worker')
        proc = pool._mp_context.Process(
            target=worker_fn,
            args=(pool._call_queue, pool._result_queue, pool._initializer,
                  pool._initargs))
        proc.start()
        pool._processes[proc.pid] = proc
        # pylint: enable=protected-access

    def starting_workers(self) -> int:
        """Workers started that have not finished their initializer yet.

        Counts every live worker, including ones started on demand by submit,
        so warm start never adds to a burst of on-demand starts.
        """
        if not self._warm_start:
            return 0
        processes = self._pool._processes  # pylint: disable=protected-access
        return max(0, len(processes or {}) - self._ready_count.value)

    def _warm_up(self) -> None:
        """Starts every worker, at most _WARM_START_CONCURRENCY at a time.

        Workers that are ready take requests while the rest are starting, so
        the pool serves requests during warm start. Submits during warm start
        may also spawn workers, up to max_workers in total, as they do today.
        """
        # pylint: disable=protected-access
        pool = self._pool
        started_at = time.monotonic()
        stalled_since: Optional[float] = None
        while True:
            with pool._shutdown_lock:
                if (self._warm_start_stopped or pool._shutdown_thread or
                        pool._broken):
                    return
                if len(pool._processes) >= self.max_workers:
                    break
                if self.starting_workers() < _WARM_START_CONCURRENCY:
                    pool._start_executor_manager_thread()
                    self._start_worker()
                    # Let the manager thread watch the new worker right away.
                    pool._executor_manager_thread_wakeup.wakeup()
                    stalled_since = None
                elif stalled_since is None:
                    stalled_since = time.monotonic()
                elif (time.monotonic() - stalled_since >
                      _WARM_START_STALL_TIMEOUT_SECONDS):
                    logger.warning(
                        'Warm start stopped: no worker became ready in '
                        f'{_WARM_START_STALL_TIMEOUT_SECONDS}s. Remaining '
                        'workers will start on demand.')
                    return
            time.sleep(_WARM_START_POLL_SECONDS)
        # pylint: enable=protected-access
        logger.info(f'Warm start: {self.max_workers} workers started in '
                    f'{time.monotonic() - started_at:.1f}s.')

    def wait_for_warm_start(self, timeout: Optional[float] = None) -> bool:
        """Waits for warm start to finish starting workers.

        Returns True when warm start is done or was not enabled, False on
        timeout.
        """
        if self._warm_start_thread is None:
            return True
        self._warm_start_thread.join(timeout)
        return not self._warm_start_thread.is_alive()

    def submit(self, fn, /, *args, **kwargs) -> concurrent.futures.Future:
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

    def shutdown(self,
                 wait: bool = True,
                 *,
                 cancel_futures: bool = False) -> None:
        """Shutdown the executor."""
        # Here wait means wait for the proactive cancellation complete.
        # TODO(aylei): we may support wait=True in the future if needed.
        assert wait is True, 'wait=False is not supported'
        # Snapshot the workers and stop warm start under the lock warm start
        # spawns under, so no worker can start after the snapshot and be left
        # out of the shutdown below.
        with self._pool._shutdown_lock:  # pylint: disable=protected-access
            self._warm_start_stopped = True
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
def _disposable_worker(fn, initializer, initargs, result_queue, args, kwargs):
    """The worker function that is used to run the task.

    Args:
        fn: The function to run.
        initializer: The initializer function to run before running the task.
        initargs: The arguments to pass to the initializer function.
        result_queue: The queue to put the result and exception into.
        args: The arguments to pass to the function.
        kwargs: The keyword arguments to pass to the function.
    """
    try:
        if initializer is not None:
            initializer(*initargs)
        result = fn(*args, **kwargs)
        result_queue.put(result)
    except BaseException as e:  # pylint: disable=broad-except
        result_queue.put(e)


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

    def _monitor_worker(self, process: multiprocessing.Process,
                        future: concurrent.futures.Future,
                        result_queue: multiprocessing.Queue) -> None:
        """Monitor the worker process and cleanup when it's done."""
        try:
            process.join()
            if not future.cancelled():
                try:
                    # Get result from the queue if process completed
                    if not result_queue.empty():
                        result = result_queue.get(block=False)
                        if isinstance(result, BaseException):
                            future.set_exception(result)
                        else:
                            future.set_result(result)
                    else:
                        # The worker puts a result or an exception unless it
                        # was killed.
                        future.set_exception(
                            concurrent.futures.process.BrokenProcessPool(
                                f'Worker process {process.pid} exited with '
                                f'code {process.exitcode} without a result.'))
                except (multiprocessing.TimeoutError, BrokenPipeError,
                        EOFError) as e:
                    future.set_exception(e)
        finally:
            if process.pid:
                with self._lock:
                    if process.pid in self.workers:
                        del self.workers[process.pid]

    def submit(self, fn, *args, **kwargs) -> concurrent.futures.Future:
        """Submit a task for execution and return a Future."""
        future: concurrent.futures.Future = concurrent.futures.Future()

        if self._shutdown:
            raise RuntimeError('Cannot submit task after executor is shutdown')

        with self._lock:
            if (self.max_workers is not None and
                    len(self.workers) >= self.max_workers):
                raise exceptions.ExecutionPoolFullError(
                    'Maximum workers reached')

        result_queue: multiprocessing.Queue = multiprocessing.Queue()
        process = multiprocessing.Process(target=_disposable_worker,
                                          args=(fn, self._initializer,
                                                self._initargs, result_queue,
                                                args, kwargs))
        process.daemon = True
        process.start()

        with self._lock:
            pid = process.pid or 0
            if pid == 0:
                raise RuntimeError('Failed to start process')
            self.workers[pid] = process

        # Start monitor thread to cleanup the worker process when it's done
        monitor_thread = threading.Thread(target=self._monitor_worker,
                                          args=(process, future, result_queue),
                                          daemon=True)
        monitor_thread.start()

        return future

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
                 warm_start: bool = False,
                 **kwargs):
        if garanteed_workers > 0:
            self._guaranteed_workers = garanteed_workers
            # Warm start applies to the guaranteed pool, including a pool
            # rebuilt after it broke; burst workers stay one-off.
            self._guaranteed_pool_kwargs = dict(kwargs, warm_start=warm_start)
            self._guaranteed_pool_lock = threading.Lock()
            self._executor = PoolExecutor(max_workers=garanteed_workers,
                                          **self._guaranteed_pool_kwargs)
        if burst_workers > 0:
            self._burst_executor = DisposableExecutor(max_workers=burst_workers,
                                                      **kwargs)

    def submit_until_success(self, fn, *args,
                             **kwargs) -> concurrent.futures.Future:
        """Submit a task for execution until success.

        Prioritizes submitting to the guaranteed pool. If no idle workers
        are available in the guaranteed pool, it will submit to the burst
        pool. If the burst pool is full, it will retry the whole process until
        the task is submitted successfully.
        TODO(aylei): this is coupled with executor.RequestWorker since we
        know the worker is dedicated to request scheduling and it either
        blocks on request polling or request submitting. So it is no harm
        to make submit blocking here. But for general cases, we need an
        internal queue to decouple submit and run.
        """

        while True:
            if self._executor is not None and self._executor.has_idle_workers():
                return self._submit_to_guaranteed_pool(fn, *args, **kwargs)
            if (self._burst_executor is not None and
                    self._burst_executor.has_idle_workers()):
                try:
                    fut = self._burst_executor.submit(fn, *args, **kwargs)
                    return fut
                except exceptions.ExecutionPoolFullError:
                    # The burst pool is full, try the next candidate.
                    pass
            if self._executor is not None:
                # No idle workers in either pool, still queue the request
                # to the guaranteed pool to keep behavior consistent.
                return self._submit_to_guaranteed_pool(fn, *args, **kwargs)
            logger.debug('No guaranteed pool set and the burst pool is full, '
                         'retry later.')
            time.sleep(0.1)

    def _submit_to_guaranteed_pool(self, fn, *args,
                                   **kwargs) -> concurrent.futures.Future:
        """Submit to the guaranteed pool with retry.

        The concurrent.futures.ProcessPoolExecutor will terminate all child
        processes and reject new tasks if any of the child process died, e.g.
        OOM killed. So when the pool is broken, we have to rebuild a new pool
        to execute tasks.

        Alternatives considered:
        - multiprocessing.Pool: individual process failure does not affect the
            pool, but it lacks of lazy-init support and does not handle process
            failure gracefully.
        - inherit from concurrent.futures.ProcessPoolExecutor: If a child
            process dies when executing a task, we have to set an exception
            in the Future object of the task. But there is a race condition
            that the child process may dies exactly after get the task from
            queue so there is no (obviously) safe way to record process PID
            to task mapping in the current architecture of
            ProcessPoolExecutor. It is more feasible to implement a custom
            ProcessPoolExecutor with an new architecture.
        - a custom ProcessPoolExecutor: non-trivial to implement, keep for
            future improvement as BrokenProcessPool is not expected to be
            a common case, i.e. it usually indicates there a bug elsewhere
            when this happens.
        """
        with self._guaranteed_pool_lock:
            assert self._executor is not None
            try:
                return self._executor.submit(fn, *args, **kwargs)
            except concurrent.futures.process.BrokenProcessPool as e:
                logger.warning('The guaranteed pool is broken, '
                               f'replacing the pool and retrying. Error: {e}')
                broken_pool = self._executor
                threading.Thread(target=broken_pool.shutdown,
                                 daemon=True).start()
                self._executor = PoolExecutor(
                    max_workers=self._guaranteed_workers,
                    **self._guaranteed_pool_kwargs)
        return self._submit_to_guaranteed_pool(fn, *args, **kwargs)

    def shutdown(self) -> None:
        """Shutdown the executor."""

        if self._burst_executor is not None:
            self._burst_executor.shutdown()
        if self._executor is not None:
            self._executor.shutdown(wait=True)
