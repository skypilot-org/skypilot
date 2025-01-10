"""Utilities for REST API."""
import contextlib
import dataclasses
import enum
import functools
import json
import os
import pathlib
import shutil
import signal
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

import colorama
import filelock

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.server.requests import payloads
from sky.server.requests.serializers import decoders
from sky.server.requests.serializers import encoders
from sky.utils import common_utils
from sky.utils import db_utils

logger = sky_logging.init_logger(__name__)

# Tables in task.db.
REQUEST_TABLE = 'requests'
COL_CLUSTER_NAME = 'cluster_name'
COL_USER_ID = 'user_id'
REQUEST_LOG_PATH_PREFIX = '~/sky_logs/api_server/requests'

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
]


class ScheduleType(enum.Enum):
    """The schedule type for the requests."""
    BLOCKING = 'blocking'
    # Queue for requests that should be executed in non-blocking manner.
    NON_BLOCKING = 'non_blocking'


@dataclasses.dataclass
class RequestPayload:
    """The payload for the requests."""

    request_id: str
    name: str
    entrypoint: str
    request_body: str
    status: str
    created_at: float
    user_id: str
    return_value: str
    error: str
    pid: Optional[int]
    schedule_type: str
    user_name: Optional[str] = None


@dataclasses.dataclass
class Request:
    """A REST request."""

    request_id: str
    name: str
    entrypoint: Callable
    request_body: payloads.RequestBody
    status: RequestStatus
    created_at: float
    user_id: str
    return_value: Any = None
    error: Optional[Dict[str, Any]] = None
    pid: Optional[int] = None
    schedule_type: ScheduleType = ScheduleType.BLOCKING

    @property
    def log_path(self) -> pathlib.Path:
        log_path_prefix = pathlib.Path(
            REQUEST_LOG_PATH_PREFIX).expanduser().absolute()
        log_path_prefix.mkdir(parents=True, exist_ok=True)
        log_path = (log_path_prefix / self.request_id).with_suffix('.log')
        return log_path

    def set_error(self, error: Exception) -> None:
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
        self.return_value = encoders.get_handler(self.name)(return_value)

    def get_return_value(self) -> Any:
        """Get the return value."""
        return decoders.get_handler(self.name)(self.return_value)

    @classmethod
    def from_row(cls, row: Tuple[Any, ...]) -> 'Request':
        content = dict(zip(REQUEST_COLUMNS, row))
        # Pop db columns that are not a part of Request.
        content.pop(COL_CLUSTER_NAME, None)
        return cls.decode(RequestPayload(**content))

    def to_row(self) -> Tuple[Any, ...]:
        payload = self.encode()
        row = []
        for k in REQUEST_COLUMNS:
            if k == COL_CLUSTER_NAME:
                cluster_name = ''
                if hasattr(self.request_body, COL_CLUSTER_NAME) and \
                        self.request_body.cluster_name is not None:
                    cluster_name = self.request_body.cluster_name
                row.append(cluster_name)
            else:
                row.append(getattr(payload, k))
        return tuple(row)

    def readable_encode(self) -> RequestPayload:
        """Serialize the request task."""
        assert isinstance(self.request_body,
                          payloads.RequestBody), (self.name, self.request_body)
        user_name = global_user_state.get_user(self.user_id).name
        return RequestPayload(
            request_id=self.request_id,
            name=self.name,
            entrypoint=self.entrypoint.__name__,
            request_body=self.request_body.model_dump_json(),
            status=self.status.value,
            return_value=json.dumps(None),
            error=json.dumps(None),
            pid=None,
            created_at=self.created_at,
            schedule_type=self.schedule_type.value,
            user_id=self.user_id,
            user_name=user_name,
        )

    def encode(self) -> RequestPayload:
        """Serialize the request task."""
        assert isinstance(self.request_body,
                          payloads.RequestBody), (self.name, self.request_body)
        try:
            return RequestPayload(
                request_id=self.request_id,
                name=self.name,
                entrypoint=encoders.pickle_and_encode(self.entrypoint),
                request_body=encoders.pickle_and_encode(self.request_body),
                status=self.status.value,
                return_value=json.dumps(self.return_value),
                error=json.dumps(self.error),
                pid=self.pid,
                created_at=self.created_at,
                schedule_type=self.schedule_type.value,
                user_id=self.user_id,
            )
        except TypeError as e:
            print(f'Error encoding: {e}\n'
                  f'{self.request_id}\n'
                  f'{self.name}\n'
                  f'{self.request_body}\n'
                  f'{self.return_value}\n'
                  f'{self.created_at}\n')
            raise

    @classmethod
    def decode(cls, payload: RequestPayload) -> 'Request':
        """Deserialize the request task."""
        return cls(
            request_id=payload.request_id,
            name=payload.name,
            entrypoint=decoders.decode_and_unpickle(payload.entrypoint),
            request_body=decoders.decode_and_unpickle(payload.request_body),
            status=RequestStatus(payload.status),
            return_value=json.loads(payload.return_value),
            error=json.loads(payload.error),
            pid=payload.pid,
            created_at=payload.created_at,
            schedule_type=ScheduleType(payload.schedule_type),
            user_id=payload.user_id,
        )


