"""Executor for the requests.

We start limited number of workers for long-running requests, and
significantly more workers for short-running requests. This is to optimize the
resource usage and the latency of the requests.

* Long-running requests are those requests that can take a long time to finish
and more resources are needed, such as cluster launching, starting, job
submission, managed job submission, etc.

* Short-running requests are those requests that can be done quickly, and
require a quick response, such as status check, job status check, etc.

With more short-running workers, we can serve more short-running requests in
parallel, and reduce the latency.

The number of the workers is determined by the system resources.

See the [README.md](../README.md) for detailed architecture of the executor.
"""
import concurrent.futures
import contextlib
import enum
import multiprocessing
import os
import queue as queue_lib
import signal
import sys
import time
import typing
from typing import Any, Callable, Generator, List, Optional, TextIO, Tuple

import setproctitle

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.server.requests import payloads
from sky.server.requests import preconditions
from sky.server.requests import requests as api_requests
from sky.server.requests.queues import mp_queue
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import atomic
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import timeline

if typing.TYPE_CHECKING:
    import types

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

P = ParamSpec('P')

logger = sky_logging.init_logger(__name__)

# On macOS, the default start method for multiprocessing is 'fork', which
# can cause issues with certain types of resources, including those used in
# the QueueManager in mp_queue.py.
# The 'spawn' start method is generally more compatible across different
# platforms, including macOS.
multiprocessing.set_start_method('spawn', force=True)

# Constants based on profiling the peak memory usage while serving various
# sky commands. These estimation are highly related to usage patterns
# (clouds enabled, type of requests, etc. see `tests/load_tests` for details.),
# the profiling covers major clouds and common usage patterns. For user has
# deviated usage pattern, they can override the default estimation by
# environment variables.
# NOTE(dev): update these constants for each release according to the load
# test results.
# TODO(aylei): maintaining these constants is error-prone, we may need to
# automatically tune parallelism at runtime according to system usage stats
# in the future.
_LONG_WORKER_MEM_GB = 0.4
_SHORT_WORKER_MEM_GB = 0.25
# To control the number of long workers.
_CPU_MULTIPLIER_FOR_LONG_WORKERS = 2
# Limit the number of long workers of local API server, since local server is
# typically:
# 1. launched automatically in an environment with high resource contention
#    (e.g. Laptop)
# 2. used by a single user
_MAX_LONG_WORKERS_LOCAL = 4
# Percentage of memory for long requests
# from the memory reserved for SkyPilot.
# This is to reserve some memory for short requests.
_MAX_MEM_PERCENT_FOR_BLOCKING = 0.6
# Minimal number of long workers to ensure responsiveness.
_MIN_LONG_WORKERS = 1
# Minimal number of short workers, there is a daemon task running on short
# workers so at least 2 workers are needed to ensure responsiveness.
_MIN_SHORT_WORKERS = 2

# Default number of burstable workers for local API server. A heuristic number
# that is large enough for most local cases.
# TODO(aylei): the number of burstable workers should be auto-tuned based on the
# system usage stats.
_BURSTABLE_WORKERS_FOR_LOCAL = 1024


class QueueBackend(enum.Enum):
    MULTIPROCESSING = 'multiprocessing'
    # TODO(zhwu): we can add redis backend in the future.


class RequestQueue:
    """The queue for the requests, either redis or multiprocessing.

    The elements in the queue are tuples of (request_id, ignore_return_value).
    """

    def __init__(self,
                 schedule_type: api_requests.ScheduleType,
                 backend: Optional[QueueBackend] = None) -> None:
        self.name = schedule_type.value
        self.backend = backend
        assert (backend is None or
                backend == QueueBackend.MULTIPROCESSING), backend
        self.queue = mp_queue.get_queue(self.name)

    def put(self, request: Tuple[str, bool]) -> None:
        """Put and request to the queue.

        Args:
            request: A tuple of request_id and ignore_return_value.
        """
        self.queue.put(request)  # type: ignore

    def get(self) -> Optional[Tuple[str, bool]]:
        """Get a request from the queue.

        It is non-blocking if the queue is empty, and returns None.

        Returns:
            A tuple of request_id and ignore_return_value.
        """
        try:
            return self.queue.get(block=False)
        except queue_lib.Empty:
            return None

    def __len__(self) -> int:
        """Get the length of the queue."""
        return self.queue.qsize()


queue_backend = QueueBackend.MULTIPROCESSING


