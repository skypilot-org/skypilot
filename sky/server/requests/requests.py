"""Utilities for REST API."""
import asyncio
import contextlib
import dataclasses
import enum
import functools
import json
import os
import pathlib
import shutil
import signal
import threading
import time
import traceback
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

import colorama
import filelock
import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext import declarative

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.server import common as server_common
from sky.server import daemons
from sky.server.requests import payloads
from sky.server.requests.serializers import decoders
from sky.server.requests.serializers import encoders
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils
from sky.utils.db import db_utils
from sky.utils.db import migration_utils

logger = sky_logging.init_logger(__name__)

_SQLALCHEMY_ENGINE: Optional[sqlalchemy.engine.Engine] = None
_DB_INIT_LOCK = threading.Lock()

Base = declarative.declarative_base()

# Tables in requests db.
REQUEST_TABLE = 'requests'
COL_CLUSTER_NAME = 'cluster_name'
COL_USER_ID = 'user_id'
COL_STATUS_MSG = 'status_msg'
COL_SHOULD_RETRY = 'should_retry'
COL_FINISHED_AT = 'finished_at'
COL_HOST_UUID = 'host_uuid'
REQUEST_LOG_PATH_PREFIX = '~/sky_logs/api_server/requests'

request_table = sqlalchemy.Table(
    REQUEST_TABLE,
    Base.metadata,
    sqlalchemy.Column('request_id', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('name', sqlalchemy.Text),
    sqlalchemy.Column('entrypoint', sqlalchemy.Text),
    sqlalchemy.Column('request_body', sqlalchemy.Text),
    sqlalchemy.Column('status', sqlalchemy.Text),
    sqlalchemy.Column('return_value', sqlalchemy.Text),
    sqlalchemy.Column('error', sqlalchemy.LargeBinary),
    sqlalchemy.Column('pid', sqlalchemy.Integer),
    sqlalchemy.Column('created_at', sqlalchemy.Float),
    sqlalchemy.Column(COL_CLUSTER_NAME, sqlalchemy.Text),
    sqlalchemy.Column('schedule_type', sqlalchemy.Text),
    sqlalchemy.Column(COL_USER_ID, sqlalchemy.Text),
    sqlalchemy.Column(COL_STATUS_MSG, sqlalchemy.Text),
    sqlalchemy.Column(COL_SHOULD_RETRY, sqlalchemy.Boolean),
    sqlalchemy.Column(COL_FINISHED_AT, sqlalchemy.Float),
    sqlalchemy.Column(COL_HOST_UUID, sqlalchemy.Text),
)
REQUEST_LOG_PATH_PREFIX = '~/sky_logs/api_server/requests'

DEFAULT_REQUESTS_RETENTION_HOURS = 24  # 1 day


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
    COL_HOST_UUID,
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
    # The UUID of the API server that serves the request.
    host_uuid: Optional[str] = None

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

        # Convert error from bytes back to string for RequestPayload
        if content.get('error') and isinstance(content['error'], bytes):
            content['error'] = content['error'].decode('utf-8')

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
            return_value=json.dumps(None),
            error=json.dumps(None),
            pid=None,
            created_at=self.created_at,
            schedule_type=self.schedule_type.value,
            user_id=self.user_id,
            user_name=user_name,
            cluster_name=self.cluster_name,
            status_msg=self.status_msg,
            should_retry=self.should_retry,
            finished_at=self.finished_at,
            host_uuid=self.host_uuid,
        )

    def encode(self) -> payloads.RequestPayload:
        """Serialize the SkyPilot API request."""
        assert isinstance(self.request_body,
                          payloads.RequestBody), (self.name, self.request_body)
        try:
            return payloads.RequestPayload(
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
                cluster_name=self.cluster_name,
                status_msg=self.status_msg,
                should_retry=self.should_retry,
                finished_at=self.finished_at,
                host_uuid=self.host_uuid,
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
                return_value=json.loads(payload.return_value),
                error=json.loads(payload.error),
                pid=payload.pid,
                created_at=payload.created_at,
                schedule_type=ScheduleType(payload.schedule_type),
                user_id=payload.user_id,
                cluster_name=payload.cluster_name,
                status_msg=payload.status_msg,
                should_retry=payload.should_retry,
                finished_at=payload.finished_at,
                host_uuid=payload.host_uuid,
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


def kill_cluster_requests(cluster_name: str, exclude_request_name: str):
    """Kill all pending and running requests for a cluster.

    Args:
        cluster_name: the name of the cluster.
        exclude_request_names: exclude requests with these names. This is to
            prevent killing the caller request.
    """
    request_ids = [
        request_task.request_id for request_task in get_request_tasks(
            cluster_names=[cluster_name],
            status=[RequestStatus.PENDING, RequestStatus.RUNNING],
            exclude_request_names=[exclude_request_name])
    ]
    kill_requests(request_ids)


def kill_requests(request_ids: Optional[List[str]] = None,
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
            request_task.request_id for request_task in get_request_tasks(
                user_id=user_id,
                status=[RequestStatus.RUNNING, RequestStatus.PENDING],
                # Avoid cancelling the cancel request itself.
                exclude_request_names=['sky.api_cancel'])
        ]
    cancelled_request_ids = []
    for request_id in request_ids:
        with update_request(request_id) as request_record:
            if request_record is None:
                logger.debug(f'No request ID {request_id}')
                continue

            # Skip internal requests. The internal requests are scheduled with
            # request_id in range(len(INTERNAL_REQUEST_EVENTS)).
            if request_record.request_id in set(
                    event.id for event in daemons.INTERNAL_REQUEST_DAEMONS):
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
                # TODO(aylei): for requests that processed by other API servers,
                # forward the kill request to the API server that runs the
                # request.
                try:
                    os.kill(request_record.pid, signal.SIGTERM)
                except ProcessLookupError:
                    logger.debug(f'Process {request_record.pid} not found')
            request_record.status = RequestStatus.CANCELLED
            request_record.finished_at = time.time()
            cancelled_request_ids.append(request_id)
    return cancelled_request_ids


def create_table(engine: sqlalchemy.engine.Engine):
    # Enable WAL mode to avoid locking issues.
    # See: issue #1441 and PR #1509
    # https://github.com/microsoft/WSL/issues/2395
    # TODO(romilb): We do not enable WAL for WSL because of known issue in WSL.
    #  This may cause the database locked problem from WSL issue #1441.
    if (engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value and
            not common_utils.is_wsl()):
        try:
            with orm.Session(engine) as session:
                session.execute(sqlalchemy.text('PRAGMA journal_mode=WAL'))
                session.commit()
        except sqlalchemy.exc.OperationalError as e:
            if 'database is locked' not in str(e):
                raise
            # If the database is locked, it is OK to continue, as the WAL mode
            # is not critical and is likely to be enabled by other processes.

    migration_utils.safe_alembic_upgrade(
        engine, migration_utils.REQUESTS_DB_NAME,
        migration_utils.REQUESTS_VERSION)


def initialize_and_get_db() -> sqlalchemy.engine.Engine:
    global _SQLALCHEMY_ENGINE

    if _SQLALCHEMY_ENGINE is not None:
        return _SQLALCHEMY_ENGINE
    with _DB_INIT_LOCK:
        if _SQLALCHEMY_ENGINE is not None:
            return _SQLALCHEMY_ENGINE
        # get an engine to the db
        engine = migration_utils.get_engine(migration_utils.REQUESTS_DB_NAME)

        # run migrations if needed
        create_table(engine)

        # set and return engine
        _SQLALCHEMY_ENGINE = engine
        return _SQLALCHEMY_ENGINE


def _init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        initialize_and_get_db()
        return func(*args, **kwargs)

    return wrapper


def reset_db_and_logs():
    """Create the database."""
    server_common.clear_local_api_server_database()
    shutil.rmtree(pathlib.Path(REQUEST_LOG_PATH_PREFIX).expanduser(),
                  ignore_errors=True)
    shutil.rmtree(server_common.API_SERVER_CLIENT_DIR.expanduser(),
                  ignore_errors=True)


def request_lock_path(request_id: str) -> str:
    lock_path = os.path.expanduser(REQUEST_LOG_PATH_PREFIX)
    os.makedirs(lock_path, exist_ok=True)
    return os.path.join(lock_path, f'.{request_id}.lock')


@contextlib.contextmanager
@_init_db
def update_request(request_id: str) -> Generator[Optional[Request], None, None]:
    """Get a SkyPilot API request."""
    request = _get_request_no_lock(request_id)
    yield request
    if request is not None:
        _add_or_update_request_no_lock(request)


def _get_request_no_lock(request_id: str) -> Optional[Request]:
    """Get a SkyPilot API request."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(request_table).filter(
            request_table.c.request_id.like(request_id + '%')).first()
        if row is None:
            return None
    return Request.from_row(row)


@_init_db
def get_latest_request_id() -> Optional[str]:
    """Get the latest request ID."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(request_table.c.request_id).order_by(
            request_table.c.created_at.desc()).first()
        return row[0] if row else None


