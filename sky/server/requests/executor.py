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
import asyncio
import concurrent.futures
import contextlib
import multiprocessing
import os
import queue as queue_lib
import signal
import sys
import threading
import time
import typing
from typing import Any, Callable, Generator, List, Optional, TextIO, Tuple

import setproctitle

from sky import exceptions
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.server import common as server_common
from sky.server import config as server_config
from sky.server import constants as server_constants
from sky.server.requests import payloads
from sky.server.requests import preconditions
from sky.server.requests import process
from sky.server.requests import requests as api_requests
from sky.server.requests.queues import local_queue
from sky.server.requests.queues import mp_queue
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import context
from sky.utils import context_utils
from sky.utils import subprocess_utils
from sky.utils import tempstore
from sky.utils import timeline
from sky.workspaces import core as workspaces_core

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


class RequestQueue:
    """The queue for the requests, either redis or multiprocessing.

    The elements in the queue are tuples of (request_id, ignore_return_value).
    """

    def __init__(self,
                 schedule_type: api_requests.ScheduleType,
                 backend: Optional[server_config.QueueBackend] = None) -> None:
        self.name = schedule_type.value
        self.backend = backend
        if backend == server_config.QueueBackend.MULTIPROCESSING:
            self.queue = mp_queue.get_queue(self.name)
        elif backend == server_config.QueueBackend.LOCAL:
            self.queue = local_queue.get_queue(self.name)
        else:
            raise RuntimeError(f'Invalid queue backend: {backend}')

    def put(self, request: Tuple[str, bool, bool]) -> None:
        """Put and request to the queue.

        Args:
            request: A tuple of request_id, ignore_return_value, and retryable.
        """
        self.queue.put(request)  # type: ignore

    def get(self) -> Optional[Tuple[str, bool, bool]]:
        """Get a request from the queue.

        It is non-blocking if the queue is empty, and returns None.

        Returns:
            A tuple of request_id, ignore_return_value, and retryable.
        """
        try:
            return self.queue.get(block=False)
        except queue_lib.Empty:
            return None

    def __len__(self) -> int:
        """Get the length of the queue."""
        return self.queue.qsize()


