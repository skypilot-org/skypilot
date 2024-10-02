"""Utilities for REST API."""
import contextlib
import dataclasses
import enum
import functools
import json
import os
import pathlib
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

import filelock

from sky import exceptions
from sky.api import common
from sky.api.requests import decoders
from sky.api.requests import encoders
from sky.api.requests import payloads
from sky.utils import common_utils
from sky.utils import db_utils

TASK_LOG_PATH_PREFIX = '~/sky_logs/api_server/requests'

# TODO(zhwu): For scalability, there are several TODOs:
# 1. Use Redis + Celery for task execution.
# 2. Move logs to persistent place.
# 3. Deploy API server in a autoscaling fashion.


class RequestStatus(enum.Enum):
    """The status of a request."""

    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    ABORTED = 'ABORTED'

    def __gt__(self, other):
        return (list(RequestStatus).index(self) >
                list(RequestStatus).index(other))


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
]


@dataclasses.dataclass
class RequestPayload:
    request_id: str
    name: str
    entrypoint: str
    request_body: str
    status: str
    created_at: float
    return_value: str
    error: str
    pid: Optional[int]


@dataclasses.dataclass
class Request:
    """A REST request."""

    request_id: str
    name: str
    entrypoint: Callable
    request_body: payloads.RequestBody
    status: RequestStatus
    created_at: float
    return_value: Any = None
    error: Optional[Dict[str, Any]] = None
    pid: Optional[int] = None

    @property
    def log_path(self) -> pathlib.Path:
        log_path_prefix = pathlib.Path(
            TASK_LOG_PATH_PREFIX).expanduser().absolute()
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
        return cls.decode(RequestPayload(**dict(zip(REQUEST_COLUMNS, row))))

    def to_row(self) -> Tuple[Any, ...]:
        payload = self.encode()
        return tuple(getattr(payload, k) for k in REQUEST_COLUMNS)

    def readable_encode(self) -> RequestPayload:
        """Serialize the request task."""
        assert isinstance(self.request_body,
                          payloads.RequestBody), (self.name, self.request_body)
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
        )


_DB_PATH = os.path.expanduser(common.API_SERVER_REQUEST_DB_PATH)
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

    # Table for Clusters
    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS requests (
        request_id TEXT PRIMARY KEY,
        name TEXT,
        entrypoint TEXT,
        request_body TEXT,
        status TEXT,
        created_at REAL,
        return_value TEXT,
        error BLOB,
        pid INTEGER)""")


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


def reset_db():
    """Create the database."""
    common_utils.remove_file_if_exists(_DB_PATH)


def request_lock_path(request_id: str) -> str:
    request_lock = os.path.join(os.path.dirname(_DB_PATH),
                                f'.{request_id}.lock')
    return request_lock


@contextlib.contextmanager
@init_db
def update_rest_task(request_id: str):
    """Get a REST task."""
    rest_task = _get_rest_task_no_lock(request_id)
    yield rest_task
    if rest_task is not None:
        _dump_request_no_lock(rest_task)


def _get_rest_task_no_lock(request_id: str) -> Optional[Request]:
    """Get a REST task."""
    assert _DB is not None
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute('SELECT * FROM requests WHERE request_id LIKE ?',
                       (request_id + '%',))
        row = cursor.fetchone()
        if row is None:
            return None
    return Request.from_row(row)


@init_db
def get_request(request_id: str) -> Optional[Request]:
    """Get a REST task."""
    with filelock.FileLock(request_lock_path(request_id)):
        return _get_rest_task_no_lock(request_id)


@init_db
def create_if_not_exists(request: Request) -> bool:
    """Create a REST task if it does not exist."""
    with filelock.FileLock(request_lock_path(request.request_id)):
        if _get_rest_task_no_lock(request.request_id) is not None:
            return False
        _dump_request_no_lock(request)
        return True


@init_db
def get_request_tasks() -> List[Request]:
    """Get a REST task."""
    assert _DB is not None
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute('SELECT * FROM requests '
                       'ORDER BY created_at DESC')
        rows = cursor.fetchall()
        if rows is None:
            return []
    requests = []
    for row in rows:
        rest_task = Request.from_row(row)
        requests.append(rest_task)
    return requests


def _dump_request_no_lock(request_task: Request):
    """Dump a REST task."""
    row = request_task.to_row()
    fill_str = ', '.join(['?'] * len(row))
    assert _DB is not None
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute(f'INSERT OR REPLACE INTO requests VALUES ({fill_str})',
                       row)


@init_db
def dump_reqest(request: Request):
    """Dump a REST task."""
    with filelock.FileLock(request_lock_path(request.request_id)):
        _dump_request_no_lock(request)


# def _check_process_alive(pid: int) -> bool:
#     """Check if a process is alive."""

#     psutil_process = psutil.Process(pid)
#     return psutil_process.is_running()