@_init_db
def get_request(request_id: str) -> Optional[Request]:
    """Get a SkyPilot API request."""
    with filelock.FileLock(request_lock_path(request_id)):
        return _get_request_no_lock(request_id)


@_init_db
def create_if_not_exists(request: Request) -> bool:
    """Create a SkyPilot API request if it does not exist."""
    with filelock.FileLock(request_lock_path(request.request_id)):
        if _get_request_no_lock(request.request_id) is not None:
            return False
        _add_or_update_request_no_lock(request)
        return True


@_init_db
def get_request_tasks(
    status: Optional[List[RequestStatus]] = None,
    cluster_names: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    exclude_request_names: Optional[List[str]] = None,
    include_request_names: Optional[List[str]] = None,
    finished_before: Optional[float] = None,
    host_uuid: Optional[str] = None,
) -> List[Request]:
    """Get a list of requests that match the given filters.

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

    Raises:
        ValueError: If both exclude_request_names and include_request_names are
            provided.
    """
    if exclude_request_names is not None and include_request_names is not None:
        raise ValueError(
            'Only one of exclude_request_names or include_request_names can be '
            'provided, not both.')

    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        query = session.query(request_table)

        if status is not None:
            status_values = [s.value for s in status]
            query = query.filter(request_table.c.status.in_(status_values))
        if exclude_request_names is not None:
            query = query.filter(
                ~request_table.c.name.in_(exclude_request_names))
        if cluster_names is not None:
            query = query.filter(
                request_table.c.cluster_name.in_(cluster_names))
        if user_id is not None:
            query = query.filter(request_table.c.user_id == user_id)
        if include_request_names is not None:
            query = query.filter(
                request_table.c.name.in_(include_request_names))
        if finished_before is not None:
            query = query.filter(request_table.c.finished_at < finished_before)
        if host_uuid is not None:
            query = query.filter(request_table.c.host_uuid == host_uuid)

        rows = query.order_by(request_table.c.created_at.desc()).all()

    requests = []
    for row in rows:
        request = Request.from_row(row)
        requests.append(request)
    return requests