queue_backend = server_config.QueueBackend.MULTIPROCESSING


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

    def __init__(self, schedule_type: api_requests.ScheduleType,
                 config: server_config.WorkerConfig) -> None:
        self.schedule_type = schedule_type
        self.garanteed_parallelism = config.garanteed_parallelism
        self.burstable_parallelism = config.burstable_parallelism
        self._thread: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()

    def __str__(self) -> str:
        return f'Worker(schedule_type={self.schedule_type.value})'

    def run_in_background(self) -> None:
        # Thread dispatcher is sufficient for current scale, refer to
        # tests/load_tests/test_queue_dispatcher.py for more details.
        # Use daemon thread for automatic cleanup.
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        self._thread = thread

    def cancel(self) -> None:
        if self._thread is not None:
            self._cancel_event.set()
            self._thread.join()

    def process_request(self, executor: process.BurstableExecutor,
                        queue: RequestQueue) -> None:
        try:
            request_element = queue.get()
            if request_element is None:
                time.sleep(0.1)
                return
            request_id, ignore_return_value, retryable = request_element
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
            fut = executor.submit_until_success(_request_execution_wrapper,
                                                request_id, ignore_return_value)
            if retryable:
                # If the task might fail and be retried, start a thread to
                # monitor the future and process retry.
                threading.Thread(target=self.handle_task_result,
                                 args=(fut, request_element),
                                 daemon=True).start()

            logger.info(f'[{self}] Submitted request: {request_id}')
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            # Catch any other exceptions to avoid crashing the worker process.
            logger.error(
                f'[{self}] Error processing request: '
                f'{request_id if "request_id" in locals() else ""} '
                f'{common_utils.format_exception(e, use_bracket=True)}')

    def handle_task_result(self, fut: concurrent.futures.Future,
                           request_element: Tuple[str, bool, bool]) -> None:
        try:
            fut.result()
        except exceptions.ExecutionRetryableError as e:
            time.sleep(e.retry_wait_seconds)
            # Reschedule the request.
            queue = _get_queue(self.schedule_type)
            queue.put(request_element)

    def run(self) -> None:
        # Handle the SIGTERM signal to abort the executor process gracefully.
        proc_group = f'{self.schedule_type.value}'
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, _sigterm_handler)
            setproctitle.setproctitle(f'SkyPilot:worker:{proc_group}')
        queue = _get_queue(self.schedule_type)

        # Use concurrent.futures.ProcessPoolExecutor instead of
        # multiprocessing.Pool because the former is more efficient with the
        # support of lazy creation of worker processes.
        # We use executor instead of individual multiprocessing.Process to avoid
        # the overhead of forking a new process for each request, which can be
        # about 1s delay.
        try:
            executor = process.BurstableExecutor(
                garanteed_workers=self.garanteed_parallelism,
                burst_workers=self.burstable_parallelism,
                initializer=executor_initializer,
                initargs=(proc_group,))
            while not self._cancel_event.is_set():
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
    # Note: may be overridden by AuthProxyMiddleware.
    # TODO(zhwu): we need to make the entire request a context available to the
    # entire request execution, so that we can access info like user through
    # the execution.
    user = models.User(id=request_body.env_vars[constants.USER_ID_ENV_VAR],
                       name=request_body.env_vars[constants.USER_ENV_VAR])
    global_user_state.add_or_update_user(user)
    # Refetch the user to get the latest user info, including the created_at
    # field.
    user = global_user_state.get_user(user.id)

    # Force color to be enabled.
    os.environ['CLICOLOR_FORCE'] = '1'
    server_common.reload_for_new_request(
        client_entrypoint=request_body.entrypoint,
        client_command=request_body.entrypoint_command,
        using_remote_api_server=request_body.using_remote_api_server,
        user=user)
    try:
        logger.debug(
            f'override path: {request_body.override_skypilot_config_path}')
        with skypilot_config.override_skypilot_config(
                request_body.override_skypilot_config,
                request_body.override_skypilot_config_path):
            # Rejecting requests to workspaces that the user does not have
            # permission to access.
            workspaces_core.reject_request_for_unauthorized_workspace(user)
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
    5. Maintain the lifecycle of the temp dir used by the request.
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

    # Append to the log file instead of overwriting it since there might be
    # logs from previous retries.
    with log_path.open('a', encoding='utf-8') as f:
        # Store copies of the original stdout and stderr file descriptors
        original_stdout, original_stderr = _redirect_output(f)
        # Redirect the stdout/stderr before overriding the environment and
        # config, as there can be some logs during override that needs to be
        # captured in the log file.
        try:
            with override_request_env_and_config(request_body), \
                tempstore.tempdir():
                if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
                    config = skypilot_config.to_dict()
                    logger.debug(f'request config: \n'
                                 f'{common_utils.dump_yaml_str(dict(config))}')
                return_value = func(**request_body.to_kwargs())
                f.flush()
        except KeyboardInterrupt:
            logger.info(f'Request {request_id} cancelled by user')
            # Kill all children processes related to this request.
            # Each executor handles a single request, so we can safely kill all
            # children processes related to this request.
            # This is required as python does not pass the KeyboardInterrupt
            # to the threads that are not main thread.
            subprocess_utils.kill_children_processes()
            _restore_output(original_stdout, original_stderr)
            return
        except exceptions.ExecutionRetryableError as e:
            logger.error(e)
            logger.info(e.hint)
            with api_requests.update_request(request_id) as request_task:
                assert request_task is not None, request_id
                # Retried request will undergo rescheduling and a new execution,
                # clear the pid of the request.
                request_task.pid = None
            # Yield control to the scheduler for uniform handling of retries.
            _restore_output(original_stdout, original_stderr)
            raise
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            api_requests.set_request_failed(request_id, e)
            _restore_output(original_stdout, original_stderr)
            logger.info(f'Request {request_id} failed due to '
                        f'{common_utils.format_exception(e)}')
            return
        else:
            api_requests.set_request_succeeded(
                request_id, return_value if not ignore_return_value else None)
            _restore_output(original_stdout, original_stderr)
            logger.info(f'Request {request_id} finished')


