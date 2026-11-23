"""ProcessPoolExecutor with additional supports for skypilot."""
import concurrent.futures
import concurrent.futures.process
import logging
import multiprocessing
import os
import signal
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sky import exceptions
from sky.metrics import utils as metrics_utils
from sky.utils import atomic
from sky.utils import subprocess_utils

logger = logging.getLogger(__name__)

# Warm start lets at most this many workers be starting (spawned, initializer
# not yet finished) at a time. Each start imports SkyPilot, which costs CPU and
# memory, and runs plugin initializers, some of which connect to the database.
# Pacing by readiness means a slow start slows warm start down instead of
# piling up starting processes.
_WARM_START_MAX_STARTING = 2
_WARM_START_POLL_SECONDS = 0.1
# Warm start stops, and the remaining workers start on demand, when no worker
# becomes ready for this long.
_WARM_START_STALL_SECONDS = 120
# BurstableExecutor turns warm start off for a pool after this many pools in a
# row broke before warm start finished (a worker kept dying while starting).
_WARM_START_MAX_CONSECUTIVE_FAILURES = 5
# How long shutdown waits for the warm-start thread, which checks for a stop
# every _WARM_START_POLL_SECONDS.
_WARM_START_JOIN_SECONDS = 10
# How long warm start of a pool that replaces a broken one waits for the broken
# pool's workers to exit first. Idle workers ignore SIGTERM, so they last until
# shutdown kills them after its grace period (10 s).
_WARM_START_PREDECESSOR_SECONDS = 30
# How long replacing a pool that broke while warm start ran waits for the
# stdlib to reap its workers, when telling a start-up failure from a serving
# crash needs their exit codes.
_BROKEN_POOL_JOIN_SECONDS = 5

# What a worker reports on its start-up pipe once its initializer has run,
# followed by its pid.
_STARTED = b'r'
_START_FAILED = b'f'


def _send_start_signal(conn, status: bytes) -> None:
    """One small pipe write: atomic and lock-free, so a worker dying at any
    point cannot leave the parent blocked."""
    try:
        conn.send_bytes(status + str(os.getpid()).encode())
    except OSError:
        pass  # The parent closed its end, e.g. it is shutting down.


def _parse_start_signal(message: bytes) -> Tuple[bytes, int]:
    """Returns the status and pid (0 if unreadable) of a start-up report."""
    try:
        pid = int(message[1:] or 0)
    except ValueError:
        pid = 0
    return message[:1], pid


def _run_initializer(ready_conn, initializer: Optional[Callable],
                     initargs: Tuple) -> None:
    """Runs a worker's initializer and reports whether it started."""
    try:
        if initializer is not None:
            initializer(*initargs)
    except BaseException:
        _send_start_signal(ready_conn, _START_FAILED)
        raise
    _warn_if_sigterm_handled()
    _send_start_signal(ready_conn, _STARTED)


def _warn_if_sigterm_handled() -> None:
    """Warm start tells a starting worker that the stdlib stopped (it exits
    with -SIGTERM) from one that failed to start. A SIGTERM handler installed
    during start-up, e.g. by a plugin, would blur that, so say so."""
    if signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:
        logger.warning(
            'An executor worker initializer installed a SIGTERM handler; '
            'workers stopped while starting may be counted as start failures.')


def _count_start_failure(pool: str, reason: str) -> None:
    metrics_utils.SKY_APISERVER_EXECUTOR_WORKER_START_FAILURES_TOTAL.labels(
        pool=pool, reason=reason).inc()