def _add_or_update_request_no_lock(request: Request):
    """Add or update a REST request into the database."""
    assert _SQLALCHEMY_ENGINE is not None
    payload = request.encode()

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')

        # Convert error string to bytes for LargeBinary column
        error_bytes = payload.error.encode('utf-8') if payload.error else b''

        insert_stmnt = insert_func(request_table).values(
            request_id=payload.request_id,
            name=payload.name,
            entrypoint=payload.entrypoint,
            request_body=payload.request_body,
            status=payload.status,
            created_at=payload.created_at,
            return_value=payload.return_value,
            error=error_bytes,
            pid=payload.pid,
            cluster_name=payload.cluster_name,
            schedule_type=payload.schedule_type,
            user_id=payload.user_id,
            status_msg=payload.status_msg,
            should_retry=payload.should_retry,
            finished_at=payload.finished_at,
            host_uuid=payload.host_uuid,
        )

        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            # For SQLite, use INSERT OR REPLACE
            insert_stmnt = insert_stmnt.prefix_with('OR REPLACE')
            session.execute(insert_stmnt)
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            # For PostgreSQL, use ON CONFLICT DO UPDATE
            do_update_stmt = insert_stmnt.on_conflict_do_update(
                index_elements=[request_table.c.request_id],
                set_={
                    request_table.c.name: payload.name,
                    request_table.c.entrypoint: payload.entrypoint,
                    request_table.c.request_body: payload.request_body,
                    request_table.c.status: payload.status,
                    request_table.c.created_at: payload.created_at,
                    request_table.c.return_value: payload.return_value,
                    request_table.c.error: error_bytes,
                    request_table.c.pid: payload.pid,
                    request_table.c.cluster_name: payload.cluster_name,
                    request_table.c.schedule_type: payload.schedule_type,
                    request_table.c.user_id: payload.user_id,
                    request_table.c.status_msg: payload.status_msg,
                    request_table.c.should_retry: payload.should_retry,
                    request_table.c.finished_at: payload.finished_at,
                    request_table.c.host_uuid: payload.host_uuid,
                })
            session.execute(do_update_stmt)

        session.commit()