class ProcessPoolExecutor(concurrent.futures.ProcessPoolExecutor):
    """A custom ProcessPoolExecutor with additional supports for skypilot.

    The additional supports include:
    1. Disposable workers: support control whether the worker process should
       exit after complete a task.
    2. Idle check: support check if there are any idle workers.
    3. Proactive shutdown: SIGTERM worker processes when the executor is
       shutting down instead of indefinitely waiting.
    """

    # Control whether to reuse the worker process, workers processes are
    # disposable if `reuse_worker` is False.
    # This is a workaround for Python 3.10 since `max_tasks_per_child` was
    # introduced in 3.11.
    # Ref: https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.ProcessPoolExecutor # pylint: disable=line-too-long
    # TODO(aylei): use the official `max_tasks_per_child` when upgrade to 3.11
    _reuse_worker: bool

    # The number of workers that are handling tasks, atomicity across
    # multiple threads is sufficient since the idleness check is best-effort
    # and does not affect the correctness.
    # E.g. the following case is totally fine:
    # 1. Thread 1 checks running == max_workers
    # 2. Thread 2 decrements running
    # 3. Thread 1 schedules the task to other pool even if the pool is
    #    currently idle.
    running: atomic.Int = atomic.Int(0)

    def __init__(self, max_workers: int, reuse_worker: bool = True, **kwargs):
        super().__init__(max_workers=max_workers, **kwargs)
        self._reuse_worker = reuse_worker
        self.max_workers = max_workers

    def submit(self, fn, *args, **kwargs) -> concurrent.futures.Future:
        """Submit a task for execution.

        If reuse_worker is False, wraps the function to exit after completion.
        """
        self.running.increment()
        if not self._reuse_worker:
            # Wrap the function to exit the process after completion
            def wrapped_fn(*fn_args, **fn_kwargs):
                try:
                    fn(*fn_args, **fn_kwargs)
                    sys.exit(0)
                except Exception as e:  # pylint: disable=broad-except
                    # We expect the caller to handle the all exceptions in fn,
                    # but just in case.
                    logger.error(f'Error in disposable: {e}')
                    sys.exit(1)

            fn = wrapped_fn
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


