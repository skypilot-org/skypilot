"""Utilities for REST API."""
import asyncio
import atexit
import contextlib
import dataclasses
import enum
import functools
import os
import pathlib
import shutil
import signal
import sqlite3
import threading
import time
import traceback
from typing import (Any, Callable, Dict, Generator, List, NamedTuple, Optional,
                    Tuple)
import uuid

import anyio
import colorama
import filelock
import orjson

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.metrics import utils as metrics_lib
from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.server import daemons
from sky.server.requests import payloads
from sky.server.requests.serializers import decoders
from sky.server.requests.serializers import encoders
from sky.server.requests.serializers import return_value_serializers
from sky.utils import asyncio_utils
from sky.utils import common_utils
from sky.utils import ux_utils
from sky.utils.db import db_utils

logger = sky_logging.init_logger(__name__)

# Tables in task.db.
REQUEST_TABLE = 'requests'
COL_CLUSTER_NAME = 'cluster_name'
COL_USER_ID = 'user_id'
COL_STATUS_MSG = 'status_msg'
COL_SHOULD_RETRY = 'should_retry'
COL_FINISHED_AT = 'finished_at'
REQUEST_LOG_PATH_PREFIX = '~/sky_logs/api_server/requests'

DEFAULT_REQUESTS_RETENTION_HOURS = 24  # 1 day

# TODO(zhwu): For scalability, there are several TODOs:
# [x] Have a way to queue requests.
# [ ] Move logs to persistent place.
# [ ] Deploy API server in a autoscaling fashion.


class RequestStatus(enum.Enum):
    """The status of a request."""

    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'

    def __gt__(self, other):
        return (list(RequestStatus).index(self) >
                list(RequestStatus).index(other))

    def colored_str(self):
        color = _STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'

    @classmethod
    def finished_status(cls) -> List['RequestStatus']:
        return [cls.SUCCEEDED, cls.FAILED, cls.CANCELLED]


_STATUS_TO_COLOR = {
    RequestStatus.PENDING: colorama.Fore.BLUE,
    RequestStatus.RUNNING: colorama.Fore.GREEN,
    RequestStatus.SUCCEEDED: colorama.Fore.GREEN,
    RequestStatus.FAILED: colorama.Fore.RED,
    RequestStatus.CANCELLED: colorama.Fore.WHITE,
}

REQUEST_COLUMNS = [
    'request_id',
    'name',
    'entrypoint',
    'request_body',
    'status',
    'return_value',
    'error',
    'pid',
    'created_at',
    COL_CLUSTER_NAME,
    'schedule_type',
    COL_USER_ID,
    COL_STATUS_MSG,
    COL_SHOULD_RETRY,
    COL_FINISHED_AT,
]


class ScheduleType(enum.Enum):
    """The schedule type for the requests."""
    LONG = 'long'
    # Queue for requests that should be executed quickly for a quick response.
    SHORT = 'short'