def set_request_failed(request_id: str, e: BaseException) -> None:
    """Set a request to failed and populate the error message."""
    with ux_utils.enable_traceback():
        stacktrace = traceback.format_exc()
    setattr(e, 'stacktrace', stacktrace)
    with update_request(request_id) as request_task:
        assert request_task is not None, request_id
        request_task.status = RequestStatus.FAILED
        request_task.finished_at = time.time()
        request_task.set_error(e)


def set_request_succeeded(request_id: str, result: Optional[Any]) -> None:
    """Set a request to succeeded and populate the result."""
    with update_request(request_id) as request_task:
        assert request_task is not None, request_id
        request_task.status = RequestStatus.SUCCEEDED
        request_task.finished_at = time.time()
        if result is not None:
            request_task.set_return_value(result)


def set_request_cancelled(request_id: str) -> None:
    """Set a request to cancelled."""
    with update_request(request_id) as request_task:
        assert request_task is not None, request_id
        request_task.finished_at = time.time()
        request_task.status = RequestStatus.CANCELLED


@_init_db
def delete_requests(req_ids: List[str]):
    """Clean up requests by their IDs."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        stmt = sqlalchemy.delete(request_table).where(
            request_table.c.request_id.in_(req_ids))
        session.execute(stmt)
        session.commit()


@_init_db
def delete_requests_by_host_uuid(host_uuid: str):
    """Delete requests by host UUID."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        stmt = sqlalchemy.delete(request_table).where(
            request_table.c.host_uuid == host_uuid)
        session.execute(stmt)
        session.commit()


def clean_finished_requests_with_retention(retention_seconds: int):
    """Clean up finished requests older than the retention period.

    This function removes old finished requests (SUCCEEDED, FAILED, CANCELLED)
    from the database and cleans up their associated log files.

    Args:
        retention_seconds: Requests older than this many seconds will be
            deleted.
    """
    reqs = get_request_tasks(status=RequestStatus.finished_status(),
                             finished_before=time.time() - retention_seconds)

    subprocess_utils.run_in_parallel(
        func=lambda req: req.log_path.unlink(missing_ok=True),
        args=reqs,
        num_threads=len(reqs))

    delete_requests([req.request_id for req in reqs])

    # To avoid leakage of the log file, logs must be deleted before the
    # request task in the database.
    logger.info(f'Cleaned up {len(reqs)} finished requests '
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
                clean_finished_requests_with_retention(retention_seconds)
        except asyncio.CancelledError:
            logger.info('Requests GC daemon cancelled')
            break
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error running requests GC daemon: {e}')
        # Run the daemon at most once every hour to avoid too frequent
        # cleanup.
        await asyncio.sleep(max(retention_seconds, 3600))
