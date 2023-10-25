"""Utilities for REST API."""
import contextlib
import dataclasses
import enum
import functools
import os
import pathlib
import sqlite3
from typing import Any, List, Optional

import filelock

from sky.utils import common_utils
from sky.utils import db_utils


class RequestStatus(enum.Enum):
    """The status of a task."""

    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    ABORTED = 'ABORTED'

    def __gt__(self, other):
        return (list(RequestStatus).index(self) >
                list(RequestStatus).index(other))


@dataclasses.dataclass
class Request:
    """A REST task."""

    request_id: str
    request_name: str
    status: RequestStatus
    return_value: Any = None
    log_path: Optional[str] = None
    pid: Optional[int] = None


_DB_PATH = os.path.expanduser('~/.sky/api_server/tasks.db')
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
        CREATE TABLE IF NOT EXISTS rest_tasks (
        request_id TEXT PRIMARY KEY,
        request_name TEXT,
        status TEXT,
        return_value TEXT,
        log_path TEXT,
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
        cursor.execute('SELECT * FROM rest_tasks WHERE request_id=?',
                       (request_id,))
        row = cursor.fetchone()
        if row is None:
            return None
    return Request(
        request_id=row[0],
        request_name=row[1],
        status=RequestStatus(row[2]),
        return_value=row[3],
        log_path=row[4],
        pid=row[5],
    )


@init_db
def get_request(request_id: str) -> Optional[Request]:
    """Get a REST task."""
    with filelock.FileLock(request_lock_path(request_id)):
        return _get_rest_task_no_lock(request_id)


@init_db
def get_requests() -> List[Request]:
    """Get a REST task."""
    assert _DB is not None
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute('SELECT * FROM rest_tasks')
        rows = cursor.fetchall()
        if rows is None:
            return []
    rest_tasks = []
    for row in rows:
        rest_task = Request(
            request_id=row[0],
            request_name=row[1],
            status=RequestStatus(row[2]),
            return_value=row[3],
            log_path=row[4],
            pid=row[5],
        )
        rest_tasks.append(rest_task)
    return rest_tasks


def _dump_request_no_lock(request: Request):
    """Dump a REST task."""
    assert _DB is not None
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO rest_tasks VALUES (?, ?, ?, ?, ?, ?)',
            (request.request_id, request.request_name, request.status.value,
             request.return_value, request.log_path, request.pid))


@init_db
def dump_reqest(request: Request):
    """Dump a REST task."""
    with filelock.FileLock(request_lock_path(request.request_id)):
        _dump_request_no_lock(request)


# def _check_process_alive(pid: int) -> bool:
#     """Check if a process is alive."""

#     psutil_process = psutil.Process(pid)
#     return psutil_process.is_running()