@dataclasses.dataclass
class Request:
    """A SkyPilot API request."""

    request_id: str
    name: str
    entrypoint: Callable
    request_body: payloads.RequestBody
    status: RequestStatus
    created_at: float
    user_id: str
    return_value: Any = None
    error: Optional[Dict[str, Any]] = None
    # The pid of the request worker that is(was) running this request.
    pid: Optional[int] = None
    schedule_type: ScheduleType = ScheduleType.LONG
    # Resources the request operates on.
    cluster_name: Optional[str] = None
    # Status message of the request, indicates the reason of current status.
    status_msg: Optional[str] = None
    # Whether the request should be retried.
    should_retry: bool = False
    # When the request finished.
    finished_at: Optional[float] = None

    @property
    def log_path(self) -> pathlib.Path:
        log_path_prefix = pathlib.Path(
            REQUEST_LOG_PATH_PREFIX).expanduser().absolute()
        log_path_prefix.mkdir(parents=True, exist_ok=True)
        log_path = (log_path_prefix / self.request_id).with_suffix('.log')
        return log_path

    def set_error(self, error: BaseException) -> None:
        """Set the error."""
        # TODO(zhwu): pickle.dump does not work well with custom exceptions if
        # it has more than 1 arguments.
        serialized = exceptions.serialize_exception(error)
        self.error = {
            'object': encoders.pickle_and_encode(serialized),
            'type': type(error).__name__,
            'message': str(error),
        }

    def get_error(self) -> Optional[Dict[str, Any]]:
        """Get the error."""
        if self.error is None:
            return None
        unpickled = decoders.decode_and_unpickle(self.error['object'])
        deserialized = exceptions.deserialize_exception(unpickled)
        return {
            'object': deserialized,
            'type': self.error['type'],
            'message': self.error['message'],
        }

    def set_return_value(self, return_value: Any) -> None:
        """Set the return value."""
        self.return_value = encoders.get_encoder(self.name)(return_value)

    def get_return_value(self) -> Any:
        """Get the return value."""
        return decoders.get_decoder(self.name)(self.return_value)

    @classmethod
    def from_row(cls, row: Tuple[Any, ...]) -> 'Request':
        content = dict(zip(REQUEST_COLUMNS, row))
        return cls.decode(payloads.RequestPayload(**content))

    def to_row(self) -> Tuple[Any, ...]:
        payload = self.encode()
        row = []
        for k in REQUEST_COLUMNS:
            row.append(getattr(payload, k))
        return tuple(row)

    def readable_encode(self) -> payloads.RequestPayload:
        """Serialize the SkyPilot API request for display purposes.

        This function should be called on the server side to serialize the
        request body into human readable format, e.g., the entrypoint should
        be a string, and the pid, error, or return value are not needed.

        The returned value will then be displayed on the client side in request
        table.

        We do not use `encode` for display to avoid a large amount of data being
        sent to the client side, especially for the request table could include
        all the requests.
        """
        assert isinstance(self.request_body,
                          payloads.RequestBody), (self.name, self.request_body)
        user = global_user_state.get_user(self.user_id)
        user_name = user.name if user is not None else None
        return payloads.RequestPayload(
            request_id=self.request_id,
            name=self.name,
            entrypoint=self.entrypoint.__name__,
            request_body=self.request_body.model_dump_json(),
            status=self.status.value,
            return_value=orjson.dumps(None).decode('utf-8'),
            error=orjson.dumps(None).decode('utf-8'),
            pid=None,
            created_at=self.created_at,
            schedule_type=self.schedule_type.value,
            user_id=self.user_id,
            user_name=user_name,
            cluster_name=self.cluster_name,
            status_msg=self.status_msg,
            should_retry=self.should_retry,
            finished_at=self.finished_at,
        )

    def encode(self) -> payloads.RequestPayload:
        """Serialize the SkyPilot API request."""
        assert isinstance(self.request_body,
                          payloads.RequestBody), (self.name, self.request_body)
        try:
            # Use version-aware serializer to handle backward compatibility
            # for old clients that don't recognize new fields.
            serializer = return_value_serializers.get_serializer(self.name)
            return payloads.RequestPayload(
                request_id=self.request_id,
                name=self.name,
                entrypoint=encoders.pickle_and_encode(self.entrypoint),
                request_body=encoders.pickle_and_encode(self.request_body),
                status=self.status.value,
                return_value=serializer(self.return_value),
                error=orjson.dumps(self.error).decode('utf-8'),
                pid=self.pid,
                created_at=self.created_at,
                schedule_type=self.schedule_type.value,
                user_id=self.user_id,
                cluster_name=self.cluster_name,
                status_msg=self.status_msg,
                should_retry=self.should_retry,
                finished_at=self.finished_at,
            )
        except (TypeError, ValueError) as e:
            # The error is unexpected, so we don't suppress the stack trace.
            logger.error(
                f'Error encoding: {e}\n'
                f'  {self.request_id}\n'
                f'  {self.name}\n'
                f'  {self.request_body}\n'
                f'  {self.return_value}\n'
                f'  {self.created_at}\n',
                exc_info=e)
            raise

    @classmethod
    def decode(cls, payload: payloads.RequestPayload) -> 'Request':
        """Deserialize the SkyPilot API request."""
        try:
            return cls(
                request_id=payload.request_id,
                name=payload.name,
                entrypoint=decoders.decode_and_unpickle(payload.entrypoint),
                request_body=decoders.decode_and_unpickle(payload.request_body),
                status=RequestStatus(payload.status),
                return_value=orjson.loads(payload.return_value),
                error=orjson.loads(payload.error),
                pid=payload.pid,
                created_at=payload.created_at,
                schedule_type=ScheduleType(payload.schedule_type),
                user_id=payload.user_id,
                cluster_name=payload.cluster_name,
                status_msg=payload.status_msg,
                should_retry=payload.should_retry,
                finished_at=payload.finished_at,
            )
        except (TypeError, ValueError) as e:
            logger.error(
                f'Error decoding: {e}\n'
                f'  {payload.request_id}\n'
                f'  {payload.name}\n'
                f'  {payload.entrypoint}\n'
                f'  {payload.request_body}\n'
                f'  {payload.created_at}\n',
                exc_info=e)
            # The error is unexpected, so we don't suppress the stack trace.
            raise


def get_new_request_id() -> str:
    """Get a new request ID."""
    return str(uuid.uuid4())


def encode_requests(requests: List[Request]) -> List[payloads.RequestPayload]:
    """Serialize the SkyPilot API request for display purposes.

        This function should be called on the server side to serialize the
        request body into human readable format, e.g., the entrypoint should
        be a string, and the pid, error, or return value are not needed.

        The returned value will then be displayed on the client side in request
        table.

        We do not use `encode` for display to avoid a large amount of data being
        sent to the client side, especially for the request table could include
        all the requests.
        """
    encoded_requests = []
    all_users = global_user_state.get_all_users()
    all_users_map = {user.id: user.name for user in all_users}
    for request in requests:
        if request.request_body is not None:
            assert isinstance(request.request_body,
                              payloads.RequestBody), (request.name,
                                                      request.request_body)
        user_name = all_users_map.get(request.user_id)
        payload = payloads.RequestPayload(
            request_id=request.request_id,
            name=request.name,
            entrypoint=request.entrypoint.__name__
            if request.entrypoint is not None else '',
            request_body=request.request_body.model_dump_json()
            if request.request_body is not None else
            orjson.dumps(None).decode('utf-8'),
            status=request.status.value,
            return_value=orjson.dumps(None).decode('utf-8'),
            error=orjson.dumps(None).decode('utf-8'),
            pid=None,
            created_at=request.created_at,
            schedule_type=request.schedule_type.value,
            user_id=request.user_id,
            user_name=user_name,
            cluster_name=request.cluster_name,
            status_msg=request.status_msg,
            should_retry=request.should_retry,
            finished_at=request.finished_at,
        )
        encoded_requests.append(payload)
    return encoded_requests