class BurstableProcessPoolExecutor(ProcessPoolExecutor):
    """A ProcessPoolExecutor that supports bursting disposable workers."""

    # _burst_pool is a ProcessPoolExecutor that is used to run burst requests.
    _burst_pool: Optional[ProcessPoolExecutor] = None

    def __init__(self,
                 garanteed_workers: int,
                 burst_workers: int = 0,
                 **kwargs):
        super().__init__(max_workers=garanteed_workers, **kwargs)
        if burst_workers > 0:
            self._burst_pool = ProcessPoolExecutor(max_workers=burst_workers,
                                                   reuse_worker=False)

    def submit(self, fn, *args, **kwargs) -> concurrent.futures.Future:
        """Submit a task for execution.

        If the current pool has no idle workers AND there are idle workers
        in the burst pool, use the idle workers in the burst pool to run the
        task. Queue the task to the current pool otherwise.
        """

        no_idle_garanteed = not self.has_idle_workers()
        has_idle_burst = (self._burst_pool is not None and
                          self._burst_pool.has_idle_workers())
        if no_idle_garanteed and has_idle_burst:
            # Make linter happy.
            assert self._burst_pool is not None
            return self._burst_pool.submit(fn, *args, **kwargs)
        return super().submit(fn, *args, **kwargs)

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the executor."""

        if self._burst_pool is not None:
            self._burst_pool.shutdown(wait=wait)
        super().shutdown(wait=wait)


def executor_initializer(proc_group: str):
    setproctitle.setproctitle(f'SkyPilot:executor:{proc_group}:'
                              f'{multiprocessing.current_process().pid}')


class RequestWorker:
    """A worker that polls requests from the queue and runs them.

    The worker can run at least `garanteed_parallelism` requests in parallel.
    If there are more resources available, it can spin up extra workers up to
    `garanteed_parallelism + burstable_parallelism`.
    """

    # The type of queue this worker works on.
    schedule_type: api_requests.ScheduleType
    # The least number of requests that this worker can run in parallel.
    garanteed_parallelism: int
    # The extra number of requests that this worker can run in parallel
    # if there are available CPU/memory resources.
    burstable_parallelism: int = 0

    def __init__(self,
                 schedule_type: api_requests.ScheduleType,
                 garanteed_parallelism: int,
                 burstable_parallelism: int = 0) -> None:
        self.schedule_type = schedule_type
        self.garanteed_parallelism = garanteed_parallelism
        self.burstable_parallelism = burstable_parallelism

    def __str__(self) -> str:
        return f'Worker(schedule_type={self.schedule_type.value})'

    def process_request(self, executor: BurstableProcessPoolExecutor,
                        queue: RequestQueue) -> None:
        try:
            request_element = queue.get()
            if request_element is None:
                time.sleep(0.1)
                return
            request_id, ignore_return_value = request_element
            request = api_requests.get_request(request_id)
            assert request is not None, f'Request with ID {request_id} is None'
            if request.status == api_requests.RequestStatus.CANCELLED:
                return
            logger.info(f'[{self}] Submitting request: {request_id}')
            # Start additional process to run the request, so that it can be
            # cancelled when requested by a user.
            # TODO(zhwu): since the executor is reusing the request process,
            # multiple requests can share the same process pid, which may cause
            # issues with SkyPilot core functions if they rely on the exit of
            # the process, such as subprocess_daemon.py.
            executor.submit(_request_execution_wrapper, request_id,
                            ignore_return_value)

            logger.info(f'[{self}] Submitted request: {request_id}')
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            # Catch any other exceptions to avoid crashing the worker process.
            logger.error(
                f'[{self}] Error processing request: '
                f'{request_id if "request_id" in locals() else ""} '
                f'{common_utils.format_exception(e, use_bracket=True)}')

    def run(self) -> None:
        # Handle the SIGTERM signal to abort the executor process gracefully.
        signal.signal(signal.SIGTERM, _sigterm_handler)
        proc_group = f'{self.schedule_type.value}'
        setproctitle.setproctitle(f'SkyPilot:worker:{proc_group}')
        queue = _get_queue(self.schedule_type)

        # Use concurrent.futures.ProcessPoolExecutor instead of
        # multiprocessing.Pool because the former is more efficient with the
        # support of lazy creation of worker processes.
        # We use executor instead of individual multiprocessing.Process to avoid
        # the overhead of forking a new process for each request, which can be
        # about 1s delay.
        try:
            executor = BurstableProcessPoolExecutor(
                garanteed_workers=self.garanteed_parallelism,
                burst_workers=self.burstable_parallelism,
                initializer=executor_initializer,
                initargs=(proc_group,))
            while True:
                self.process_request(executor, queue)
        # TODO(aylei): better to distinct between KeyboardInterrupt and SIGTERM.
        except KeyboardInterrupt:
            pass
        finally:
            # In most cases, here we receive either ctrl-c in foreground
            # execution or SIGTERM on server exiting. Gracefully exit the
            # worker process and the executor.
            # TODO(aylei): worker may also be killed by system daemons like
            # OOM killer, crash the API server or recreate the worker process
            # to avoid broken state in such cases.
            logger.info(f'[{self}] Worker process interrupted')
            executor.shutdown()


@annotations.lru_cache(scope='global', maxsize=None)
def _get_queue(schedule_type: api_requests.ScheduleType) -> RequestQueue:
    return RequestQueue(schedule_type, backend=queue_backend)


@contextlib.contextmanager
def override_request_env_and_config(
        request_body: payloads.RequestBody) -> Generator[None, None, None]:
    """Override the environment and SkyPilot config for a request."""
    original_env = os.environ.copy()
    os.environ.update(request_body.env_vars)
    user = models.User(id=request_body.env_vars[constants.USER_ID_ENV_VAR],
                       name=request_body.env_vars[constants.USER_ENV_VAR])
    global_user_state.add_or_update_user(user)
    # Force color to be enabled.
    os.environ['CLICOLOR_FORCE'] = '1'
    server_common.reload_for_new_request(
        client_entrypoint=request_body.entrypoint,
        client_command=request_body.entrypoint_command,
        using_remote_api_server=request_body.using_remote_api_server)
    try:
        with skypilot_config.override_skypilot_config(
                request_body.override_skypilot_config):
            yield
    finally:
        # We need to call the save_timeline() since atexit will not be
        # triggered as multiple requests can be sharing the same process.
        timeline.save_timeline()
        # Restore the original environment variables, so that a new request
        # won't be affected by the previous request, e.g. SKYPILOT_DEBUG
        # setting, etc. This is necessary as our executor is reusing the
        # same process for multiple requests.
        os.environ.clear()
        os.environ.update(original_env)


def _redirect_output(file: TextIO) -> Tuple[int, int]:
    """Redirect stdout and stderr to the log file."""
    fd = file.fileno()  # Get the file descriptor from the file object
    # Store copies of the original stdout and stderr file descriptors
    original_stdout = os.dup(sys.stdout.fileno())
    original_stderr = os.dup(sys.stderr.fileno())

    # Copy this fd to stdout and stderr
    os.dup2(fd, sys.stdout.fileno())
    os.dup2(fd, sys.stderr.fileno())
    return original_stdout, original_stderr


def _restore_output(original_stdout: int, original_stderr: int) -> None:
    """Restore stdout and stderr to their original file descriptors."""
    os.dup2(original_stdout, sys.stdout.fileno())
    os.dup2(original_stderr, sys.stderr.fileno())

    # Close the duplicate file descriptors
    os.close(original_stdout)
    os.close(original_stderr)


def _sigterm_handler(signum: int, frame: Optional['types.FrameType']) -> None:
    raise KeyboardInterrupt


def _request_execution_wrapper(request_id: str,
                               ignore_return_value: bool) -> None:
    """Wrapper for a request execution.

    It wraps the execution of a request to:
    1. Deserialize the request from the request database and serialize the
       return value/exception in the request database;
    2. Update the request status based on the execution result;
    3. Redirect the stdout and stderr of the execution to log file;
    4. Handle the SIGTERM signal to abort the request gracefully.
    """
    # Handle the SIGTERM signal to abort the request processing gracefully.
    signal.signal(signal.SIGTERM, _sigterm_handler)

    pid = multiprocessing.current_process().pid
    logger.info(f'Running request {request_id} with pid {pid}')
    with api_requests.update_request(request_id) as request_task:
        assert request_task is not None, request_id
        log_path = request_task.log_path
        request_task.pid = pid
        request_task.status = api_requests.RequestStatus.RUNNING
        func = request_task.entrypoint
        request_body = request_task.request_body

    with log_path.open('w', encoding='utf-8') as f:
        # Store copies of the original stdout and stderr file descriptors
        original_stdout, original_stderr = _redirect_output(f)
        # Redirect the stdout/stderr before overriding the environment and
        # config, as there can be some logs during override that needs to be
        # captured in the log file.
        try:
            with override_request_env_and_config(request_body):
                return_value = func(**request_body.to_kwargs())
                f.flush()
        except KeyboardInterrupt:
            logger.info(f'Request {request_id} cancelled by user')
            _restore_output(original_stdout, original_stderr)
            return
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            api_requests.set_request_failed(request_id, e)
            _restore_output(original_stdout, original_stderr)
            logger.info(f'Request {request_id} failed due to '
                        f'{common_utils.format_exception(e)}')
            return
        else:
            with api_requests.update_request(request_id) as request_task:
                assert request_task is not None, request_id
                request_task.status = api_requests.RequestStatus.SUCCEEDED
                if not ignore_return_value:
                    request_task.set_return_value(return_value)
            _restore_output(original_stdout, original_stderr)
            logger.info(f'Request {request_id} finished')

    # Exit
    sys.exit(1)


def schedule_request(
        request_id: str,
        request_name: str,
        request_body: payloads.RequestBody,
        func: Callable[P, Any],
        request_cluster_name: Optional[str] = None,
        ignore_return_value: bool = False,
        schedule_type: api_requests.ScheduleType = (
            api_requests.ScheduleType.LONG),
        is_skypilot_system: bool = False,
        precondition: Optional[preconditions.Precondition] = None) -> None:
    """Enqueue a request to the request queue.

    Args:
        request_id: ID of the request.
        request_name: Name of the request type, e.g. "sky.launch".
        request_body: The request body containing parameters and environment
            variables.
        func: The function to execute when the request is processed.
        request_cluster_name: The name of the cluster associated with this
            request, if any.
        ignore_return_value: If True, the return value of the function will be
            ignored.
        schedule_type: The type of scheduling to use for this request, refer to
            `api_requests.ScheduleType` for more details.
        is_skypilot_system: Denote whether the request is from SkyPilot system.
        precondition: If a precondition is provided, the request will only be
            scheduled for execution when the precondition is met (returns True).
            The precondition is waited asynchronously and does not block the
            caller.
    """
    user_id = request_body.env_vars[constants.USER_ID_ENV_VAR]
    if is_skypilot_system:
        user_id = server_constants.SKYPILOT_SYSTEM_USER_ID
        global_user_state.add_or_update_user(
            models.User(id=user_id, name=user_id))
    request = api_requests.Request(request_id=request_id,
                                   name=server_constants.REQUEST_NAME_PREFIX +
                                   request_name,
                                   entrypoint=func,
                                   request_body=request_body,
                                   status=api_requests.RequestStatus.PENDING,
                                   created_at=time.time(),
                                   schedule_type=schedule_type,
                                   user_id=user_id,
                                   cluster_name=request_cluster_name)

    if not api_requests.create_if_not_exists(request):
        logger.debug(f'Request {request_id} already exists.')
        return

    request.log_path.touch()

    def enqueue():
        input_tuple = (request_id, ignore_return_value)
        logger.info(f'Queuing request: {request_id}')
        _get_queue(schedule_type).put(input_tuple)

    if precondition is not None:
        # Wait async to avoid blocking caller.
        precondition.wait_async(on_condition_met=enqueue)
    else:
        enqueue()


def start(deploy: bool) -> List[multiprocessing.Process]:
    """Start the request workers."""
    # Determine the job capacity of the workers based on the system resources.
    cpu_count = common_utils.get_cpu_count()
    mem_size_gb = common_utils.get_mem_size_gb()
    mem_size_gb = max(0, mem_size_gb - server_constants.MIN_AVAIL_MEM_GB)
    max_parallel_for_long = _max_long_worker_parallism(cpu_count,
                                                       mem_size_gb,
                                                       local=not deploy)
    max_parallel_for_short = _max_short_worker_parallism(
        mem_size_gb, max_parallel_for_long)
    logger.info(
        f'SkyPilot API server will start {max_parallel_for_long} workers for '
        f'long requests and will allow at max '
        f'{max_parallel_for_short} short requests in parallel.')

    sub_procs = []
    # Setup the queues.
    if queue_backend == QueueBackend.MULTIPROCESSING:
        logger.info('Creating shared request queues')
        queue_names = [
            schedule_type.value for schedule_type in api_requests.ScheduleType
        ]
        # TODO(aylei): make queue manager port configurable or pick an available
        # port automatically.
        port = mp_queue.DEFAULT_QUEUE_MANAGER_PORT
        if not common_utils.is_port_available(port):
            raise RuntimeError(
                f'SkyPilot API server fails to start as port {port!r} is '
                'already in use by another process.')
        queue_server = multiprocessing.Process(
            target=mp_queue.start_queue_manager, args=(queue_names, port))
        queue_server.start()
        sub_procs.append(queue_server)
        mp_queue.wait_for_queues_to_be_ready(queue_names, port=port)

    logger.info('Request queues created')

    burstable_parallelism = _BURSTABLE_WORKERS_FOR_LOCAL if not deploy else 0
    # Start a worker for long requests.
    long_worker = RequestWorker(schedule_type=api_requests.ScheduleType.LONG,
                                garanteed_parallelism=max_parallel_for_long,
                                burstable_parallelism=burstable_parallelism)
    long_worker_proc = multiprocessing.Process(target=long_worker.run)
    long_worker_proc.start()
    sub_procs.append(long_worker_proc)

    # Start a worker for short requests.
    short_worker = RequestWorker(schedule_type=api_requests.ScheduleType.SHORT,
                                 garanteed_parallelism=max_parallel_for_short,
                                 burstable_parallelism=burstable_parallelism)
    short_worker_proc = multiprocessing.Process(target=short_worker.run)
    short_worker_proc.start()
    sub_procs.append(short_worker_proc)
    return sub_procs


@annotations.lru_cache(scope='global', maxsize=1)
def _max_long_worker_parallism(cpu_count: int,
                               mem_size_gb: float,
                               local=False) -> int:
    """Max parallelism for long workers."""
    cpu_based_max_parallel = cpu_count * _CPU_MULTIPLIER_FOR_LONG_WORKERS
    mem_based_max_parallel = int(mem_size_gb * _MAX_MEM_PERCENT_FOR_BLOCKING /
                                 _LONG_WORKER_MEM_GB)
    n = max(_MIN_LONG_WORKERS,
            min(cpu_based_max_parallel, mem_based_max_parallel))
    if local:
        return min(n, _MAX_LONG_WORKERS_LOCAL)
    return n


@annotations.lru_cache(scope='global', maxsize=1)
def _max_short_worker_parallism(mem_size_gb: float,
                                long_worker_parallism: int) -> int:
    """Max parallelism for short workers."""
    available_mem = mem_size_gb - (long_worker_parallism * _LONG_WORKER_MEM_GB)
    n = max(_MIN_SHORT_WORKERS, int(available_mem / _SHORT_WORKER_MEM_GB))
    return n
