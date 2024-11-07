"""Executor for the requests."""
import enum
import functools
import multiprocessing
import os
import queue as queue_lib
import sys
import time
import traceback
import typing
from typing import Any, Callable, List, Optional, Union

from sky import sky_logging
from sky.api.requests import payloads
from sky.api.requests import requests
from sky.api.requests.queues import mp_queue
from sky.usage import usage_lib
from sky.utils import common
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import redis
    from redis import exceptions as redis_exceptions
else:
    redis = None
    redis_exceptions = None
    try:
        import redis
        from redis import exceptions as redis_exceptions
    except ImportError:
        pass

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


class ScheduleType(enum.Enum):
    """The schedule type for the requests."""
    BLOCKING = 'blocking'
    # Queue for requests that should be executed in non-blocking manner.
    NON_BLOCKING = 'non_blocking'

    @classmethod
    def blocking_queues(cls) -> List['ScheduleType']:
        return [cls.BLOCKING]

    @classmethod
    def non_blocking_queues(cls) -> List['ScheduleType']:
        return [cls.NON_BLOCKING]


class QueueBackend(enum.Enum):
    REDIS = 'redis'
    MULTIPROCESSING = 'multiprocessing'


def get_queue_backend() -> QueueBackend:
    if redis is None:
        return QueueBackend.MULTIPROCESSING
    try:
        assert redis is not None, 'Redis is not installed'
        queue = redis.Redis(host='localhost',
                            port=46581,
                            db=0,
                            socket_timeout=0.1)
        queue.ping()
        return QueueBackend.REDIS
    # pylint: disable=broad-except
    except (redis_exceptions.ConnectionError
            if redis_exceptions is not None else Exception):
        return QueueBackend.MULTIPROCESSING


class RequestQueue:
    """The queue for the requests, either redis or multiprocessing."""

    def __init__(self,
                 schedule_type: ScheduleType,
                 backend: Optional[QueueBackend] = None):
        self.name = schedule_type.value
        self.backend = backend
        self.queue: Union[queue_lib.Queue, 'redis.Redis']
        if backend == QueueBackend.MULTIPROCESSING:
            self.queue = mp_queue.get_queue(self.name)
        else:
            assert redis is not None, 'Redis is not installed'
            self.queue = redis.Redis(host='localhost',
                                     port=46581,
                                     db=0,
                                     socket_timeout=0.1)

    def put(self, obj: Any):
        if self.backend == QueueBackend.REDIS:
            assert isinstance(self.queue, redis.Redis), 'Redis is not installed'
            self.queue.lpush(self.name, obj)
        else:
            self.queue.put(obj)  # type: ignore

    def get(self):
        if self.backend == QueueBackend.REDIS:
            assert isinstance(self.queue, redis.Redis), 'Redis is not installed'
            return self.queue.rpop(self.name)
        else:
            try:
                return self.queue.get(block=False)
            except queue_lib.Empty:
                return None

    def __len__(self):
        # TODO(zhwu): we should autoscale based on the queue length.
        if isinstance(self.queue, redis.Redis):
            return self.queue.llen(self.name)
        else:
            return self.queue.qsize()


queue_backend = get_queue_backend()


@functools.lru_cache(maxsize=None)
def _get_queue(schedule_type: ScheduleType) -> RequestQueue:
    return RequestQueue(schedule_type, backend=queue_backend)