def _update_request_row_fields(
        row: Tuple[Any, ...],
        fields: Optional[List[str]] = None) -> Tuple[Any, ...]:
    """Update the request row fields."""
    if not fields:
        return row

    # Convert tuple to dictionary for easier manipulation
    content = dict(zip(fields, row))

    # Required fields in RequestPayload
    if 'request_id' not in fields:
        content['request_id'] = ''
    if 'name' not in fields:
        content['name'] = ''
    if 'entrypoint' not in fields:
        content['entrypoint'] = server_constants.EMPTY_PICKLED_VALUE
    if 'request_body' not in fields:
        content['request_body'] = server_constants.EMPTY_PICKLED_VALUE
    if 'status' not in fields:
        content['status'] = RequestStatus.PENDING.value
    if 'created_at' not in fields:
        content['created_at'] = 0
    if 'user_id' not in fields:
        content['user_id'] = ''
    if 'return_value' not in fields:
        content['return_value'] = orjson.dumps(None).decode('utf-8')
    if 'error' not in fields:
        content['error'] = orjson.dumps(None).decode('utf-8')
    if 'schedule_type' not in fields:
        content['schedule_type'] = ScheduleType.SHORT.value
    # Optional fields in RequestPayload
    if 'pid' not in fields:
        content['pid'] = None
    if 'cluster_name' not in fields:
        content['cluster_name'] = None
    if 'status_msg' not in fields:
        content['status_msg'] = None
    if 'should_retry' not in fields:
        content['should_retry'] = False
    if 'finished_at' not in fields:
        content['finished_at'] = None

    # Convert back to tuple in the same order as REQUEST_COLUMNS
    return tuple(content[col] for col in REQUEST_COLUMNS)


def create_table(cursor, conn):
    # Enable WAL mode to avoid locking issues.
    # See: issue #1441 and PR #1509
    # https://github.com/microsoft/WSL/issues/2395
    # TODO(romilb): We do not enable WAL for WSL because of known issue in WSL.
    #  This may cause the database locked problem from WSL issue #1441.
    if not common_utils.is_wsl():
        try:
            cursor.execute('PRAGMA journal_mode=WAL')
        except sqlite3.OperationalError as e:
            if 'database is locked' not in str(e):
                raise
            # If the database is locked, it is OK to continue, as the WAL mode
            # is not critical and is likely to be enabled by other processes.

    # Table for Requests
    cursor.execute(f"""\
        CREATE TABLE IF NOT EXISTS {REQUEST_TABLE} (
        request_id TEXT PRIMARY KEY,
        name TEXT,
        entrypoint TEXT,
        request_body TEXT,
        status TEXT,
        created_at REAL,
        return_value TEXT,
        error BLOB,
        pid INTEGER,
        {COL_CLUSTER_NAME} TEXT,
        schedule_type TEXT,
        {COL_USER_ID} TEXT,
        {COL_STATUS_MSG} TEXT,
        {COL_SHOULD_RETRY} INTEGER,
        {COL_FINISHED_AT} REAL
        )""")

    db_utils.add_column_to_table(cursor, conn, REQUEST_TABLE, COL_STATUS_MSG,
                                 'TEXT')
    db_utils.add_column_to_table(cursor, conn, REQUEST_TABLE, COL_SHOULD_RETRY,
                                 'INTEGER')
    db_utils.add_column_to_table(cursor, conn, REQUEST_TABLE, COL_FINISHED_AT,
                                 'REAL')

    # Add an index on (status, name) to speed up queries
    # that filter on these columns.
    cursor.execute(f"""\
        CREATE INDEX IF NOT EXISTS status_name_idx ON {REQUEST_TABLE} (status, name) WHERE status IN ('PENDING', 'RUNNING');
    """)
    # Add an index on cluster_name to speed up queries
    # that filter on this column.
    cursor.execute(f"""\
        CREATE INDEX IF NOT EXISTS cluster_name_idx ON {REQUEST_TABLE} ({COL_CLUSTER_NAME}) WHERE status IN ('PENDING', 'RUNNING');
    """)
    # Add an index on created_at to speed up queries that sort on this column.
    cursor.execute(f"""\
        CREATE INDEX IF NOT EXISTS created_at_idx ON {REQUEST_TABLE} (created_at);
    """)


_DB = None
_init_db_lock = threading.Lock()