class PoolExecutor(concurrent.futures.ProcessPoolExecutor):
    """A custom ProcessPoolExecutor with additional supports for skypilot.

    The additional supports include:
    1. Disposable workers: support control whether the worker process should
       exit after complete a task.
    2. Idle check: support check if there are any idle workers.
    3. Proactive shutdown: SIGTERM worker processes when the executor is
       shutting down instead of indefinitely waiting.
    4. Warm start (opt-in): start every worker in the background, a few at a
       time, instead of on the first submits that find no idle worker.
    """

    def __init__(self,
                 max_workers: int,
                 warm_start: bool = False,
                 name: str = 'guaranteed',
                 wait_for: Optional[List[multiprocessing.Process]] = None,
                 **kwargs):
        # Labels this pool's metrics, e.g. the request schedule type.
        self.name = name
        # Workers of the pool this one replaces, still exiting; warm start
        # waits for them so the two pools do not hold memory at the same time.
        self._wait_for = list(wait_for or [])
        # Every worker reports on this pipe whether its initializer ran: warm
        # start paces itself on it, and start-up failures are counted from it.
        context = kwargs.get('mp_context') or multiprocessing.get_context()
        self._ready_reader: Any
        self._ready_reader, ready_writer = context.Pipe(duplex=False)
        self._ready_lock = threading.Lock()
        self._ready_count = 0
        # Pids of the workers that reported they started.
        self._ready_pids: Set[int] = set()
        self.start_failures = 0
        kwargs['initargs'] = (ready_writer, kwargs.get('initializer'),
                              kwargs.get('initargs', ()))
        kwargs['initializer'] = _run_initializer
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
        self._warm_start_stop = threading.Event()
        self._warm_start_thread: Optional[threading.Thread] = None
        # Set once warm start has started every worker and all are ready.
        self._warm_start_done = threading.Event()
        # Why the warm-start thread returned: done, stalled, stopped or broken.
        self._warm_start_end: Optional[str] = None
        # Workers started when warm start last looked; compared with the ready
        # reports to tell whether a break happened while workers were starting.
        self._warm_start_started = 0
        if warm_start:
            # Starting workers from another thread is only safe without fork
            # (cpython gh-90622). The stdlib flag for that exists on Python
            # 3.11+, which also added _spawn_process.
            if getattr(self, '_safe_to_dynamically_spawn_children', False):
                self._warm_start_thread = threading.Thread(
                    target=self._warm_up, name='pool-warm-start', daemon=True)
                self._warm_start_thread.start()
            else:
                logger.warning('Executor warm start needs Python 3.11+ and a '
                               'non-fork start method; workers will start on '
                               'demand.')

    def _warm_up(self) -> None:
        """Starts workers, at most _WARM_START_MAX_STARTING at a time, until
        every worker is started and ready.

        The lock is held only while starting each process, so workers that are
        up take tasks while the rest start. Stops, leaving the rest to start on
        demand, if no worker becomes ready for _WARM_START_STALL_SECONDS.
        """
        # ProcessPoolExecutor has no public way to add a worker without
        # occupying the idle ones, so this uses its private state.
        # pylint: disable=protected-access
        pool: Any = self
        self._wait_for_predecessor()
        progress_at = time.monotonic()
        while not self._warm_start_stop.wait(_WARM_START_POLL_SECONDS):
            if self.drain_start_signals():
                progress_at = time.monotonic()
            ready = self._ready_count
            with pool._shutdown_lock:
                if pool._broken:
                    self._warm_start_end = 'broken'
                    return
                if pool._shutdown_thread:
                    self._warm_start_end = 'stopped'
                    return
                started = len(pool._processes)
                self._warm_start_started = started
                if started >= self.max_workers:
                    # Every worker is started; done once all are ready too.
                    if ready >= self.max_workers:
                        break
                elif started - ready < _WARM_START_MAX_STARTING:
                    pool._start_executor_manager_thread()
                    pool._spawn_process()
                    self._warm_start_started = len(pool._processes)
                    # Let the manager thread watch the new worker right away.
                    pool._executor_manager_thread_wakeup.wakeup()
                    continue
            if time.monotonic() - progress_at > _WARM_START_STALL_SECONDS:
                logger.warning(
                    f'Warm start stopped: no executor worker became ready in '
                    f'{_WARM_START_STALL_SECONDS}s ({ready} of '
                    f'{self.max_workers} ready). The rest start on demand.')
                with pool._shutdown_lock:
                    if pool._broken or pool._shutdown_thread:
                        self._warm_start_end = ('broken'
                                                if pool._broken else 'stopped')
                        return
                    self._warm_start_end = 'stalled'
                    # Tasks submitted during warm start started no worker;
                    # start workers for them now, up to the pool size.
                    missing = min(len(pool._pending_work_items),
                                  self.max_workers - len(pool._processes))
                    for _ in range(missing):
                        pool._spawn_process()
                    # Let the manager thread watch the new workers.
                    pool._executor_manager_thread_wakeup.wakeup()
                return
        if self._warm_start_stop.is_set():
            self._warm_start_end = 'stopped'
            return
        # pylint: enable=protected-access
        self._warm_start_end = 'done'
        self._warm_start_done.set()
        logger.info(f'Warm start: {self.max_workers} workers started.')

    def _wait_for_predecessor(self) -> None:
        """Waits, up to _WARM_START_PREDECESSOR_SECONDS, for the workers of the
        broken pool this one replaces to exit. Submits can still start this
        pool's first worker meanwhile."""
        deadline = time.monotonic() + _WARM_START_PREDECESSOR_SECONDS
        while any(p.is_alive() for p in self._wait_for):
            if time.monotonic() > deadline:
                logger.warning(
                    f'[{self.name}] Workers of the replaced executor pool are '
                    f'still running after {_WARM_START_PREDECESSOR_SECONDS}s; '
                    'warm-starting anyway.')
                break
            if self._warm_start_stop.wait(_WARM_START_POLL_SECONDS):
                break
        self._wait_for = []

    def broke_during_warm_start(self) -> bool:
        """Whether this broken pool broke because workers failed to start.

        True only while warm start was running and either a worker reported
        that its start-up failed, no worker of the pool had become ready yet,
        or a worker that never became ready exited on its own (killed, e.g.
        out of memory, crashed or aborted). Otherwise a ready worker died,
        possibly serving a task, and the break counts as a serving break.
        """
        if self._warm_start_thread is None or self._warm_start_end not in (
                None, 'broken'):
            return False
        if self.start_failures > 0 or (self._ready_count == 0 and
                                       self._warm_start_started > 0):
            return True
        return self._starting_worker_died()

    def wait_until_broken_handled(self, timeout: float) -> None:
        """Waits for the stdlib to finish terminating and reaping the workers
        of this broken pool, so their exit codes are final."""
        pool: Any = self
        manager = pool._executor_manager_thread  # pylint: disable=protected-access
        if manager is not None and manager is not threading.current_thread():
            manager.join(timeout)
            if manager.is_alive():
                logger.warning(
                    f'[{self.name}] Workers of a broken executor pool did not '
                    f'exit within {timeout}s of being stopped.')

    def _starting_worker_died(self) -> bool:
        """Whether a worker that never reported ready exited on its own.

        When a pool breaks, the stdlib stops the other workers with SIGTERM. A
        worker only installs its own SIGTERM handler when it runs a request,
        so a worker stopped while starting exits with -SIGTERM; any other exit
        of a worker that never became ready is its own failure to start.
        """
        # Exit codes are only final once the stdlib reaped the workers, and a
        # worker that became ready may have reported it just before the break.
        self.wait_until_broken_handled(_BROKEN_POOL_JOIN_SECONDS)
        self.drain_start_signals()
        processes = self._processes  # pylint: disable=protected-access
        if not processes:
            return False
        with self._ready_lock:
            ready_pids = set(self._ready_pids)
        for process in list(processes.values()):
            if process.pid in ready_pids:
                continue
            try:
                exitcode = process.exitcode
            except ValueError:
                continue  # Already closed.
            if exitcode is not None and exitcode != -signal.SIGTERM:
                return True
        return False

    def drain_start_signals(self) -> int:
        """Reads the workers' start-up reports; counts failed starts in the
        metrics. Returns how many workers newly reported they started."""
        started = failed = 0
        with self._ready_lock:
            try:
                while self._ready_reader.poll():
                    status, pid = _parse_start_signal(
                        self._ready_reader.recv_bytes())
                    if status == _START_FAILED:
                        failed += 1
                    else:
                        started += 1
                        self._ready_pids.add(pid)
            except (EOFError, OSError):
                pass  # Closed by shutdown.
            self._ready_count += started
            self.start_failures += failed
        for _ in range(failed):
            _count_start_failure(self.name, 'error')
        return started

    def _warm_start_running(self) -> bool:
        # _warm_start_end is set under the pool's lock when warm start stalls,
        # so a submit after the stall fallback starts workers as usual.
        return (self._warm_start_thread is not None and
                self._warm_start_end is None and
                self._warm_start_thread.is_alive())

    def _adjust_process_count(self) -> None:
        """Called by submit, with the pool's lock held, to start a worker when
        none is idle.

        While warm start runs, it alone starts workers, so the cap on workers
        starting at once also holds for submits: a task takes an idle worker if
        there is one, as the stdlib does, and otherwise waits in the queue for
        the next worker to become ready. The one exception is a pool with no
        worker yet: the task starts the first one, so it never waits on a pool
        with nothing to run it (warm start counts that worker toward its cap).
        Once warm start is done or has given up, workers start on demand as
        usual.
        """
        pool: Any = self
        # pylint: disable=protected-access
        running = self._warm_start_running()
        if running and pool._processes:
            pool._idle_worker_semaphore.acquire(blocking=False)
            return
        super()._adjust_process_count()  # type: ignore[misc]
        if running:
            # Count the first worker toward the pool's started workers.
            self._warm_start_started = len(pool._processes)

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
        # Stop warm start first, so no worker starts after the snapshot below.
        self._warm_start_stop.set()
        if self._warm_start_thread is not None:
            self._warm_start_thread.join(_WARM_START_JOIN_SECONDS)
            if self._warm_start_thread.is_alive():
                logger.warning('Warm start did not stop within '
                               f'{_WARM_START_JOIN_SECONDS}s; shutting down '
                               'the pool anyway.')
        self.drain_start_signals()
        with self._ready_lock:
            self._ready_reader.close()
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
def _disposable_worker(fn, initializer, initargs, result_queue, args, kwargs,
                       ready_conn):
    """The worker function that is used to run the task.

    Args:
        fn: The function to run.
        initializer: The initializer function to run before running the task.
        initargs: The arguments to pass to the initializer function.
        result_queue: The queue to put the result and exception into.
        args: The arguments to pass to the function.
        kwargs: The keyword arguments to pass to the function.
        ready_conn: Where the worker reports whether its initializer ran.
    """
    try:
        _run_initializer(ready_conn, initializer, initargs)
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
                 initargs: Tuple = (),
                 name: str = 'burst'):
        # Labels this executor's metrics, e.g. the request schedule type.
        self.name = name
        self.max_workers: Optional[int] = max_workers
        self.workers: Dict[int, multiprocessing.Process] = {}
        self._shutdown: bool = False
        self._lock: threading.Lock = threading.Lock()
        self._initializer: Optional[Callable] = initializer
        self._initargs: Tuple = initargs

    def _monitor_worker(self, process: multiprocessing.Process,
                        future: concurrent.futures.Future,
                        result_queue: multiprocessing.Queue,
                        ready_reader: Any) -> None:
        """Monitor the worker process and cleanup when it's done."""
        try:
            process.join()
            # Free the worker's slot before resolving the future, so a caller
            # that submits again as soon as it has the result finds room.
            self._remove_worker(process)
            self._count_start_failure(ready_reader)
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
            ready_reader.close()
            self._remove_worker(process)

    def _remove_worker(self, process: multiprocessing.Process) -> None:
        if process.pid:
            with self._lock:
                self.workers.pop(process.pid, None)

    def _count_start_failure(self, ready_reader: Any) -> None:
        """Counts an exited worker that never finished starting."""
        if self._shutdown:
            return  # Killed by shutdown, not a failure to start.
        try:
            message = ready_reader.recv_bytes() if ready_reader.poll() else None
        except (EOFError, OSError):
            message = None
        if message is None:
            _count_start_failure(self.name, 'died')
        elif _parse_start_signal(message)[0] == _START_FAILED:
            _count_start_failure(self.name, 'error')

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
        ready_reader, ready_writer = multiprocessing.Pipe(duplex=False)
        process = multiprocessing.Process(target=_disposable_worker,
                                          args=(fn, self._initializer,
                                                self._initargs, result_queue,
                                                args, kwargs, ready_writer))
        process.daemon = True
        process.start()
        ready_writer.close()

        with self._lock:
            pid = process.pid or 0
            if pid == 0:
                raise RuntimeError('Failed to start process')
            self.workers[pid] = process

        # Start monitor thread to cleanup the worker process when it's done
        monitor_thread = threading.Thread(target=self._monitor_worker,
                                          args=(process, future, result_queue,
                                                ready_reader),
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
                 name: str = 'guaranteed',
                 **kwargs):
        # Labels this executor's metrics, e.g. the request schedule type.
        self._name = name
        # Pools in a row that broke before warm start finished.
        self._warm_start_failures = 0
        if garanteed_workers > 0:
            self._guaranteed_workers = garanteed_workers
            # Warm start applies to the guaranteed pool, including a pool
            # rebuilt after it broke; burst workers stay one-off.
            self._guaranteed_pool_kwargs = dict(kwargs,
                                                warm_start=warm_start,
                                                name=name)
            self._guaranteed_pool_lock = threading.Lock()
            self._executor = PoolExecutor(max_workers=garanteed_workers,
                                          **self._guaranteed_pool_kwargs)
        if burst_workers > 0:
            # Burst workers next to a guaranteed pool get their own label.
            burst_name = f'{name}-burst' if garanteed_workers > 0 else name
            self._burst_executor = DisposableExecutor(max_workers=burst_workers,
                                                      name=burst_name,
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
                self._record_broken_pool(broken_pool)
                # Workers the stdlib could not stop (idle ones ignore SIGTERM)
                # live until shutdown kills them.
                survivors = list((broken_pool._processes or {}).values())  # pylint: disable=protected-access
                threading.Thread(target=broken_pool.shutdown,
                                 daemon=True).start()
                self._executor = PoolExecutor(
                    max_workers=self._guaranteed_workers,
                    wait_for=survivors,
                    **self._guaranteed_pool_kwargs)
        return self._submit_to_guaranteed_pool(fn, *args, **kwargs)

    def _record_broken_pool(self, broken_pool: PoolExecutor) -> None:
        """Counts a broken pool, and turns warm start off for the pools that
        replace it once it keeps failing. Call with _guaranteed_pool_lock held.
        """
        # A worker whose initializer raised reported it on the start-up pipe.
        broken_pool.drain_start_signals()
        if not broken_pool.broke_during_warm_start():
            self._warm_start_failures = 0
            metrics_utils.SKY_APISERVER_EXECUTOR_POOL_BROKEN_TOTAL.labels(
                pool=self._name, phase='serving').inc()
            return
        self._warm_start_failures += 1
        metrics_utils.SKY_APISERVER_EXECUTOR_POOL_BROKEN_TOTAL.labels(
            pool=self._name, phase='warm_start').inc()
        if broken_pool.start_failures == 0:
            # No worker was ready and none reported an error: a starting worker
            # was killed.
            _count_start_failure(self._name, 'died')
        if (self._warm_start_failures >= _WARM_START_MAX_CONSECUTIVE_FAILURES
                and self._guaranteed_pool_kwargs.get('warm_start')):
            self._guaranteed_pool_kwargs['warm_start'] = False
            metrics_utils.SKY_APISERVER_EXECUTOR_WARM_START_DISABLED.labels(
                pool=self._name).set(1)
            logger.warning(
                f'[{self._name}] {self._warm_start_failures} executor pools in '
                'a row broke while warm start was starting workers; turning '
                'warm start off. Workers now start on demand.')

    def shutdown(self) -> None:
        """Shutdown the executor."""

        if self._burst_executor is not None:
            self._burst_executor.shutdown()
        if self._executor is not None:
            self._executor.shutdown(wait=True)