def _wrapper(request_id: str, ignore_return_value: bool):
    """Wrapper for a request task."""

    def redirect_output(file):
        """Redirect stdout and stderr to the log file."""
        fd = file.fileno()  # Get the file descriptor from the file object
        # Store copies of the original stdout and stderr file descriptors
        original_stdout = os.dup(sys.stdout.fileno())
        original_stderr = os.dup(sys.stderr.fileno())

        # Copy this fd to stdout and stderr
        os.dup2(fd, sys.stdout.fileno())
        os.dup2(fd, sys.stderr.fileno())
        return original_stdout, original_stderr

    def restore_output(original_stdout, original_stderr):
        """Restore stdout and stderr to their original file descriptors. """
        os.dup2(original_stdout, sys.stdout.fileno())
        os.dup2(original_stderr, sys.stderr.fileno())

        # Close the duplicate file descriptors
        os.close(original_stdout)
        os.close(original_stderr)

    pid = multiprocessing.current_process().pid
    logger.info(f'Running request {request_id} with pid {pid}')
    with requests.update_request(request_id) as request_task:
        assert request_task is not None, request_id
        log_path = request_task.log_path
        request_task.pid = pid
        request_task.status = requests.RequestStatus.RUNNING
        func = request_task.entrypoint
        request_body = request_task.request_body

    with log_path.open('w', encoding='utf-8') as f:
        # Store copies of the original stdout and stderr file descriptors
        original_stdout, original_stderr = redirect_output(f)
        try:
            os.environ.update(request_body.env_vars)
            # Force color to be enabled.
            os.environ['CLICOLOR_FORCE'] = '1'
            common.reload()
            return_value = func(**request_body.to_kwargs())
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.enable_traceback():
                stacktrace = traceback.format_exc()
            setattr(e, 'stacktrace', stacktrace)
            usage_lib.store_exception(e)
            with requests.update_request(request_id) as request_task:
                assert request_task is not None, request_id
                request_task.status = requests.RequestStatus.FAILED
                request_task.set_error(e)
            restore_output(original_stdout, original_stderr)
            logger.info(f'Request {request_id} failed due to {e}')
            return_value = None
        else:
            with requests.update_request(request_id) as request_task:
                assert request_task is not None, request_id
                request_task.status = requests.RequestStatus.SUCCEEDED
                if not ignore_return_value:
                    request_task.set_return_value(return_value)
            restore_output(original_stdout, original_stderr)
            logger.info(f'Request {request_id} finished')

        # Clean up cluster table.
        with requests.update_request(request_id) as request_task:
            assert request_task is not None, request_id
            if request_task.request_body.cluster_name and request_task.status in (
                    requests.RequestStatus.ABORTED,
                    requests.RequestStatus.FAILED,
                    requests.RequestStatus.SUCCEEDED):
                requests.remove_cluster_request(
                    request_task.request_body.cluster_name, request_id)
        return return_value


def schedule_request(request_id: str,
                     request_name: str,
                     request_body: payloads.RequestBody,
                     func: Callable[P, Any],
                     ignore_return_value: bool = False,
                     schedule_type: ScheduleType = ScheduleType.BLOCKING):
    """Enqueue a request to the request queue."""
    request = requests.Request(request_id=request_id,
                               name=request_name,
                               entrypoint=func,
                               request_body=request_body,
                               status=requests.RequestStatus.PENDING,
                               created_at=time.time())

    if not requests.create_if_not_exists(request):
        logger.debug(f'Request {request_id} already exists.')
        return

    request.log_path.touch()
    input_tuple = (request_id, ignore_return_value)
    _get_queue(schedule_type).put(input_tuple)


def request_worker(worker_id: int, non_blocking: bool = False):
    """Worker for the requests."""
    logger.info(f'Request worker {worker_id} -- started with pid '
                f'{multiprocessing.current_process().pid}')
    if non_blocking:
        queue_types = ScheduleType.non_blocking_queues()
    else:
        queue_types = ScheduleType.blocking_queues()
    while True:
        for queue_type in queue_types:
            request = _get_queue(queue_type).get()
            if request is not None:
                break
        if request is None:
            time.sleep(.1)
            continue
        request_id, ignore_return_value = request
        request = requests.get_request(request_id)
        if request.status == requests.RequestStatus.ABORTED:
            continue
        logger.info(
            f'Request worker {worker_id} -- running request: {request_id}')
        # Start additional process to run the request, so that it can be aborted
        # when requested by a user.
        process = multiprocessing.Process(target=_wrapper,
                                          args=(request_id,
                                                ignore_return_value))
        process.start()

        if queue_type == ScheduleType.BLOCKING:
            # Wait for the request to finish.
            try:
                process.join()
            except Exception as e:  # pylint: disable=broad-except
                logger.error(
                    f'Request worker {worker_id} -- request {request_id} '
                    f'failed: {e}')
            logger.info(
                f'Request worker {worker_id} -- request {request_id} finished')
        else:
            # Non-blocking requests are handled by the non-blocking worker.
            logger.info(
                f'Request worker {worker_id} -- request {request_id} submitted')


def start(num_queue_workers: int = 1) -> List[multiprocessing.Process]:
    """Start the request workers."""
    # Setup the queues.
    if queue_backend == QueueBackend.MULTIPROCESSING:
        logger.info('Creating shared request queues')
        mp_queue.create_mp_queues(
            [schedule_type.value for schedule_type in ScheduleType])
    logger.info('Request queues created')

    # Wait for the queues to be created. This is necessary to avoid request
    # workers to be refused by the connection to the queue.
    time.sleep(2)

    workers = []
    for worker_id in range(num_queue_workers):
        worker = multiprocessing.Process(target=request_worker,
                                         args=(worker_id,))
        logger.info(f'Starting request worker: {worker_id}')
        worker.start()
        workers.append(worker)

    # Start a non-blocking worker.
    worker = multiprocessing.Process(target=request_worker, args=(-1, True))
    logger.info('Starting non-blocking request worker')
    worker.start()
    workers.append(worker)
    return workers