def _init_db_within_lock():
    global _DB
    if _DB is None:
        db_path = os.path.expanduser(
            server_constants.API_SERVER_REQUEST_DB_PATH)
        pathlib.Path(db_path).parents[0].mkdir(parents=True, exist_ok=True)
        _DB = db_utils.SQLiteConn(db_path, create_table)


def init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if _DB is not None:
            return func(*args, **kwargs)
        with _init_db_lock:
            _init_db_within_lock()
        return func(*args, **kwargs)

    return wrapper


def init_db_async(func):
    """Async version of init_db."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        if _DB is not None:
            return await func(*args, **kwargs)
        # If _DB is not initialized, init_db_async will be blocked if there
        # is a thread initializing _DB, this is fine since it occurs on process
        # startup.
        with _init_db_lock:
            _init_db_within_lock()
        return await func(*args, **kwargs)

    return wrapper


def reset_db_and_logs():
    """Create the database."""
    logger.debug('clearing local API server database')
    server_common.clear_local_api_server_database()
    logger.debug(
        f'clearing local API server logs directory at {REQUEST_LOG_PATH_PREFIX}'
    )
    shutil.rmtree(pathlib.Path(REQUEST_LOG_PATH_PREFIX).expanduser(),
                  ignore_errors=True)
    logger.debug('clearing local API server client directory at '
                 f'{server_common.API_SERVER_CLIENT_DIR.expanduser()}')
    shutil.rmtree(server_common.API_SERVER_CLIENT_DIR.expanduser(),
                  ignore_errors=True)
    with _init_db_lock:
        _init_db_within_lock()
    assert _DB is not None
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute('SELECT sqlite_version()')
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError('Failed to get SQLite version')
        version_str = row[0]
        version_parts = version_str.split('.')
        assert len(version_parts) >= 2, \
            f'Invalid version string: {version_str}'
        major, minor = int(version_parts[0]), int(version_parts[1])
        # SQLite 3.35.0+ supports RETURNING statements.
        # 3.35.0 was released in March 2021.
        if not ((major > 3) or (major == 3 and minor >= 35)):
            raise RuntimeError(
                f'SQLite version {version_str} is not supported. '
                'Please upgrade to SQLite 3.35.0 or later.')


def request_lock_path(request_id: str) -> str:
    lock_path = os.path.expanduser(REQUEST_LOG_PATH_PREFIX)
    os.makedirs(lock_path, exist_ok=True)
    return os.path.join(lock_path, f'.{request_id}.lock')


def kill_cluster_requests(cluster_name: str, exclude_request_name: str):
    """Kill all pending and running requests for a cluster.

    Args:
        cluster_name: the name of the cluster.
        exclude_request_names: exclude requests with these names. This is to
            prevent killing the caller request.
    """
    request_ids = [
        request_task.request_id
        for request_task in get_request_tasks(req_filter=RequestTaskFilter(
            status=[RequestStatus.PENDING, RequestStatus.RUNNING],
            exclude_request_names=[exclude_request_name],
            cluster_names=[cluster_name],
            fields=['request_id']))
    ]
    _kill_requests(request_ids)


def kill_requests(request_ids: Optional[List[str]] = None,
                  user_id: Optional[str] = None) -> List[str]:
    """Kill requests with a given request ID prefix."""
    expanded_request_ids: Optional[List[str]] = None
    if request_ids is not None:
        expanded_request_ids = []
        for request_id in request_ids:
            request_tasks = get_requests_with_prefix(request_id,
                                                     fields=['request_id'])
            if request_tasks is None or len(request_tasks) == 0:
                continue
            if len(request_tasks) > 1:
                raise ValueError(f'Multiple requests found for '
                                 f'request ID prefix: {request_id}')
            expanded_request_ids.append(request_tasks[0].request_id)
    return _kill_requests(request_ids=expanded_request_ids, user_id=user_id)


# needed for backward compatibility. Remove by v0.10.7 or v0.12.0
# and rename kill_requests to kill_requests_with_prefix.
kill_requests_with_prefix = kill_requests


def _should_kill_request(request_id: str,
                         request_record: Optional[Request]) -> bool:
    if request_record is None:
        logger.debug(f'No request ID {request_id}')
        return False
    # Skip internal requests. The internal requests are scheduled with
    # request_id in range(len(INTERNAL_REQUEST_EVENTS)).
    if request_record.request_id in set(
            event.id for event in daemons.INTERNAL_REQUEST_DAEMONS):
        return False
    if request_record.status > RequestStatus.RUNNING:
        logger.debug(f'Request {request_id} already finished')
        return False
    return True


def _kill_requests(request_ids: Optional[List[str]] = None,
                   user_id: Optional[str] = None) -> List[str]:
    """Kill a SkyPilot API request and set its status to cancelled.

    Args:
        request_ids: The request IDs to kill. If None, all requests for the
            user are killed.
        user_id: The user ID to kill requests for. If None, all users are
            killed.

    Returns:
        A list of request IDs that were cancelled.
    """
    if request_ids is None:
        request_ids = [
            request_task.request_id
            for request_task in get_request_tasks(req_filter=RequestTaskFilter(
                status=[RequestStatus.PENDING, RequestStatus.RUNNING],
                # Avoid cancelling the cancel request itself.
                exclude_request_names=['sky.api_cancel'],
                user_id=user_id,
                fields=['request_id']))
        ]
    cancelled_request_ids = []
    for request_id in request_ids:
        with update_request(request_id) as request_record:
            if not _should_kill_request(request_id, request_record):
                continue
            if request_record.pid is not None:
                logger.debug(f'Killing request process {request_record.pid}')
                # Use SIGTERM instead of SIGKILL:
                # - The executor can handle SIGTERM gracefully
                # - After SIGTERM, the executor can reuse the request process
                #   for other requests, avoiding the overhead of forking a new
                #   process for each request.
                os.kill(request_record.pid, signal.SIGTERM)
            request_record.status = RequestStatus.CANCELLED
            request_record.finished_at = time.time()
            cancelled_request_ids.append(request_id)
    return cancelled_request_ids


@init_db_async
@asyncio_utils.shield
async def kill_request_async(request_id: str) -> bool:
    """Kill a SkyPilot API request and set its status to cancelled.

    Returns:
        True if the request was killed, False otherwise.
    """
    async with filelock.AsyncFileLock(request_lock_path(request_id)):
        request = await _get_request_no_lock_async(request_id)
        if not _should_kill_request(request_id, request):
            return False
        assert request is not None
        if request.pid is not None:
            logger.debug(f'Killing request process {request.pid}')
            # Use SIGTERM instead of SIGKILL:
            # - The executor can handle SIGTERM gracefully
            # - After SIGTERM, the executor can reuse the request process
            #   for other requests, avoiding the overhead of forking a new
            #   process for each request.
            os.kill(request.pid, signal.SIGTERM)
        request.status = RequestStatus.CANCELLED
        request.finished_at = time.time()
        await _add_or_update_request_no_lock_async(request)
    return True


@contextlib.contextmanager
@init_db
@metrics_lib.time_me
def update_request(request_id: str) -> Generator[Optional[Request], None, None]:
    """Get and update a SkyPilot API request."""
    # Acquire the lock to avoid race conditions between multiple request
    # operations, e.g. execute and cancel.
    with filelock.FileLock(request_lock_path(request_id)):
        request = _get_request_no_lock(request_id)
        yield request
        if request is not None:
            _add_or_update_request_no_lock(request)


@init_db_async
@metrics_lib.time_me
@asyncio_utils.shield
async def update_status_async(request_id: str, status: RequestStatus) -> None:
    """Update the status of a request"""
    async with filelock.AsyncFileLock(request_lock_path(request_id)):
        request = await _get_request_no_lock_async(request_id)
        if request is not None:
            request.status = status
            await _add_or_update_request_no_lock_async(request)


@init_db_async
@metrics_lib.time_me
@asyncio_utils.shield
async def update_status_msg_async(request_id: str, status_msg: str) -> None:
    """Update the status message of a request"""
    async with filelock.AsyncFileLock(request_lock_path(request_id)):
        request = await _get_request_no_lock_async(request_id)
        if request is not None:
            request.status_msg = status_msg
            await _add_or_update_request_no_lock_async(request)


def _get_request_no_lock(
        request_id: str,
        fields: Optional[List[str]] = None) -> Optional[Request]:
    """Get a SkyPilot API request."""
    assert _DB is not None
    columns_str = ', '.join(REQUEST_COLUMNS)
    if fields:
        columns_str = ', '.join(fields)
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute((f'SELECT {columns_str} FROM {REQUEST_TABLE} '
                        'WHERE request_id LIKE ?'), (request_id + '%',))
        row = cursor.fetchone()
        if row is None:
            return None
    if fields:
        row = _update_request_row_fields(row, fields)
    return Request.from_row(row)


async def _get_request_no_lock_async(
        request_id: str,
        fields: Optional[List[str]] = None) -> Optional[Request]:
    """Async version of _get_request_no_lock."""
    assert _DB is not None
    columns_str = ', '.join(REQUEST_COLUMNS)
    if fields:
        columns_str = ', '.join(fields)
    async with _DB.execute_fetchall_async(
        (f'SELECT {columns_str} FROM {REQUEST_TABLE} '
         'WHERE request_id LIKE ?'), (request_id + '%',)) as rows:
        row = rows[0] if rows else None
        if row is None:
            return None
    if fields:
        row = _update_request_row_fields(row, fields)
    return Request.from_row(row)


@init_db_async
@metrics_lib.time_me
async def get_latest_request_id_async() -> Optional[str]:
    """Get the latest request ID."""
    assert _DB is not None
    async with _DB.execute_fetchall_async(
        (f'SELECT request_id FROM {REQUEST_TABLE} '
         'ORDER BY created_at DESC LIMIT 1')) as rows:
        return rows[0][0] if rows else None


@init_db
@metrics_lib.time_me
def get_request(request_id: str,
                fields: Optional[List[str]] = None) -> Optional[Request]:
    """Get a SkyPilot API request."""
    with filelock.FileLock(request_lock_path(request_id)):
        return _get_request_no_lock(request_id, fields)


@init_db_async
@metrics_lib.time_me_async
@asyncio_utils.shield
async def get_request_async(
        request_id: str,
        fields: Optional[List[str]] = None) -> Optional[Request]:
    """Async version of get_request."""
    # TODO(aylei): figure out how to remove FileLock here to avoid the overhead
    async with filelock.AsyncFileLock(request_lock_path(request_id)):
        return await _get_request_no_lock_async(request_id, fields)


@init_db
@metrics_lib.time_me
def get_requests_with_prefix(
        request_id_prefix: str,
        fields: Optional[List[str]] = None) -> Optional[List[Request]]:
    """Get requests with a given request ID prefix."""
    assert _DB is not None
    if fields:
        columns_str = ', '.join(fields)
    else:
        columns_str = ', '.join(REQUEST_COLUMNS)
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute((f'SELECT {columns_str} FROM {REQUEST_TABLE} '
                        'WHERE request_id LIKE ?'), (request_id_prefix + '%',))
        rows = cursor.fetchall()
        if not rows:
            return None
        if fields:
            rows = [_update_request_row_fields(row, fields) for row in rows]
        return [Request.from_row(row) for row in rows]


@init_db_async
@metrics_lib.time_me_async
@asyncio_utils.shield
async def get_requests_async_with_prefix(
        request_id_prefix: str,
        fields: Optional[List[str]] = None) -> Optional[List[Request]]:
    """Async version of get_request_with_prefix."""
    assert _DB is not None
    if fields:
        columns_str = ', '.join(fields)
    else:
        columns_str = ', '.join(REQUEST_COLUMNS)
    async with _DB.execute_fetchall_async(
        (f'SELECT {columns_str} FROM {REQUEST_TABLE} '
         'WHERE request_id LIKE ?'), (request_id_prefix + '%',)) as rows:
        if not rows:
            return None
        if fields:
            rows = [_update_request_row_fields(row, fields) for row in rows]
        return [Request.from_row(row) for row in rows]


class StatusWithMsg(NamedTuple):
    status: RequestStatus
    status_msg: Optional[str] = None


@init_db_async
@metrics_lib.time_me_async
async def get_request_status_async(
    request_id: str,
    include_msg: bool = False,
) -> Optional[StatusWithMsg]:
    """Get the status of a request.

    Args:
        request_id: The ID of the request.
        include_msg: Whether to include the status message.

    Returns:
        The status of the request. If the request is not found, returns
        None.
    """
    assert _DB is not None
    columns = 'status'
    if include_msg:
        columns += ', status_msg'
    sql = f'SELECT {columns} FROM {REQUEST_TABLE} WHERE request_id LIKE ?'
    async with _DB.execute_fetchall_async(sql, (request_id + '%',)) as rows:
        if rows is None or len(rows) == 0:
            return None
        status = RequestStatus(rows[0][0])
        status_msg = rows[0][1] if include_msg else None
        return StatusWithMsg(status, status_msg)


@init_db_async
@metrics_lib.time_me_async
@asyncio_utils.shield
async def create_if_not_exists_async(request: Request) -> bool:
    """Create a request if it does not exist, otherwise do nothing.

    Returns:
        True if a new request is created, False if the request already exists.
    """
    assert _DB is not None
    request_columns = ', '.join(REQUEST_COLUMNS)
    values_str = ', '.join(['?'] * len(REQUEST_COLUMNS))
    sql_statement = (
        f'INSERT INTO {REQUEST_TABLE} '
        f'({request_columns}) VALUES '
        f'({values_str}) ON CONFLICT(request_id) DO NOTHING RETURNING ROWID')
    request_row = request.to_row()
    if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
        logger.debug(f'Start creating request {request.request_id}')
    try:
        # Execute the SQL statement without getting the request lock.
        # The request lock is used to prevent racing with cancellation codepath,
        # but a request cannot be cancelled before it is created.
        row = await _DB.execute_get_returning_value_async(
            sql_statement, request_row)
    finally:
        if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
            logger.debug(f'End creating request {request.request_id}')
    return True if row else False


@dataclasses.dataclass
class RequestTaskFilter:
    """Filter for requests.

    Args:
        status: a list of statuses of the requests to filter on.
        cluster_names: a list of cluster names to filter requests on.
        exclude_request_names: a list of request names to exclude from results.
            Mutually exclusive with include_request_names.
        user_id: the user ID to filter requests on.
            If None, all users are included.
        include_request_names: a list of request names to filter on.
            Mutually exclusive with exclude_request_names.
        finished_before: if provided, only include requests finished before this
            timestamp.
        limit: the number of requests to show. If None, show all requests.

    Raises:
        ValueError: If both exclude_request_names and include_request_names are
            provided.
    """
    status: Optional[List[RequestStatus]] = None
    cluster_names: Optional[List[str]] = None
    user_id: Optional[str] = None
    exclude_request_names: Optional[List[str]] = None
    include_request_names: Optional[List[str]] = None
    finished_before: Optional[float] = None
    limit: Optional[int] = None
    fields: Optional[List[str]] = None
    sort: bool = False

    def __post_init__(self):
        if (self.exclude_request_names is not None and
                self.include_request_names is not None):
            raise ValueError(
                'Only one of exclude_request_names or include_request_names '
                'can be provided, not both.')

    def build_query(self) -> Tuple[str, List[Any]]:
        """Build the SQL query and filter parameters.

        Returns:
            A tuple of (SQL, SQL parameters).
        """
        filters = []
        filter_params: List[Any] = []
        if self.status is not None:
            status_list_str = ','.join(
                repr(status.value) for status in self.status)
            filters.append(f'status IN ({status_list_str})')
        if self.include_request_names is not None:
            request_names_str = ','.join(
                repr(name) for name in self.include_request_names)
            filters.append(f'name IN ({request_names_str})')
        if self.exclude_request_names is not None:
            exclude_request_names_str = ','.join(
                repr(name) for name in self.exclude_request_names)
            filters.append(f'name NOT IN ({exclude_request_names_str})')
        if self.cluster_names is not None:
            cluster_names_str = ','.join(
                repr(name) for name in self.cluster_names)
            filters.append(f'{COL_CLUSTER_NAME} IN ({cluster_names_str})')
        if self.user_id is not None:
            filters.append(f'{COL_USER_ID} = ?')
            filter_params.append(self.user_id)
        if self.finished_before is not None:
            filters.append('finished_at < ?')
            filter_params.append(self.finished_before)
        filter_str = ' AND '.join(filters)
        if filter_str:
            filter_str = f' WHERE {filter_str}'
        columns_str = ', '.join(REQUEST_COLUMNS)
        if self.fields:
            columns_str = ', '.join(self.fields)
        sort_str = ''
        if self.sort:
            sort_str = ' ORDER BY created_at DESC'
        query_str = (f'SELECT {columns_str} FROM {REQUEST_TABLE}{filter_str}'
                     f'{sort_str}')
        if self.limit is not None:
            query_str += f' LIMIT {self.limit}'
        return query_str, filter_params


@init_db
@metrics_lib.time_me
def get_request_tasks(req_filter: RequestTaskFilter) -> List[Request]:
    """Get a list of requests that match the given filters.

    Args:
        req_filter: the filter to apply to the requests. Refer to
            RequestTaskFilter for the details.
    """
    assert _DB is not None
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute(*req_filter.build_query())
        rows = cursor.fetchall()
        if rows is None:
            return []
    if req_filter.fields:
        rows = [
            _update_request_row_fields(row, req_filter.fields) for row in rows
        ]
    return [Request.from_row(row) for row in rows]


@init_db_async
@metrics_lib.time_me_async
async def get_request_tasks_async(
        req_filter: RequestTaskFilter) -> List[Request]:
    """Async version of get_request_tasks."""
    assert _DB is not None
    async with _DB.execute_fetchall_async(*req_filter.build_query()) as rows:
        if not rows:
            return []
    if req_filter.fields:
        rows = [
            _update_request_row_fields(row, req_filter.fields) for row in rows
        ]
    return [Request.from_row(row) for row in rows]


@init_db_async
@metrics_lib.time_me_async
async def get_api_request_ids_start_with(incomplete: str) -> List[str]:
    """Get a list of API request ids for shell completion."""
    assert _DB is not None
    # Prioritize alive requests (PENDING, RUNNING) over finished ones,
    # then order by creation time (newest first) within each category.
    async with _DB.execute_fetchall_async(
        f"""SELECT request_id FROM {REQUEST_TABLE}
                WHERE request_id LIKE ?
                ORDER BY
                    CASE
                        WHEN status IN ('PENDING', 'RUNNING') THEN 0
                        ELSE 1
                    END,
                    created_at DESC
                LIMIT 1000""", (f'{incomplete}%',)) as rows:
        if not rows:
            return []
    return [row[0] for row in rows]


_add_or_update_request_sql = (f'INSERT OR REPLACE INTO {REQUEST_TABLE} '
                              f'({", ".join(REQUEST_COLUMNS)}) VALUES '
                              f'({", ".join(["?"] * len(REQUEST_COLUMNS))})')


def _add_or_update_request_no_lock(request: Request):
    """Add or update a REST request into the database."""
    assert _DB is not None
    if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
        logger.debug(f'Start adding or updating request {request.request_id}')
    try:
        with _DB.conn:
            cursor = _DB.conn.cursor()
            cursor.execute(_add_or_update_request_sql, request.to_row())
    finally:
        if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
            logger.debug(f'End adding or updating request {request.request_id}')


async def _add_or_update_request_no_lock_async(request: Request):
    """Async version of _add_or_update_request_no_lock."""
    assert _DB is not None
    await _DB.execute_and_commit_async(_add_or_update_request_sql,
                                       request.to_row())


def set_exception_stacktrace(e: BaseException) -> None:
    with ux_utils.enable_traceback():
        stacktrace = traceback.format_exc()
    setattr(e, 'stacktrace', stacktrace)


def set_request_failed(request_id: str, e: BaseException) -> None:
    """Set a request to failed and populate the error message."""
    set_exception_stacktrace(e)
    with update_request(request_id) as request_task:
        assert request_task is not None, request_id
        request_task.status = RequestStatus.FAILED
        request_task.finished_at = time.time()
        request_task.set_error(e)


@init_db_async
@metrics_lib.time_me_async
@asyncio_utils.shield
async def set_request_failed_async(request_id: str, e: BaseException) -> None:
    """Set a request to failed and populate the error message."""
    set_exception_stacktrace(e)
    async with filelock.AsyncFileLock(request_lock_path(request_id)):
        request_task = await _get_request_no_lock_async(request_id)
        assert request_task is not None, request_id
        request_task.status = RequestStatus.FAILED
        request_task.finished_at = time.time()
        request_task.set_error(e)
        await _add_or_update_request_no_lock_async(request_task)


def set_request_succeeded(request_id: str, result: Optional[Any]) -> None:
    """Set a request to succeeded and populate the result."""
    with update_request(request_id) as request_task:
        assert request_task is not None, request_id
        request_task.status = RequestStatus.SUCCEEDED
        request_task.finished_at = time.time()
        if result is not None:
            request_task.set_return_value(result)


@init_db_async
@metrics_lib.time_me_async
@asyncio_utils.shield
async def set_request_succeeded_async(request_id: str,
                                      result: Optional[Any]) -> None:
    """Set a request to succeeded and populate the result."""
    async with filelock.AsyncFileLock(request_lock_path(request_id)):
        request_task = await _get_request_no_lock_async(request_id)
        assert request_task is not None, request_id
        request_task.status = RequestStatus.SUCCEEDED
        request_task.finished_at = time.time()
        if result is not None:
            request_task.set_return_value(result)
        await _add_or_update_request_no_lock_async(request_task)


@init_db_async
@metrics_lib.time_me_async
@asyncio_utils.shield
async def set_request_cancelled_async(request_id: str) -> None:
    """Set a pending or running request to cancelled."""
    async with filelock.AsyncFileLock(request_lock_path(request_id)):
        request_task = await _get_request_no_lock_async(request_id)
        assert request_task is not None, request_id
        # Already finished or cancelled.
        if request_task.status > RequestStatus.RUNNING:
            return
        request_task.finished_at = time.time()
        request_task.status = RequestStatus.CANCELLED
        await _add_or_update_request_no_lock_async(request_task)


@init_db
@metrics_lib.time_me
async def _delete_requests(request_ids: List[str]):
    """Clean up requests by their IDs."""
    id_list_str = ','.join(repr(request_id) for request_id in request_ids)
    assert _DB is not None
    if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
        logger.debug(f'Start deleting requests {request_ids}')
    try:
        await _DB.execute_and_commit_async(
            f'DELETE FROM {REQUEST_TABLE} WHERE request_id IN ({id_list_str})')
    finally:
        if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
            logger.debug(f'End deleting requests {request_ids}')


async def clean_finished_requests_with_retention(retention_seconds: int,
                                                 batch_size: int = 1000):
    """Clean up finished requests older than the retention period.

    This function removes old finished requests (SUCCEEDED, FAILED, CANCELLED)
    from the database and cleans up their associated log files.

    Args:
        retention_seconds: Requests older than this many seconds will be
            deleted.
        batch_size: batch delete 'batch_size' requests at a time to
            avoid using too much memory and once and to let each
            db query complete in a reasonable time. All stale
            requests older than the retention period will be deleted
            regardless of the batch size.
    """
    total_deleted = 0
    while True:
        reqs = await get_request_tasks_async(
            req_filter=RequestTaskFilter(status=RequestStatus.finished_status(),
                                         finished_before=time.time() -
                                         retention_seconds,
                                         limit=batch_size,
                                         fields=['request_id']))
        if len(reqs) == 0:
            break
        futs = []
        for req in reqs:
            # req.log_path is derived from request_id,
            # so it's ok to just grab the request_id in the above query.
            futs.append(
                asyncio.create_task(
                    anyio.Path(
                        req.log_path.absolute()).unlink(missing_ok=True)))
        await asyncio.gather(*futs)

        await _delete_requests([req.request_id for req in reqs])
        total_deleted += len(reqs)
        if len(reqs) < batch_size:
            break

    # To avoid leakage of the log file, logs must be deleted before the
    # request task in the database.
    logger.info(f'Cleaned up {total_deleted} finished requests '
                f'older than {retention_seconds} seconds')


async def requests_gc_daemon():
    """Garbage collect finished requests periodically."""
    while True:
        logger.info('Running requests GC daemon...')
        # Use the latest config.
        skypilot_config.reload_config()
        retention_seconds = skypilot_config.get_nested(
            ('api_server', 'requests_retention_hours'),
            DEFAULT_REQUESTS_RETENTION_HOURS) * 3600
        try:
            # Negative value disables the requests GC
            if retention_seconds >= 0:
                await clean_finished_requests_with_retention(retention_seconds)
        except asyncio.CancelledError:
            logger.info('Requests GC daemon cancelled')
            break
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error running requests GC daemon: {e}'
                         f'traceback: {traceback.format_exc()}')
        # Run the daemon at most once every hour to avoid too frequent
        # cleanup.
        await asyncio.sleep(max(retention_seconds, 3600))


def _cleanup():
    if _DB is not None:
        asyncio.run(_DB.close())


atexit.register(_cleanup)