def kill_cluster_requests(cluster_name: str, exclude_request_name: str):
    """Kill all running requests for a cluster.

    Args:
        cluster_name: the name of the cluster.
        exclude_request_names: exclude requests with these names. This is to
            prevent killing the caller request.
    """
    request_ids = [
        request_task.request_id for request_task in get_request_tasks(
            cluster_names=[cluster_name],
            status=[RequestStatus.RUNNING],
            exclude_request_names=[exclude_request_name])
    ]
    kill_requests(request_ids)


def refresh_cluster_status_event():
    """Periodically refresh the cluster status."""
    # pylint: disable=import-outside-toplevel
    import time

    from sky import core
    while True:
        print('Refreshing cluster status...')
        # TODO(zhwu): Periodically refresh will cause the cluster being locked
        # and other operations, such as down, may fail due to not being able to
        # acquire the lock.
        core.status(refresh=True, all_users=True)
        print('Refreshed cluster status...')
        time.sleep(20)


@dataclasses.dataclass
class InternalRequestEvent:
    id: str
    name: str
    event_fn: Callable[[], None]


# Register the events to run in the background.
INTERNAL_REQUEST_EVENTS = [
    # This status refresh daemon can cause the autostopp'ed/autodown'ed cluster
    # set to updated status automatically, without showing users the hint of
    # cluster being stopped or down when `sky status -r` is called.
    InternalRequestEvent(id='skypilot-status-refresh-daemon',
                         name='status',
                         event_fn=refresh_cluster_status_event)
]


def kill_requests(request_ids: List[str]):
    for request_id in request_ids:
        with update_request(request_id) as request_record:
            if request_record is None:
                logger.debug(f'No request ID {request_id}')
                continue
            # Skip internal requests. The internal requests are scheduled with
            # request_id in range(len(INTERNAL_REQUEST_EVENTS)).
            if request_record.request_id in set(
                    event.id for event in INTERNAL_REQUEST_EVENTS):
                continue
            if request_record.status > RequestStatus.RUNNING:
                logger.debug(f'Request {request_id} already finished')
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


_DB_PATH = os.path.expanduser(server_constants.API_SERVER_REQUEST_DB_PATH)
pathlib.Path(_DB_PATH).parents[0].mkdir(parents=True, exist_ok=True)


