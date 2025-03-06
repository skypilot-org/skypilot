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
import dataclasses
import enum
import multiprocessing
import os
import queue as queue_lib
import signal
import sys
import time
import traceback
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
from sky.server.requests import requests as api_requests
from sky.server.requests.queues import mp_queue
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import timeline
from sky.utils import ux_utils

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


class QueueBackend(enum.Enum):
    MULTIPROCESSING = 'multiprocessing'
    # TODO(zhwu): we can add redis backend in the future.


@dataclasses.dataclass
class RequestWorker:
    id: int
    # The type of queue this worker works on.
    schedule_type: api_requests.ScheduleType

    def __str__(self) -> str:
        return f'Worker(id={self.id}, schedule_type={self.schedule_type.value})'


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

    def sigterm_handler(signum: int,
                        frame: Optional['types.FrameType']) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, sigterm_handler)

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
            with ux_utils.enable_traceback():
                stacktrace = traceback.format_exc()
            setattr(e, 'stacktrace', stacktrace)
            with api_requests.update_request(request_id) as request_task:
                assert request_task is not None, request_id
                request_task.status = api_requests.RequestStatus.FAILED
                request_task.set_error(e)
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


def schedule_request(request_id: str,
                     request_name: str,
                     request_body: payloads.RequestBody,
                     func: Callable[P, Any],
                     request_cluster_name: Optional[str] = None,
                     ignore_return_value: bool = False,
                     schedule_type: api_requests.ScheduleType = api_requests.
                     ScheduleType.LONG,
                     is_skypilot_system: bool = False) -> None:
    """Enqueue a request to the request queue."""
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
    input_tuple = (request_id, ignore_return_value)

    logger.info(f'Queuing request: {request_id}')
    _get_queue(schedule_type).put(input_tuple)


def executor_initializer(proc_group: str):
    setproctitle.setproctitle(f'SkyPilot:executor:{proc_group}:'
                              f'{multiprocessing.current_process().pid}')


def request_worker(worker: RequestWorker, max_parallel_size: int) -> None:
    """Worker for the requests.

    Args:
        max_parallel_size: Maximum number of parallel jobs this worker can run.
    """
    proc_group = f'{worker.schedule_type.value}-{worker.id}'
    setproctitle.setproctitle(f'SkyPilot:worker:{proc_group}')
    queue = _get_queue(worker.schedule_type)

    def process_request(executor: concurrent.futures.ProcessPoolExecutor):
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
            logger.info(f'[{worker}] Submitting request: {request_id}')
            # Start additional process to run the request, so that it can be
            # cancelled when requested by a user.
            # TODO(zhwu): since the executor is reusing the request process,
            # multiple requests can share the same process pid, which may cause
            # issues with SkyPilot core functions if they rely on the exit of
            # the process, such as subprocess_daemon.py.
            future = executor.submit(_request_execution_wrapper, request_id,
                                     ignore_return_value)

            if worker.schedule_type == api_requests.ScheduleType.LONG:
                try:
                    future.result(timeout=None)
                except Exception as e:  # pylint: disable=broad-except
                    logger.error(f'[{worker}] Request {request_id} failed: {e}')
                logger.info(f'[{worker}] Finished request: {request_id}')
            else:
                logger.info(f'[{worker}] Submitted request: {request_id}')
        except KeyboardInterrupt:
            # Interrupt the worker process will stop request execution, but
            # the SIGTERM request should be respected anyway since it might
            # be explicitly sent by user.
            # TODO(aylei): crash the API server or recreate the worker process
            # to avoid broken state.
            logger.error(f'[{worker}] Worker process interrupted')
            with ux_utils.print_exception_no_traceback():
                raise
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            # Catch any other exceptions to avoid crashing the worker process.
            logger.error(
                f'[{worker}] Error processing request {request_id}: '
                f'{common_utils.format_exception(e, use_bracket=True)}')

    # Use concurrent.futures.ProcessPoolExecutor instead of multiprocessing.Pool
    # because the former is more efficient with the support of lazy creation of
    # worker processes.
    # We use executor instead of individual multiprocessing.Process to avoid
    # the overhead of forking a new process for each request, which can be about
    # 1s delay.
    with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_parallel_size,
            initializer=executor_initializer,
            initargs=(proc_group,)) as executor:
        while True:
            process_request(executor)


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

    for worker_id in range(max_parallel_for_long):
        worker = RequestWorker(id=worker_id,
                               schedule_type=api_requests.ScheduleType.LONG)
        worker_proc = multiprocessing.Process(target=request_worker,
                                              args=(worker, 1))
        worker_proc.start()
        sub_procs.append(worker_proc)

    # Start a worker for short requests.
    worker = RequestWorker(id=1, schedule_type=api_requests.ScheduleType.SHORT)
    worker_proc = multiprocessing.Process(target=request_worker,
                                          args=(worker, max_parallel_for_short))
    worker_proc.start()
    sub_procs.append(worker_proc)
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