async def execute_request_coroutine(request: api_requests.Request):
    """Execute a request in current event loop.

    Similar to _request_execution_wrapper, but executed as coroutine in current
    event loop. This is designed for executing tasks that are not CPU
    intensive, e.g. sky logs.
    """
    ctx = context.get()
    if ctx is None:
        raise ValueError('Context is not initialized')
    logger.info(f'Executing request {request.request_id} in coroutine')
    func = request.entrypoint
    request_body = request.request_body
    with api_requests.update_request(request.request_id) as request_task:
        request_task.status = api_requests.RequestStatus.RUNNING
    # Redirect stdout and stderr to the request log path.
    original_output = ctx.redirect_log(request.log_path)
    # Override environment variables that backs env_options.Options
    # TODO(aylei): compared to process executor, running task in coroutine has
    # two issues to fix:
    # 1. skypilot config is not contextual
    # 2. envs that read directly from os.environ are not contextual
    ctx.override_envs(request_body.env_vars)
    fut: asyncio.Future = context_utils.to_thread(func,
                                                  **request_body.to_kwargs())

    async def poll_task(request_id: str) -> bool:
        request = api_requests.get_request(request_id)
        if request is None:
            raise RuntimeError('Request not found')

        if request.status == api_requests.RequestStatus.CANCELLED:
            ctx.cancel()
            return True

        if fut.done():
            try:
                result = await fut
                api_requests.set_request_succeeded(request_id, result)
            except asyncio.CancelledError:
                # The task is cancelled by ctx.cancel(), where the status
                # should already be set to CANCELLED.
                pass
            except Exception as e:  # pylint: disable=broad-except
                ctx.redirect_log(original_output)
                api_requests.set_request_failed(request_id, e)
                logger.error(f'Request {request_id} failed due to '
                             f'{common_utils.format_exception(e)}')
            return True
        return False

    try:
        while True:
            res = await poll_task(request.request_id)
            if res:
                break
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        # Current coroutine is cancelled due to client disconnect, set the
        # request status for consistency.
        api_requests.set_request_cancelled(request.request_id)
        pass
    # pylint: disable=broad-except
    except (Exception, KeyboardInterrupt, SystemExit) as e:
        # Handle any other error
        ctx.redirect_log(original_output)
        ctx.cancel()
        api_requests.set_request_failed(request.request_id, e)
        logger.error(f'Request {request.request_id} interrupted due to '
                     f'unhandled exception: {common_utils.format_exception(e)}')
        raise


def prepare_request(
    request_id: str,
    request_name: str,
    request_body: payloads.RequestBody,
    func: Callable[P, Any],
    request_cluster_name: Optional[str] = None,
    schedule_type: api_requests.ScheduleType = (api_requests.ScheduleType.LONG),
    is_skypilot_system: bool = False,
) -> api_requests.Request:
    """Prepare a request for execution."""
    user_id = request_body.env_vars[constants.USER_ID_ENV_VAR]
    if is_skypilot_system:
        user_id = constants.SKYPILOT_SYSTEM_USER_ID
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
        raise exceptions.RequestAlreadyExistsError(
            f'Request {request_id} already exists.')

    request.log_path.touch()
    return request


def schedule_request(request_id: str,
                     request_name: str,
                     request_body: payloads.RequestBody,
                     func: Callable[P, Any],
                     request_cluster_name: Optional[str] = None,
                     ignore_return_value: bool = False,
                     schedule_type: api_requests.ScheduleType = (
                         api_requests.ScheduleType.LONG),
                     is_skypilot_system: bool = False,
                     precondition: Optional[preconditions.Precondition] = None,
                     retryable: bool = False) -> None:
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
    prepare_request(request_id, request_name, request_body, func,
                    request_cluster_name, schedule_type, is_skypilot_system)

    def enqueue():
        input_tuple = (request_id, ignore_return_value, retryable)
        logger.info(f'Queuing request: {request_id}')
        _get_queue(schedule_type).put(input_tuple)

    if precondition is not None:
        # Wait async to avoid blocking caller.
        precondition.wait_async(on_condition_met=enqueue)
    else:
        enqueue()


def start(
    config: server_config.ServerConfig
) -> Tuple[Optional[multiprocessing.Process], List[RequestWorker]]:
    """Start the request workers.

    Request workers run in background, schedule the requests and delegate the
    request execution to executor processes.

    Returns:
        A tuple of the queue server process and the list of request worker
        threads.
    """
    global queue_backend
    queue_backend = config.queue_backend
    queue_server = None
    # Setup the queues.
    if queue_backend == server_config.QueueBackend.MULTIPROCESSING:
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
        mp_queue.wait_for_queues_to_be_ready(queue_names,
                                             queue_server,
                                             port=port)
    elif queue_backend == server_config.QueueBackend.LOCAL:
        # No setup is needed for local queue backend.
        pass
    else:
        # Should be checked earlier, but just in case.
        raise RuntimeError(f'Invalid queue backend: {queue_backend}')

    logger.info('Request queues created')

    workers = []
    # Start a worker for long requests.
    long_worker = RequestWorker(schedule_type=api_requests.ScheduleType.LONG,
                                config=config.long_worker_config)
    long_worker.run_in_background()
    workers.append(long_worker)

    # Start a worker for short requests.
    short_worker = RequestWorker(schedule_type=api_requests.ScheduleType.SHORT,
                                 config=config.short_worker_config)
    short_worker.run_in_background()
    workers.append(short_worker)
    return queue_server, workers