def create_table(cursor, conn):
    del conn
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
        {COL_USER_ID} TEXT)""")


_DB = None


def init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _DB
        if _DB is None:
            _DB = db_utils.SQLiteConn(_DB_PATH, create_table)
        return func(*args, **kwargs)

    return wrapper


def reset_db_and_logs():
    """Create the database."""
    common_utils.remove_file_if_exists(_DB_PATH)
    shutil.rmtree(REQUEST_LOG_PATH_PREFIX, ignore_errors=True)
    shutil.rmtree(server_common.CLIENT_DIR, ignore_errors=True)


def request_lock_path(request_id: str) -> str:
    return os.path.join(os.path.dirname(_DB_PATH), f'.{request_id}.lock')


@contextlib.contextmanager
@init_db
def update_request(request_id: str):
    """Get a REST request."""
    request = _get_request_no_lock(request_id)
    yield request
    if request is not None:
        _dump_request_no_lock(request)


def _get_request_no_lock(request_id: str) -> Optional[Request]:
    """Get a REST task."""
    assert _DB is not None
    columns_str = ', '.join(REQUEST_COLUMNS)
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute(
            f'SELECT {columns_str} FROM {REQUEST_TABLE} '
            'WHERE request_id LIKE ?', (request_id + '%',))
        row = cursor.fetchone()
        if row is None:
            return None
    return Request.from_row(row)


@init_db
def get_latest_request_id() -> Optional[str]:
    """Get the latest request ID."""
    assert _DB is not None
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute(f'SELECT request_id FROM {REQUEST_TABLE} '
                       'ORDER BY created_at DESC LIMIT 1')
        row = cursor.fetchone()
        return row[0] if row else None


@init_db
def get_request(request_id: str) -> Optional[Request]:
    """Get a REST task."""
    with filelock.FileLock(request_lock_path(request_id)):
        return _get_request_no_lock(request_id)


@init_db
def create_if_not_exists(request: Request) -> bool:
    """Create a REST task if it does not exist."""
    with filelock.FileLock(request_lock_path(request.request_id)):
        if _get_request_no_lock(request.request_id) is not None:
            return False
        _dump_request_no_lock(request)
        return True


@init_db
def get_request_tasks(
    status: Optional[List[RequestStatus]] = None,
    cluster_names: Optional[List[str]] = None,
    exclude_request_names: Optional[List[str]] = None,
    user_id: Optional[str] = None,
) -> List[Request]:
    """Get a list of requests that match the given filters.

    Args:
        status: a list of statuses of the requests to filter on.
        cluster_names: a list of cluster names to filter requests on.
        exclude_request_names: a list of request names to exclude from results.
        user_id: the user ID to filter requests on.
            If None, all users are included.
    """
    filters = []
    filter_params = []
    if status is not None:
        status_list_str = ','.join(repr(status.value) for status in status)
        filters.append(f'status IN ({status_list_str})')
    if exclude_request_names is not None:
        exclude_request_names_str = ','.join(
            repr(name) for name in exclude_request_names)
        filters.append(f'name NOT IN ({exclude_request_names_str})')
    if cluster_names is not None:
        cluster_names_str = ','.join(repr(name) for name in cluster_names)
        filters.append(f'{COL_CLUSTER_NAME} IN ({cluster_names_str})')
    if user_id is not None:
        filters.append(f'{COL_USER_ID} = ?')
        filter_params.append(user_id)
    assert _DB is not None
    with _DB.conn:
        cursor = _DB.conn.cursor()
        filter_str = ' AND '.join(filters)
        if filter_str:
            filter_str = f' WHERE {filter_str}'
        columns_str = ', '.join(REQUEST_COLUMNS)
        cursor.execute(
            f'SELECT {columns_str} FROM {REQUEST_TABLE}{filter_str} '
            'ORDER BY created_at DESC', filter_params)
        rows = cursor.fetchall()
        if rows is None:
            return []
    requests = []
    for row in rows:
        rest_task = Request.from_row(row)
        requests.append(rest_task)
    return requests


def _dump_request_no_lock(request: Request):
    """Dump a REST request."""
    row = request.to_row()
    key_str = ', '.join(REQUEST_COLUMNS)
    fill_str = ', '.join(['?'] * len(row))
    assert _DB is not None
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute(
            f'INSERT OR REPLACE INTO {REQUEST_TABLE} ({key_str}) '
            f'VALUES ({fill_str})', row)
