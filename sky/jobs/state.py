"""The database for managed jobs status."""
# TODO(zhwu): maybe use file based status instead of database, so
# that we can easily switch to a s3-based storage.
import asyncio
import collections
import enum
import functools
import ipaddress
import json
import sqlite3
import threading
import time
import typing
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union
import urllib.parse

import colorama
import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext import asyncio as sql_async
from sqlalchemy.ext import declarative

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import context_utils
from sky.utils.db import db_utils
from sky.utils.db import migration_utils

if typing.TYPE_CHECKING:
    from sqlalchemy.engine import row

    from sky.schemas.generated import managed_jobsv1_pb2
else:
    managed_jobsv1_pb2 = adaptors_common.LazyImport(
        'sky.schemas.generated.managed_jobsv1_pb2')

# Separate callback types for sync and async contexts
SyncCallbackType = Callable[[str], None]
AsyncCallbackType = Callable[[str], Awaitable[Any]]
CallbackType = Union[SyncCallbackType, AsyncCallbackType]

logger = sky_logging.init_logger(__name__)

_SQLALCHEMY_ENGINE: Optional[sqlalchemy.engine.Engine] = None
_SQLALCHEMY_ENGINE_ASYNC: Optional[sql_async.AsyncEngine] = None
_SQLALCHEMY_ENGINE_LOCK = threading.Lock()

_DB_RETRY_TIMES = 30

Base = declarative.declarative_base()

# === Database schema ===
# `spot` table contains all the finest-grained tasks, including all the
# tasks of a managed job (called spot for legacy reason, as it is generalized
# from the previous managed spot jobs). All tasks of the same job will have the
# same `spot_job_id`.
# The `job_name` column is now deprecated. It now holds the task's name, i.e.,
# the same content as the `task_name` column.
# The `job_id` is now not really a job id, but a only a unique
# identifier/primary key for all the tasks. We will use `spot_job_id`
# to identify the job.
# TODO(zhwu): schema migration may be needed.

spot_table = sqlalchemy.Table(
    'spot',
    Base.metadata,
    sqlalchemy.Column('job_id',
                      sqlalchemy.Integer,
                      primary_key=True,
                      autoincrement=True),
    sqlalchemy.Column('job_name', sqlalchemy.Text),
    sqlalchemy.Column('resources', sqlalchemy.Text),
    sqlalchemy.Column('submitted_at', sqlalchemy.Float),
    sqlalchemy.Column('status', sqlalchemy.Text),
    sqlalchemy.Column('run_timestamp', sqlalchemy.Text),
    sqlalchemy.Column('start_at', sqlalchemy.Float, server_default=None),
    sqlalchemy.Column('end_at', sqlalchemy.Float, server_default=None),
    sqlalchemy.Column('last_recovered_at',
                      sqlalchemy.Float,
                      server_default='-1'),
    sqlalchemy.Column('recovery_count', sqlalchemy.Integer, server_default='0'),
    sqlalchemy.Column('job_duration', sqlalchemy.Float, server_default='0'),
    sqlalchemy.Column('failure_reason', sqlalchemy.Text),
    sqlalchemy.Column('spot_job_id', sqlalchemy.Integer, index=True),
    sqlalchemy.Column('task_id', sqlalchemy.Integer, server_default='0'),
    sqlalchemy.Column('task_name', sqlalchemy.Text),
    sqlalchemy.Column('specs', sqlalchemy.Text),
    sqlalchemy.Column('local_log_file', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('metadata', sqlalchemy.Text, server_default='{}'),
    sqlalchemy.Column('logs_cleaned_at', sqlalchemy.Float, server_default=None),
)

job_info_table = sqlalchemy.Table(
    'job_info',
    Base.metadata,
    sqlalchemy.Column('spot_job_id',
                      sqlalchemy.Integer,
                      primary_key=True,
                      autoincrement=True),
    sqlalchemy.Column('name', sqlalchemy.Text),
    sqlalchemy.Column('schedule_state', sqlalchemy.Text),
    sqlalchemy.Column('controller_pid', sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('controller_pid_started_at',
                      sqlalchemy.Float,
                      server_default=None),
    sqlalchemy.Column('dag_yaml_path', sqlalchemy.Text),
    sqlalchemy.Column('env_file_path', sqlalchemy.Text),
    sqlalchemy.Column('dag_yaml_content', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('env_file_content', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('user_hash', sqlalchemy.Text),
    sqlalchemy.Column('workspace', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('priority',
                      sqlalchemy.Integer,
                      server_default=str(constants.DEFAULT_PRIORITY)),
    sqlalchemy.Column('entrypoint', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('original_user_yaml_path',
                      sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('original_user_yaml_content',
                      sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('pool', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('current_cluster_name',
                      sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('job_id_on_pool_cluster',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('pool_hash', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('controller_logs_cleaned_at',
                      sqlalchemy.Float,
                      server_default=None),
)

ha_recovery_script_table = sqlalchemy.Table(
    'ha_recovery_script',
    Base.metadata,
    sqlalchemy.Column('job_id', sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column('script', sqlalchemy.Text),
)


def create_table(engine: sqlalchemy.engine.Engine):
    # Enable WAL mode to avoid locking issues.
    # See: issue #3863, #1441 and PR #1509
    # https://github.com/microsoft/WSL/issues/2395
    # TODO(romilb): We do not enable WAL for WSL because of known issue in WSL.
    #  This may cause the database locked problem from WSL issue #1441.
    if (engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value and
            not common_utils.is_wsl()):
        try:
            with orm.Session(engine) as session:
                session.execute(sqlalchemy.text('PRAGMA journal_mode=WAL'))
                session.execute(sqlalchemy.text('PRAGMA synchronous=1'))
                session.commit()
        except sqlalchemy_exc.OperationalError as e:
            if 'database is locked' not in str(e):
                raise
            # If the database is locked, it is OK to continue, as the WAL mode
            # is not critical and is likely to be enabled by other processes.

    migration_utils.safe_alembic_upgrade(engine,
                                         migration_utils.SPOT_JOBS_DB_NAME,
                                         migration_utils.SPOT_JOBS_VERSION)


def force_no_postgres() -> bool:
    """Force no postgres.

    If the db is localhost on the api server, and we are not in consolidation
    mode, we must force using sqlite and not using the api server on the jobs
    controller.
    """
    conn_string = skypilot_config.get_nested(('db',), None)

    if conn_string:
        parsed = urllib.parse.urlparse(conn_string)
        # it freezes if we use the normal get_consolidation_mode function
        consolidation_mode = skypilot_config.get_nested(
            ('jobs', 'controller', 'consolidation_mode'), default_value=False)
        if ((parsed.hostname == 'localhost' or
             ipaddress.ip_address(parsed.hostname).is_loopback) and
                not consolidation_mode):
            return True
    return False


def initialize_and_get_db_async() -> sql_async.AsyncEngine:
    global _SQLALCHEMY_ENGINE_ASYNC
    if _SQLALCHEMY_ENGINE_ASYNC is not None:
        return _SQLALCHEMY_ENGINE_ASYNC
    with _SQLALCHEMY_ENGINE_LOCK:
        if _SQLALCHEMY_ENGINE_ASYNC is not None:
            return _SQLALCHEMY_ENGINE_ASYNC

        _SQLALCHEMY_ENGINE_ASYNC = db_utils.get_engine('spot_jobs',
                                                       async_engine=True)

    # to create the table in case an async function gets called first
    initialize_and_get_db()
    return _SQLALCHEMY_ENGINE_ASYNC


# We wrap the sqlalchemy engine initialization in a thread
# lock to ensure that multiple threads do not initialize the
# engine which could result in a rare race condition where
# a session has already been created with _SQLALCHEMY_ENGINE = e1,
# and then another thread overwrites _SQLALCHEMY_ENGINE = e2
# which could result in e1 being garbage collected unexpectedly.
def initialize_and_get_db() -> sqlalchemy.engine.Engine:
    global _SQLALCHEMY_ENGINE
    if _SQLALCHEMY_ENGINE is not None:
        return _SQLALCHEMY_ENGINE

    with _SQLALCHEMY_ENGINE_LOCK:
        if _SQLALCHEMY_ENGINE is not None:
            return _SQLALCHEMY_ENGINE
        # get an engine to the db
        engine = db_utils.get_engine('spot_jobs')

        # run migrations if needed
        create_table(engine)

        # return engine
        _SQLALCHEMY_ENGINE = engine
        return _SQLALCHEMY_ENGINE


def _init_db_async(func):
    """Initialize the async database. Add backoff to the function call."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        if _SQLALCHEMY_ENGINE_ASYNC is None:
            # this may happen multiple times since there is no locking
            # here but thats fine, this is just a short circuit for the
            # common case.
            await context_utils.to_thread(initialize_and_get_db_async)

        backoff = common_utils.Backoff(initial_backoff=1, max_backoff_factor=5)
        last_exc = None
        for _ in range(_DB_RETRY_TIMES):
            try:
                return await func(*args, **kwargs)
            except (sqlalchemy_exc.OperationalError,
                    asyncio.exceptions.TimeoutError, OSError,
                    sqlalchemy_exc.TimeoutError, sqlite3.OperationalError,
                    sqlalchemy_exc.InterfaceError, sqlite3.InterfaceError) as e:
                last_exc = e
            logger.debug(f'DB error: {last_exc}')
            await asyncio.sleep(backoff.current_backoff())
        assert last_exc is not None
        raise last_exc

    return wrapper


def _init_db(func):
    """Initialize the database. Add backoff to the function call."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if _SQLALCHEMY_ENGINE is None:
            # this may happen multiple times since there is no locking
            # here but thats fine, this is just a short circuit for the
            # common case.
            initialize_and_get_db()

        backoff = common_utils.Backoff(initial_backoff=1, max_backoff_factor=10)
        last_exc = None
        for _ in range(_DB_RETRY_TIMES):
            try:
                return func(*args, **kwargs)
            except (sqlalchemy_exc.OperationalError,
                    asyncio.exceptions.TimeoutError, OSError,
                    sqlalchemy_exc.TimeoutError, sqlite3.OperationalError,
                    sqlalchemy_exc.InterfaceError, sqlite3.InterfaceError) as e:
                last_exc = e
            logger.debug(f'DB error: {last_exc}')
            time.sleep(backoff.current_backoff())
        assert last_exc is not None
        raise last_exc

    return wrapper


async def _describe_task_transition_failure(session: sql_async.AsyncSession,
                                            job_id: int, task_id: int) -> str:
    """Return a human-readable description when a task transition fails."""
    details = 'Couldn\'t fetch the task details.'
    try:
        debug_result = await session.execute(
            sqlalchemy.select(spot_table.c.status, spot_table.c.end_at).where(
                sqlalchemy.and_(spot_table.c.spot_job_id == job_id,
                                spot_table.c.task_id == task_id)))
        rows = debug_result.mappings().all()
        details = (f'{len(rows)} rows matched job {job_id} and task '
                   f'{task_id}.')
        for row in rows:
            status = row['status']
            end_at = row['end_at']
            details += f' Status: {status}, End time: {end_at}.'
    except Exception as exc:  # pylint: disable=broad-except
        details += f' Error fetching task details: {exc}'
    return details


# job_duration is the time a job actually runs (including the
# setup duration) before last_recover, excluding the provision
# and recovery time.
# If the job is not finished:
# total_job_duration = now() - last_recovered_at + job_duration
# If the job is not finished:
# total_job_duration = end_at - last_recovered_at + job_duration
#
# Column names to be used in the jobs dict returned to the caller,
# e.g., via sky jobs queue. These may not correspond to actual
# column names in the DB and it corresponds to the combined view
# by joining the spot and job_info tables.
def _get_jobs_dict(r: 'row.RowMapping') -> Dict[str, Any]:
    # WARNING: If you update these you may also need to update GetJobTable in
    # the skylet ManagedJobsServiceImpl.
    return {
        '_job_id': r.get('job_id'),  # from spot table
        '_task_name': r.get('job_name'),  # deprecated, from spot table
        'resources': r.get('resources'),
        'submitted_at': r.get('submitted_at'),
        'status': r.get('status'),
        'run_timestamp': r.get('run_timestamp'),
        'start_at': r.get('start_at'),
        'end_at': r.get('end_at'),
        'last_recovered_at': r.get('last_recovered_at'),
        'recovery_count': r.get('recovery_count'),
        'job_duration': r.get('job_duration'),
        'failure_reason': r.get('failure_reason'),
        'job_id': r.get(spot_table.c.spot_job_id
                       ),  # ambiguous, use table.column
        'task_id': r.get('task_id'),
        'task_name': r.get('task_name'),
        'specs': r.get('specs'),
        'local_log_file': r.get('local_log_file'),
        'metadata': r.get('metadata'),
        # columns from job_info table (some may be None for legacy jobs)
        '_job_info_job_id': r.get(job_info_table.c.spot_job_id
                                 ),  # ambiguous, use table.column
        'job_name': r.get('name'),  # from job_info table
        'schedule_state': r.get('schedule_state'),
        'controller_pid': r.get('controller_pid'),
        'controller_pid_started_at': r.get('controller_pid_started_at'),
        # the _path columns are for backwards compatibility, use the _content
        # columns instead
        'dag_yaml_path': r.get('dag_yaml_path'),
        'env_file_path': r.get('env_file_path'),
        'dag_yaml_content': r.get('dag_yaml_content'),
        'env_file_content': r.get('env_file_content'),
        'user_hash': r.get('user_hash'),
        'workspace': r.get('workspace'),
        'priority': r.get('priority'),
        'entrypoint': r.get('entrypoint'),
        'original_user_yaml_path': r.get('original_user_yaml_path'),
        'original_user_yaml_content': r.get('original_user_yaml_content'),
        'pool': r.get('pool'),
        'current_cluster_name': r.get('current_cluster_name'),
        'job_id_on_pool_cluster': r.get('job_id_on_pool_cluster'),
        'pool_hash': r.get('pool_hash'),
    }


class ManagedJobStatus(enum.Enum):
    """Managed job status, designed to be in serverless style.

    The ManagedJobStatus is a higher level status than the JobStatus.
    Each managed job submitted to a cluster will have a JobStatus
    on that cluster:
        JobStatus = [INIT, SETTING_UP, PENDING, RUNNING, ...]
    Whenever the cluster is preempted and recovered, the JobStatus
    will go through the statuses above again.
    That means during the lifetime of a managed job, its JobsStatus could be
    reset to INIT or SETTING_UP multiple times (depending on the preemptions).

    However, a managed job only has one ManagedJobStatus on the jobs controller.
        ManagedJobStatus = [PENDING, STARTING, RUNNING, ...]
    Mapping from JobStatus to ManagedJobStatus:
        INIT            ->  STARTING/RECOVERING
        SETTING_UP      ->  RUNNING
        PENDING         ->  RUNNING
        RUNNING         ->  RUNNING
        SUCCEEDED       ->  SUCCEEDED
        FAILED          ->  FAILED
        FAILED_SETUP    ->  FAILED_SETUP
    Not all statuses are in this list, since some ManagedJobStatuses are only
    possible while the cluster is INIT/STOPPED/not yet UP.
    Note that the JobStatus will not be stuck in PENDING, because each cluster
    is dedicated to a managed job, i.e. there should always be enough resource
    to run the job and the job will be immediately transitioned to RUNNING.

    You can see a state diagram for ManagedJobStatus in sky/jobs/README.md.
    """
    # PENDING: Waiting for the jobs controller to have a slot to run the
    # controller process.
    PENDING = 'PENDING'
    # SUBMITTED: This state used to be briefly set before immediately changing
    # to STARTING. Its use was removed in #5682. We keep it for backwards
    # compatibility, so we can still parse old jobs databases that may have jobs
    # in this state.
    # TODO(cooperc): remove this in v0.12.0
    DEPRECATED_SUBMITTED = 'SUBMITTED'
    # The submitted_at timestamp of the managed job in the 'spot' table will be
    # set to the time when the job controller begins running.
    # STARTING: The controller process is launching the cluster for the managed
    # job.
    STARTING = 'STARTING'
    # RUNNING: The job is submitted to the cluster, and is setting up or
    # running.
    # The start_at timestamp of the managed job in the 'spot' table will be set
    # to the time when the job is firstly transitioned to RUNNING.
    RUNNING = 'RUNNING'
    # RECOVERING: The cluster is preempted, and the controller process is
    # recovering the cluster (relaunching/failover).
    RECOVERING = 'RECOVERING'
    # CANCELLING: The job is requested to be cancelled by the user, and the
    # controller is cleaning up the cluster.
    CANCELLING = 'CANCELLING'
    # Terminal statuses
    # SUCCEEDED: The job is finished successfully.
    SUCCEEDED = 'SUCCEEDED'
    # CANCELLED: The job is cancelled by the user. When the managed job is in
    # CANCELLED status, the cluster has been cleaned up.
    CANCELLED = 'CANCELLED'
    # FAILED: The job is finished with failure from the user's program.
    FAILED = 'FAILED'
    # FAILED_SETUP: The job is finished with failure from the user's setup
    # script.
    FAILED_SETUP = 'FAILED_SETUP'
    # FAILED_PRECHECKS: the underlying `sky.launch` fails due to precheck
    # errors only. I.e., none of the failover exceptions, if any, is due to
    # resources unavailability. This exception includes the following cases:
    # 1. The optimizer cannot find a feasible solution.
    # 2. Precheck errors: invalid cluster name, failure in getting cloud user
    #    identity, or unsupported feature.
    FAILED_PRECHECKS = 'FAILED_PRECHECKS'
    # FAILED_NO_RESOURCE: The job is finished with failure because there is no
    # resource available in the cloud provider(s) to launch the cluster.
    FAILED_NO_RESOURCE = 'FAILED_NO_RESOURCE'
    # FAILED_CONTROLLER: The job is finished with failure because of unexpected
    # error in the controller process.
    FAILED_CONTROLLER = 'FAILED_CONTROLLER'

    def is_terminal(self) -> bool:
        return self in self.terminal_statuses()

    def is_failed(self) -> bool:
        return self in self.failure_statuses()

    def colored_str(self) -> str:
        color = _SPOT_STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'

    def __lt__(self, other) -> bool:
        status_list = list(ManagedJobStatus)
        return status_list.index(self) < status_list.index(other)

    @classmethod
    def terminal_statuses(cls) -> List['ManagedJobStatus']:
        return [
            cls.SUCCEEDED,
            cls.FAILED,
            cls.FAILED_SETUP,
            cls.FAILED_PRECHECKS,
            cls.FAILED_NO_RESOURCE,
            cls.FAILED_CONTROLLER,
            cls.CANCELLED,
        ]

    @classmethod
    def failure_statuses(cls) -> List['ManagedJobStatus']:
        return [
            cls.FAILED, cls.FAILED_SETUP, cls.FAILED_PRECHECKS,
            cls.FAILED_NO_RESOURCE, cls.FAILED_CONTROLLER
        ]

    @classmethod
    def processing_statuses(cls) -> List['ManagedJobStatus']:
        # Any status that is not terminal and is not CANCELLING.
        return [
            cls.PENDING,
            cls.STARTING,
            cls.RUNNING,
            cls.RECOVERING,
        ]

    @classmethod
    def from_protobuf(
        cls, protobuf_value: 'managed_jobsv1_pb2.ManagedJobStatus'
    ) -> Optional['ManagedJobStatus']:
        """Convert protobuf ManagedJobStatus enum to Python enum value."""
        protobuf_to_enum = {
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_UNSPECIFIED: None,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_PENDING: cls.PENDING,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_SUBMITTED:
                cls.DEPRECATED_SUBMITTED,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_STARTING: cls.STARTING,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_RUNNING: cls.RUNNING,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_SUCCEEDED: cls.SUCCEEDED,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED: cls.FAILED,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_CONTROLLER:
                cls.FAILED_CONTROLLER,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_SETUP:
                cls.FAILED_SETUP,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_CANCELLED: cls.CANCELLED,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_RECOVERING: cls.RECOVERING,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_CANCELLING: cls.CANCELLING,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_PRECHECKS:
                cls.FAILED_PRECHECKS,
            managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_NO_RESOURCE:
                cls.FAILED_NO_RESOURCE,
        }

        if protobuf_value not in protobuf_to_enum:
            raise ValueError(
                f'Unknown protobuf ManagedJobStatus value: {protobuf_value}')

        return protobuf_to_enum[protobuf_value]

    def to_protobuf(self) -> 'managed_jobsv1_pb2.ManagedJobStatus':
        """Convert this Python enum value to protobuf enum value."""
        enum_to_protobuf = {
            ManagedJobStatus.PENDING:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_PENDING,
            ManagedJobStatus.DEPRECATED_SUBMITTED:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_SUBMITTED,
            ManagedJobStatus.STARTING:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_STARTING,
            ManagedJobStatus.RUNNING:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_RUNNING,
            ManagedJobStatus.SUCCEEDED:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_SUCCEEDED,
            ManagedJobStatus.FAILED:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED,
            ManagedJobStatus.FAILED_CONTROLLER:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_CONTROLLER,
            ManagedJobStatus.FAILED_SETUP:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_SETUP,
            ManagedJobStatus.CANCELLED:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_CANCELLED,
            ManagedJobStatus.RECOVERING:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_RECOVERING,
            ManagedJobStatus.CANCELLING:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_CANCELLING,
            ManagedJobStatus.FAILED_PRECHECKS:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_PRECHECKS,
            ManagedJobStatus.FAILED_NO_RESOURCE:
                managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_NO_RESOURCE,
        }

        if self not in enum_to_protobuf:
            raise ValueError(f'Unknown ManagedJobStatus value: {self}')

        return enum_to_protobuf[self]


_SPOT_STATUS_TO_COLOR = {
    ManagedJobStatus.PENDING: colorama.Fore.BLUE,
    ManagedJobStatus.STARTING: colorama.Fore.BLUE,
    ManagedJobStatus.RUNNING: colorama.Fore.GREEN,
    ManagedJobStatus.RECOVERING: colorama.Fore.CYAN,
    ManagedJobStatus.SUCCEEDED: colorama.Fore.GREEN,
    ManagedJobStatus.FAILED: colorama.Fore.RED,
    ManagedJobStatus.FAILED_PRECHECKS: colorama.Fore.RED,
    ManagedJobStatus.FAILED_SETUP: colorama.Fore.RED,
    ManagedJobStatus.FAILED_NO_RESOURCE: colorama.Fore.RED,
    ManagedJobStatus.FAILED_CONTROLLER: colorama.Fore.RED,
    ManagedJobStatus.CANCELLING: colorama.Fore.YELLOW,
    ManagedJobStatus.CANCELLED: colorama.Fore.YELLOW,
    # TODO(cooperc): backwards compatibility, remove this in v0.12.0
    ManagedJobStatus.DEPRECATED_SUBMITTED: colorama.Fore.BLUE,
}


class ManagedJobScheduleState(enum.Enum):
    """Captures the state of the job from the scheduler's perspective.

    A job that predates the introduction of the scheduler will be INVALID.

    A newly created job will be INACTIVE.  The following transitions are valid:
    - INACTIVE -> WAITING: The job is "submitted" to the scheduler, and its job
      controller can be started.
    - WAITING -> LAUNCHING: The job controller is starting by the scheduler and
      may proceed to sky.launch.
    - LAUNCHING -> ALIVE: The launch attempt was completed. It may have
      succeeded or failed. The job controller is not allowed to sky.launch again
      without transitioning to ALIVE_WAITING and then LAUNCHING.
    - LAUNCHING -> ALIVE_BACKOFF: The launch failed to find resources, and is
      in backoff waiting for resources.
    - ALIVE -> ALIVE_WAITING: The job controller wants to sky.launch again,
      either for recovery or to launch a subsequent task.
    - ALIVE_BACKOFF -> ALIVE_WAITING: The backoff period has ended, and the job
      controller wants to try to launch again.
    - ALIVE_WAITING -> LAUNCHING: The scheduler has determined that the job
      controller may launch again.
    - LAUNCHING, ALIVE, or ALIVE_WAITING -> DONE: The job controller is exiting
      and the job is in some terminal status. In the future it may be possible
      to transition directly from WAITING or even INACTIVE to DONE if the job is
      cancelled.

    You can see a state diagram in sky/jobs/README.md.

    There is no well-defined mapping from the managed job status to schedule
    state or vice versa. (In fact, schedule state is defined on the job and
    status on the task.)
    - INACTIVE or WAITING should only be seen when a job is PENDING.
    - ALIVE_BACKOFF should only be seen when a job is STARTING.
    - ALIVE_WAITING should only be seen when a job is RECOVERING, has multiple
      tasks, or needs to retry launching.
    - LAUNCHING and ALIVE can be seen in many different statuses.
    - DONE should only be seen when a job is in a terminal status.
    Since state and status transitions are not atomic, it may be possible to
    briefly observe inconsistent states, like a job that just finished but
    hasn't yet transitioned to DONE.
    """
    # This job may have been created before scheduler was introduced in #4458.
    # This state is not used by scheduler but just for backward compatibility.
    # TODO(cooperc): remove this in v0.11.0
    # TODO(luca): the only states we need are INACTIVE, WAITING, ALIVE, and
    # DONE. ALIVE = old LAUNCHING + ALIVE + ALIVE_BACKOFF + ALIVE_WAITING and
    # will represent jobs that are claimed by a controller. Delete the rest
    # in v0.13.0
    INVALID = None
    # The job should be ignored by the scheduler.
    INACTIVE = 'INACTIVE'
    # The job is waiting to transition to LAUNCHING for the first time. The
    # scheduler should try to transition it, and when it does, it should start
    # the job controller.
    WAITING = 'WAITING'
    # The job is already alive, but wants to transition back to LAUNCHING,
    # e.g. for recovery, or launching later tasks in the DAG. The scheduler
    # should try to transition it to LAUNCHING.
    ALIVE_WAITING = 'ALIVE_WAITING'
    # The job is running sky.launch, or soon will, using a limited number of
    # allowed launch slots.
    LAUNCHING = 'LAUNCHING'
    # The job is alive, but is in backoff waiting for resources - a special case
    # of ALIVE.
    ALIVE_BACKOFF = 'ALIVE_BACKOFF'
    # The controller for the job is running, but it's not currently launching.
    ALIVE = 'ALIVE'
    # The job is in a terminal state. (Not necessarily SUCCEEDED.)
    DONE = 'DONE'

    @classmethod
    def from_protobuf(
        cls, protobuf_value: 'managed_jobsv1_pb2.ManagedJobScheduleState'
    ) -> Optional['ManagedJobScheduleState']:
        """Convert protobuf ManagedJobScheduleState enum to Python enum value.
        """
        protobuf_to_enum = {
            managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_UNSPECIFIED: None,
            managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_INVALID: cls.INVALID,
            managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_INACTIVE:
                cls.INACTIVE,
            managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_WAITING: cls.WAITING,
            managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_ALIVE_WAITING:
                cls.ALIVE_WAITING,
            managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_LAUNCHING:
                cls.LAUNCHING,
            managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_ALIVE_BACKOFF:
                cls.ALIVE_BACKOFF,
            managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_ALIVE: cls.ALIVE,
            managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_DONE: cls.DONE,
        }

        if protobuf_value not in protobuf_to_enum:
            raise ValueError('Unknown protobuf ManagedJobScheduleState value: '
                             f'{protobuf_value}')

        return protobuf_to_enum[protobuf_value]

    def to_protobuf(self) -> 'managed_jobsv1_pb2.ManagedJobScheduleState':
        """Convert this Python enum value to protobuf enum value."""
        enum_to_protobuf = {
            ManagedJobScheduleState.INVALID:
                managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_INVALID,
            ManagedJobScheduleState.INACTIVE:
                managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_INACTIVE,
            ManagedJobScheduleState.WAITING:
                managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_WAITING,
            ManagedJobScheduleState.ALIVE_WAITING:
                managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_ALIVE_WAITING,
            ManagedJobScheduleState.LAUNCHING:
                managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_LAUNCHING,
            ManagedJobScheduleState.ALIVE_BACKOFF:
                managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_ALIVE_BACKOFF,
            ManagedJobScheduleState.ALIVE:
                managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_ALIVE,
            ManagedJobScheduleState.DONE:
                managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_DONE,
        }

        if self not in enum_to_protobuf:
            raise ValueError(f'Unknown ManagedJobScheduleState value: {self}')

        return enum_to_protobuf[self]


ControllerPidRecord = collections.namedtuple('ControllerPidRecord', [
    'pid',
    'started_at',
])


# === Status transition functions ===
@_init_db
def set_job_info_without_job_id(name: str, workspace: str, entrypoint: str,
                                pool: Optional[str], pool_hash: Optional[str],
                                user_hash: Optional[str]) -> int:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')

        insert_stmt = insert_func(job_info_table).values(
            name=name,
            schedule_state=ManagedJobScheduleState.INACTIVE.value,
            workspace=workspace,
            entrypoint=entrypoint,
            pool=pool,
            pool_hash=pool_hash,
            user_hash=user_hash,
        )

        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            result = session.execute(insert_stmt)
            ret = result.lastrowid
            session.commit()
            return ret
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            result = session.execute(
                insert_stmt.returning(job_info_table.c.spot_job_id))
            ret = result.scalar()
            session.commit()
            return ret
        else:
            raise ValueError('Unsupported database dialect')


@_init_db
def set_pending(
    job_id: int,
    task_id: int,
    task_name: str,
    resources_str: str,
    metadata: str,
):
    """Set the task to pending state."""
    assert _SQLALCHEMY_ENGINE is not None

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.execute(
            sqlalchemy.insert(spot_table).values(
                spot_job_id=job_id,
                task_id=task_id,
                task_name=task_name,
                resources=resources_str,
                metadata=metadata,
                status=ManagedJobStatus.PENDING.value,
            ))
        session.commit()


@_init_db_async
async def set_backoff_pending_async(job_id: int, task_id: int):
    """Set the task to PENDING state if it is in backoff.

    This should only be used to transition from STARTING or RECOVERING back to
    PENDING.
    """
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.update(spot_table).where(
                sqlalchemy.and_(
                    spot_table.c.spot_job_id == job_id,
                    spot_table.c.task_id == task_id,
                    spot_table.c.status.in_([
                        ManagedJobStatus.STARTING.value,
                        ManagedJobStatus.RECOVERING.value
                    ]),
                    spot_table.c.end_at.is_(None),
                )).values({spot_table.c.status: ManagedJobStatus.PENDING.value})
        )
        count = result.rowcount
        await session.commit()
        if count != 1:
            details = await _describe_task_transition_failure(
                session, job_id, task_id)
            message = ('Failed to set the task back to pending. '
                       f'({count} rows updated. {details})')
            logger.error(message)
            raise exceptions.ManagedJobStatusError(message)
    # Do not call callback_func here, as we don't use the callback for PENDING.


@_init_db
async def set_restarting_async(job_id: int, task_id: int, recovering: bool):
    """Set the task back to STARTING or RECOVERING from PENDING.

    This should not be used for the initial transition from PENDING to STARTING.
    In that case, use set_starting instead. This function should only be used
    after using set_backoff_pending to transition back to PENDING during
    launch retry backoff.
    """
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    target_status = ManagedJobStatus.STARTING.value
    if recovering:
        target_status = ManagedJobStatus.RECOVERING.value
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.update(spot_table).where(
                sqlalchemy.and_(
                    spot_table.c.spot_job_id == job_id,
                    spot_table.c.task_id == task_id,
                    spot_table.c.end_at.is_(None),
                )).values({spot_table.c.status: target_status}))
        count = result.rowcount
        await session.commit()
        logger.debug(f'back to {target_status}')
        if count != 1:
            details = await _describe_task_transition_failure(
                session, job_id, task_id)
            message = (f'Failed to set the task back to {target_status}. '
                       f'({count} rows updated. {details})')
            logger.error(message)
            raise exceptions.ManagedJobStatusError(message)
    # Do not call callback_func here, as it should only be invoked for the
    # initial (pre-`set_backoff_pending`) transition to STARTING or RECOVERING.


@_init_db
def set_failed(
    job_id: int,
    task_id: Optional[int],
    failure_type: ManagedJobStatus,
    failure_reason: str,
    callback_func: Optional[CallbackType] = None,
    end_time: Optional[float] = None,
    override_terminal: bool = False,
):
    """Set an entire job or task to failed.

    By default, don't override tasks that are already terminal (that is, for
    which end_at is already set).

    Args:
        job_id: The job id.
        task_id: The task id. If None, all non-finished tasks of the job will
            be set to failed.
        failure_type: The failure type. One of ManagedJobStatus.FAILED_*.
        failure_reason: The failure reason.
        end_time: The end time. If None, the current time will be used.
        override_terminal: If True, override the current status even if end_at
            is already set.
    """
    assert _SQLALCHEMY_ENGINE is not None
    assert failure_type.is_failed(), failure_type
    end_time = time.time() if end_time is None else end_time

    fields_to_set: Dict[str, Any] = {
        spot_table.c.status: failure_type.value,
        spot_table.c.failure_reason: failure_reason,
    }
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # Get previous status
        previous_status = session.execute(
            sqlalchemy.select(spot_table.c.status).where(
                spot_table.c.spot_job_id == job_id)).fetchone()[0]
        previous_status = ManagedJobStatus(previous_status)
        if previous_status == ManagedJobStatus.RECOVERING:
            # If the job is recovering, we should set the last_recovered_at to
            # the end_time, so that the end_at - last_recovered_at will not be
            # affect the job duration calculation.
            fields_to_set[spot_table.c.last_recovered_at] = end_time
        where_conditions = [spot_table.c.spot_job_id == job_id]
        if task_id is not None:
            where_conditions.append(spot_table.c.task_id == task_id)

        # Handle failure_reason prepending when override_terminal is True
        if override_terminal:
            # Get existing failure_reason with row lock to prevent race
            # conditions
            existing_reason_result = session.execute(
                sqlalchemy.select(spot_table.c.failure_reason).where(
                    sqlalchemy.and_(*where_conditions)).with_for_update())
            existing_reason_row = existing_reason_result.fetchone()
            if existing_reason_row and existing_reason_row[0]:
                # Prepend new failure reason to existing one
                fields_to_set[spot_table.c.failure_reason] = (
                    failure_reason + '. Previously: ' + existing_reason_row[0])
            # Use COALESCE for end_at to avoid overriding the existing end_at if
            # it's already set.
            fields_to_set[spot_table.c.end_at] = sqlalchemy.func.coalesce(
                spot_table.c.end_at, end_time)
        else:
            fields_to_set[spot_table.c.end_at] = end_time
            where_conditions.append(spot_table.c.end_at.is_(None))
        count = session.query(spot_table).filter(
            sqlalchemy.and_(*where_conditions)).update(fields_to_set)
        session.commit()
        updated = count > 0
    if callback_func and updated:
        callback_func('FAILED')
    logger.info(failure_reason)


@_init_db
def set_pending_cancelled(job_id: int):
    """Set the job as cancelled, if it is PENDING and WAITING/INACTIVE.

    This may fail if the job is not PENDING, e.g. another process has changed
    its state in the meantime.

    Returns:
        True if the job was cancelled, False otherwise.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # Subquery to get the spot_job_ids that match the joined condition
        subquery = session.query(spot_table.c.job_id).join(
            job_info_table,
            spot_table.c.spot_job_id == job_info_table.c.spot_job_id
        ).filter(
            spot_table.c.spot_job_id == job_id,
            spot_table.c.status == ManagedJobStatus.PENDING.value,
            # Note: it's possible that a WAITING job actually needs to be
            # cleaned up, if we are in the middle of an upgrade/recovery and
            # the job is waiting to be reclaimed by a new controller. But,
            # in this case the status will not be PENDING.
            sqlalchemy.or_(
                job_info_table.c.schedule_state ==
                ManagedJobScheduleState.WAITING.value,
                job_info_table.c.schedule_state ==
                ManagedJobScheduleState.INACTIVE.value,
            ),
        ).subquery()

        count = session.query(spot_table).filter(
            spot_table.c.job_id.in_(subquery)).update(
                {spot_table.c.status: ManagedJobStatus.CANCELLED.value},
                synchronize_session=False)
        session.commit()
        return count > 0


@_init_db
def set_local_log_file(job_id: int, task_id: Optional[int],
                       local_log_file: str):
    """Set the local log file for a job."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        where_conditions = [spot_table.c.spot_job_id == job_id]
        if task_id is not None:
            where_conditions.append(spot_table.c.task_id == task_id)
        session.query(spot_table).filter(
            sqlalchemy.and_(*where_conditions)).update(
                {spot_table.c.local_log_file: local_log_file})
        session.commit()


# ======== utility functions ========
@_init_db
def get_nonterminal_job_ids_by_name(name: Optional[str],
                                    user_hash: Optional[str] = None,
                                    all_users: bool = False) -> List[int]:
    """Get non-terminal job ids by name.

    If name is None:
    1. if all_users is False, get for the given user_hash
    2. otherwise, get for all users
    """
    assert _SQLALCHEMY_ENGINE is not None

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # Build the query using SQLAlchemy core
        query = sqlalchemy.select(
            spot_table.c.spot_job_id.distinct()).select_from(
                spot_table.outerjoin(
                    job_info_table,
                    spot_table.c.spot_job_id == job_info_table.c.spot_job_id,
                ))
        where_conditions = [
            ~spot_table.c.status.in_([
                status.value for status in ManagedJobStatus.terminal_statuses()
            ])
        ]
        if name is None and not all_users:
            if user_hash is None:
                # For backwards compatibility. With codegen, USER_ID_ENV_VAR
                # was set to the correct value by the jobs controller, as
                # part of ManagedJobCodeGen._build(). This is no longer the
                # case for the Skylet gRPC server, which is why we need to
                # pass it explicitly through the request body.
                logger.debug('user_hash is None, using current user hash')
                user_hash = common_utils.get_user_hash()
            where_conditions.append(job_info_table.c.user_hash == user_hash)
        if name is not None:
            # We match the job name from `job_info` for the jobs submitted after
            # #1982, and from `spot` for the jobs submitted before #1982, whose
            # job_info is not available.
            where_conditions.append(
                sqlalchemy.or_(
                    job_info_table.c.name == name,
                    sqlalchemy.and_(job_info_table.c.name.is_(None),
                                    spot_table.c.task_name == name),
                ))
        query = query.where(sqlalchemy.and_(*where_conditions)).order_by(
            spot_table.c.spot_job_id.desc())
        rows = session.execute(query).fetchall()
        job_ids = [row[0] for row in rows if row[0] is not None]
        return job_ids


@_init_db
def get_jobs_to_check_status(job_id: Optional[int] = None) -> List[int]:
    """Get jobs that need controller process checking.

    Args:
        job_id: Optional job ID to check. If None, checks all jobs.

    Returns a list of job_ids, including the following:
    - Jobs that have a schedule_state that is not DONE
    - Jobs have schedule_state DONE but are in a non-terminal status
    - Legacy jobs (that is, no schedule state) that are in non-terminal status
    """
    assert _SQLALCHEMY_ENGINE is not None

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        terminal_status_values = [
            status.value for status in ManagedJobStatus.terminal_statuses()
        ]

        query = sqlalchemy.select(
            spot_table.c.spot_job_id.distinct()).select_from(
                spot_table.outerjoin(
                    job_info_table,
                    spot_table.c.spot_job_id == job_info_table.c.spot_job_id))

        # Get jobs that are either:
        # 1. Have schedule state that is not DONE, or
        # 2. Have schedule state DONE AND are in non-terminal status (unexpected
        #    inconsistent state), or
        # 3. Have no schedule state (legacy) AND are in non-terminal status

        # non-legacy jobs that are not DONE
        condition1 = sqlalchemy.and_(
            job_info_table.c.schedule_state.is_not(None),
            job_info_table.c.schedule_state !=
            ManagedJobScheduleState.DONE.value)
        # legacy or that are in non-terminal status or
        # DONE jobs that are in non-terminal status
        condition2 = sqlalchemy.and_(
            sqlalchemy.or_(
                # legacy jobs
                job_info_table.c.schedule_state.is_(None),
                # non-legacy DONE jobs
                job_info_table.c.schedule_state ==
                ManagedJobScheduleState.DONE.value),
            # non-terminal
            ~spot_table.c.status.in_(terminal_status_values),
        )
        where_condition = sqlalchemy.or_(condition1, condition2)
        if job_id is not None:
            where_condition = sqlalchemy.and_(
                where_condition, spot_table.c.spot_job_id == job_id)

        query = query.where(where_condition).order_by(
            spot_table.c.spot_job_id.desc())

        rows = session.execute(query).fetchall()
        return [row[0] for row in rows if row[0] is not None]


@_init_db
def _get_all_task_ids_statuses(
        job_id: int) -> List[Tuple[int, ManagedJobStatus]]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        id_statuses = session.execute(
            sqlalchemy.select(
                spot_table.c.task_id,
                spot_table.c.status,
            ).where(spot_table.c.spot_job_id == job_id).order_by(
                spot_table.c.task_id.asc())).fetchall()
        return [(row[0], ManagedJobStatus(row[1])) for row in id_statuses]


@_init_db
def get_all_task_ids_names_statuses_logs(
    job_id: int
) -> List[Tuple[int, str, ManagedJobStatus, str, Optional[float]]]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        id_names = session.execute(
            sqlalchemy.select(
                spot_table.c.task_id,
                spot_table.c.task_name,
                spot_table.c.status,
                spot_table.c.local_log_file,
                spot_table.c.logs_cleaned_at,
            ).where(spot_table.c.spot_job_id == job_id).order_by(
                spot_table.c.task_id.asc())).fetchall()
        return [(row[0], row[1], ManagedJobStatus(row[2]), row[3], row[4])
                for row in id_names]


def get_num_tasks(job_id: int) -> int:
    return len(_get_all_task_ids_statuses(job_id))


def get_latest_task_id_status(
        job_id: int) -> Union[Tuple[int, ManagedJobStatus], Tuple[None, None]]:
    """Returns the (task id, status) of the latest task of a job.

    The latest means the task that is currently being executed or to be started
    by the controller process. For example, in a managed job with 3 tasks, the
    first task is succeeded, and the second task is being executed. This will
    return (1, ManagedJobStatus.RUNNING).

    If the job_id does not exist, (None, None) will be returned.
    """
    id_statuses = _get_all_task_ids_statuses(job_id)
    if not id_statuses:
        return None, None
    task_id, status = next(
        ((tid, st) for tid, st in id_statuses if not st.is_terminal()),
        id_statuses[-1],
    )
    # Unpack the tuple first, or it triggers a Pylint's bug on recognizing
    # the return type.
    return task_id, status


@_init_db
def get_job_controller_process(job_id: int) -> Optional[ControllerPidRecord]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.execute(
            sqlalchemy.select(
                job_info_table.c.controller_pid,
                job_info_table.c.controller_pid_started_at).where(
                    job_info_table.c.spot_job_id == job_id)).fetchone()
        if row is None or row[0] is None:
            return None
        pid = row[0]
        if pid < 0:
            # Between #7051 and #7847, the controller pid was negative to
            # indicate a controller process that can handle multiple jobs.
            pid = -pid
        return ControllerPidRecord(pid=pid, started_at=row[1])


@_init_db
def is_legacy_controller_process(job_id: int) -> bool:
    """Check if the controller process is a legacy single-job controller process

    After #7051, the controller process pid is negative to indicate a new
    multi-job controller process.
    After #7847, the controller process pid is changed back to positive, but
    controller_pid_started_at will also be set.
    """
    # TODO(cooperc): Remove this function for 0.13.0
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.execute(
            sqlalchemy.select(
                job_info_table.c.controller_pid,
                job_info_table.c.controller_pid_started_at).where(
                    job_info_table.c.spot_job_id == job_id)).fetchone()
        if row is None:
            raise ValueError(f'Job {job_id} not found')
        if row[0] is None:
            # Job is from before #4485, so controller_pid is not set
            # This is a legacy single-job controller process (running in ray!)
            return True
        started_at = row[1]
        if started_at is not None:
            # controller_pid_started_at is only set after #7847, so we know this
            # must be a non-legacy multi-job controller process.
            return False
        pid = row[0]
        if pid < 0:
            # Between #7051 and #7847, the controller pid was negative to
            # indicate a non-legacy multi-job controller process.
            return False
        return True


def get_status(job_id: int) -> Optional[ManagedJobStatus]:
    _, status = get_latest_task_id_status(job_id)
    return status


@_init_db
def get_failure_reason(job_id: int) -> Optional[str]:
    """Get the failure reason of a job.

    If the job has multiple tasks, we return the first failure reason.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        reason = session.execute(
            sqlalchemy.select(spot_table.c.failure_reason).where(
                spot_table.c.spot_job_id == job_id).order_by(
                    spot_table.c.task_id.asc())).fetchall()
        reason = [r[0] for r in reason if r[0] is not None]
        if not reason:
            return None
        return reason[0]


@_init_db
def get_managed_job_tasks(job_id: int) -> List[Dict[str, Any]]:
    """Get managed job tasks for a specific managed job id from the database."""
    assert _SQLALCHEMY_ENGINE is not None

    # Join spot and job_info tables to get the job name for each task.
    # We use LEFT OUTER JOIN mainly for backward compatibility, as for an
    # existing controller before #1982, the job_info table may not exist,
    # and all the managed jobs created before will not present in the
    # job_info.
    # Note: we will get the user_hash here, but don't try to call
    # global_user_state.get_user() on it. This runs on the controller, which may
    # not have the user info. Prefer to do it on the API server side.
    query = sqlalchemy.select(spot_table, job_info_table).select_from(
        spot_table.outerjoin(
            job_info_table,
            spot_table.c.spot_job_id == job_info_table.c.spot_job_id))
    query = query.where(spot_table.c.spot_job_id == job_id)
    query = query.order_by(spot_table.c.task_id.asc())
    rows = None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.execute(query).fetchall()
    jobs = []
    for row in rows:
        job_dict = _get_jobs_dict(row._mapping)  # pylint: disable=protected-access
        job_dict['status'] = ManagedJobStatus(job_dict['status'])
        job_dict['schedule_state'] = ManagedJobScheduleState(
            job_dict['schedule_state'])
        if job_dict['job_name'] is None:
            job_dict['job_name'] = job_dict['task_name']
        job_dict['metadata'] = json.loads(job_dict['metadata'])

        # Add user YAML content for managed jobs.
        job_dict['user_yaml'] = job_dict.get('original_user_yaml_content')
        if job_dict['user_yaml'] is None:
            # Backwards compatibility - try to read from file path
            yaml_path = job_dict.get('original_user_yaml_path')
            if yaml_path:
                try:
                    with open(yaml_path, 'r', encoding='utf-8') as f:
                        job_dict['user_yaml'] = f.read()
                except (FileNotFoundError, IOError, OSError) as e:
                    logger.debug('Failed to read original user YAML for job '
                                 f'{job_id} from {yaml_path}: {e}')

        jobs.append(job_dict)
    return jobs


def _map_response_field_to_db_column(field: str):
    """Map the response field name to an actual SQLAlchemy ColumnElement.

    This ensures we never pass plain strings to SQLAlchemy 2.0 APIs like
    Select.with_only_columns().
    """
    # Explicit aliases differing from actual DB column names
    alias_mapping = {
        '_job_id': spot_table.c.job_id,  # spot.job_id
        '_task_name': spot_table.c.job_name,  # deprecated, from spot table
        'job_id': spot_table.c.spot_job_id,  # public job id -> spot.spot_job_id
        '_job_info_job_id': job_info_table.c.spot_job_id,
        'job_name': job_info_table.c.name,  # public job name -> job_info.name
    }
    if field in alias_mapping:
        return alias_mapping[field]

    # Try direct match on the `spot` table columns
    if field in spot_table.c:
        return spot_table.c[field]

    # Try direct match on the `job_info` table columns
    if field in job_info_table.c:
        return job_info_table.c[field]

    raise ValueError(f'Unknown field: {field}')


@_init_db
def get_managed_jobs_total() -> int:
    """Get the total number of managed jobs."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(sqlalchemy.func.count()  # pylint: disable=not-callable
                             ).select_from(spot_table)).fetchone()
        return result[0] if result else 0


@_init_db
def get_managed_jobs_highest_priority() -> int:
    """Get the highest priority of the managed jobs."""
    assert _SQLALCHEMY_ENGINE is not None
    query = sqlalchemy.select(sqlalchemy.func.max(
        job_info_table.c.priority)).where(
            sqlalchemy.and_(
                job_info_table.c.schedule_state.in_([
                    ManagedJobScheduleState.LAUNCHING.value,
                    ManagedJobScheduleState.ALIVE_BACKOFF.value,
                    ManagedJobScheduleState.WAITING.value,
                    ManagedJobScheduleState.ALIVE_WAITING.value,
                ]),
                job_info_table.c.priority.is_not(None),
            ))
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        priority = session.execute(query).fetchone()
        return priority[0] if priority and priority[
            0] is not None else constants.MIN_PRIORITY


def build_managed_jobs_with_filters_no_status_query(
    fields: Optional[List[str]] = None,
    job_ids: Optional[List[int]] = None,
    accessible_workspaces: Optional[List[str]] = None,
    workspace_match: Optional[str] = None,
    name_match: Optional[str] = None,
    pool_match: Optional[str] = None,
    user_hashes: Optional[List[Optional[str]]] = None,
    skip_finished: bool = False,
    count_only: bool = False,
    status_count: bool = False,
) -> sqlalchemy.Select:
    """Build a query to get managed jobs from the database with filters."""
    # Join spot and job_info tables to get the job name for each task.
    # We use LEFT OUTER JOIN mainly for backward compatibility, as for an
    # existing controller before #1982, the job_info table may not exist,
    # and all the managed jobs created before will not present in the
    # job_info.
    # Note: we will get the user_hash here, but don't try to call
    # global_user_state.get_user() on it. This runs on the controller, which may
    # not have the user info. Prefer to do it on the API server side.
    if count_only:
        query = sqlalchemy.select(sqlalchemy.func.count().label('count'))  # pylint: disable=not-callable
    elif status_count:
        query = sqlalchemy.select(spot_table.c.status,
                                  sqlalchemy.func.count().label('count'))  # pylint: disable=not-callable
    else:
        query = sqlalchemy.select(spot_table, job_info_table)
    query = query.select_from(
        spot_table.outerjoin(
            job_info_table,
            spot_table.c.spot_job_id == job_info_table.c.spot_job_id))
    if skip_finished:
        # Filter out finished jobs at the DB level. If a multi-task job is
        # partially finished, include all its tasks. We do this by first
        # selecting job_ids that have at least one non-terminal task, then
        # restricting the main query to those job_ids.
        terminal_status_values = [
            s.value for s in ManagedJobStatus.terminal_statuses()
        ]
        non_terminal_job_ids_subquery = (sqlalchemy.select(
            spot_table.c.spot_job_id).where(
                sqlalchemy.or_(
                    spot_table.c.status.is_(None),
                    sqlalchemy.not_(
                        spot_table.c.status.in_(terminal_status_values)),
                )).distinct())
        query = query.where(
            spot_table.c.spot_job_id.in_(non_terminal_job_ids_subquery))
    if not count_only and not status_count and fields:
        # Resolve requested field names to explicit ColumnElements from
        # the joined tables.
        selected_columns = [_map_response_field_to_db_column(f) for f in fields]
        query = query.with_only_columns(*selected_columns)
    if job_ids is not None:
        query = query.where(spot_table.c.spot_job_id.in_(job_ids))
    if accessible_workspaces is not None:
        query = query.where(
            job_info_table.c.workspace.in_(accessible_workspaces))
    if workspace_match is not None:
        query = query.where(
            job_info_table.c.workspace.like(f'%{workspace_match}%'))
    if name_match is not None:
        query = query.where(job_info_table.c.name.like(f'%{name_match}%'))
    if pool_match is not None:
        query = query.where(job_info_table.c.pool.like(f'%{pool_match}%'))
    if user_hashes is not None:
        query = query.where(job_info_table.c.user_hash.in_(user_hashes))
    return query


def build_managed_jobs_with_filters_query(
    fields: Optional[List[str]] = None,
    job_ids: Optional[List[int]] = None,
    accessible_workspaces: Optional[List[str]] = None,
    workspace_match: Optional[str] = None,
    name_match: Optional[str] = None,
    pool_match: Optional[str] = None,
    user_hashes: Optional[List[Optional[str]]] = None,
    statuses: Optional[List[str]] = None,
    skip_finished: bool = False,
    count_only: bool = False,
) -> sqlalchemy.Select:
    """Build a query to get managed jobs from the database with filters."""
    query = build_managed_jobs_with_filters_no_status_query(
        fields=fields,
        job_ids=job_ids,
        accessible_workspaces=accessible_workspaces,
        workspace_match=workspace_match,
        name_match=name_match,
        pool_match=pool_match,
        user_hashes=user_hashes,
        skip_finished=skip_finished,
        count_only=count_only,
    )
    if statuses is not None:
        query = query.where(spot_table.c.status.in_(statuses))
    return query


@_init_db
def get_status_count_with_filters(
    fields: Optional[List[str]] = None,
    job_ids: Optional[List[int]] = None,
    accessible_workspaces: Optional[List[str]] = None,
    workspace_match: Optional[str] = None,
    name_match: Optional[str] = None,
    pool_match: Optional[str] = None,
    user_hashes: Optional[List[Optional[str]]] = None,
    skip_finished: bool = False,
) -> Dict[str, int]:
    """Get the status count of the managed jobs with filters."""
    query = build_managed_jobs_with_filters_no_status_query(
        fields=fields,
        job_ids=job_ids,
        accessible_workspaces=accessible_workspaces,
        workspace_match=workspace_match,
        name_match=name_match,
        pool_match=pool_match,
        user_hashes=user_hashes,
        skip_finished=skip_finished,
        status_count=True,
    )
    query = query.group_by(spot_table.c.status)
    results: Dict[str, int] = {}
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.execute(query).fetchall()
        for status_value, count in rows:
            # status_value is already a string (enum value)
            results[str(status_value)] = int(count)
    return results


@_init_db
def get_managed_jobs_with_filters(
    fields: Optional[List[str]] = None,
    job_ids: Optional[List[int]] = None,
    accessible_workspaces: Optional[List[str]] = None,
    workspace_match: Optional[str] = None,
    name_match: Optional[str] = None,
    pool_match: Optional[str] = None,
    user_hashes: Optional[List[Optional[str]]] = None,
    statuses: Optional[List[str]] = None,
    skip_finished: bool = False,
    page: Optional[int] = None,
    limit: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Get managed jobs from the database with filters.

    Returns:
        A tuple containing
         - the list of managed jobs
         - the total number of managed jobs
    """
    assert _SQLALCHEMY_ENGINE is not None

    count_query = build_managed_jobs_with_filters_query(
        fields=None,
        job_ids=job_ids,
        accessible_workspaces=accessible_workspaces,
        workspace_match=workspace_match,
        name_match=name_match,
        pool_match=pool_match,
        user_hashes=user_hashes,
        statuses=statuses,
        skip_finished=skip_finished,
        count_only=True,
    )
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        total = session.execute(count_query).fetchone()[0]

    query = build_managed_jobs_with_filters_query(
        fields=fields,
        job_ids=job_ids,
        accessible_workspaces=accessible_workspaces,
        workspace_match=workspace_match,
        name_match=name_match,
        pool_match=pool_match,
        user_hashes=user_hashes,
        statuses=statuses,
        skip_finished=skip_finished,
    )
    query = query.order_by(spot_table.c.spot_job_id.desc(),
                           spot_table.c.task_id.asc())
    if page is not None and limit is not None:
        query = query.offset((page - 1) * limit).limit(limit)
    rows = None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.execute(query).fetchall()
    jobs = []
    for row in rows:
        job_dict = _get_jobs_dict(row._mapping)  # pylint: disable=protected-access
        if job_dict.get('status') is not None:
            job_dict['status'] = ManagedJobStatus(job_dict['status'])
        if job_dict.get('schedule_state') is not None:
            job_dict['schedule_state'] = ManagedJobScheduleState(
                job_dict['schedule_state'])
        if job_dict.get('job_name') is None:
            job_dict['job_name'] = job_dict.get('task_name')
        if job_dict.get('metadata') is not None:
            job_dict['metadata'] = json.loads(job_dict['metadata'])

        # Add user YAML content for managed jobs.
        job_dict['user_yaml'] = job_dict.get('original_user_yaml_content')
        if job_dict['user_yaml'] is None:
            # Backwards compatibility - try to read from file path
            yaml_path = job_dict.get('original_user_yaml_path')
            if yaml_path:
                try:
                    with open(yaml_path, 'r', encoding='utf-8') as f:
                        job_dict['user_yaml'] = f.read()
                except (FileNotFoundError, IOError, OSError) as e:
                    job_id = job_dict.get('job_id')
                    if job_id is not None:
                        logger.debug('Failed to read original user YAML for '
                                     f'job {job_id} from {yaml_path}: {e}')
                    else:
                        logger.debug('Failed to read original user YAML from '
                                     f'{yaml_path}: {e}')

        jobs.append(job_dict)
    return jobs, total


@_init_db
def get_task_name(job_id: int, task_id: int) -> str:
    """Get the task name of a job."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        task_name = session.execute(
            sqlalchemy.select(spot_table.c.task_name).where(
                sqlalchemy.and_(
                    spot_table.c.spot_job_id == job_id,
                    spot_table.c.task_id == task_id,
                ))).fetchone()
        return task_name[0]


@_init_db
def get_latest_job_id() -> Optional[int]:
    """Get the latest job id."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        job_id = session.execute(
            sqlalchemy.select(spot_table.c.spot_job_id).where(
                spot_table.c.task_id == 0).order_by(
                    spot_table.c.submitted_at.desc()).limit(1)).fetchone()
        return job_id[0] if job_id else None


@_init_db
def get_task_specs(job_id: int, task_id: int) -> Dict[str, Any]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        task_specs = session.execute(
            sqlalchemy.select(spot_table.c.specs).where(
                sqlalchemy.and_(
                    spot_table.c.spot_job_id == job_id,
                    spot_table.c.task_id == task_id,
                ))).fetchone()
        return json.loads(task_specs[0])


@_init_db
def scheduler_set_waiting(job_id: int, dag_yaml_content: str,
                          original_user_yaml_content: str,
                          env_file_content: str, priority: int):
    """Do not call without holding the scheduler lock.

    Returns: Whether this is a recovery run or not.
        If this is a recovery run, the job may already be in the WAITING
        state and the update will not change the schedule_state (hence the
        updated_count will be 0). In this case, we return True.
        Otherwise, we return False.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        updated_count = session.query(job_info_table).filter(
            sqlalchemy.and_(job_info_table.c.spot_job_id == job_id,)).update({
                job_info_table.c.schedule_state:
                    ManagedJobScheduleState.WAITING.value,
                job_info_table.c.dag_yaml_content: dag_yaml_content,
                job_info_table.c.original_user_yaml_content:
                    (original_user_yaml_content),
                job_info_table.c.env_file_content: env_file_content,
                job_info_table.c.priority: priority,
            })
        session.commit()
        assert updated_count <= 1, (job_id, updated_count)


@_init_db
def get_job_file_contents(job_id: int) -> Dict[str, Optional[str]]:
    """Return file information and stored contents for a managed job."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.execute(
            sqlalchemy.select(
                job_info_table.c.dag_yaml_path,
                job_info_table.c.env_file_path,
                job_info_table.c.dag_yaml_content,
                job_info_table.c.env_file_content,
            ).where(job_info_table.c.spot_job_id == job_id)).fetchone()

    if row is None:
        return {
            'dag_yaml_path': None,
            'env_file_path': None,
            'dag_yaml_content': None,
            'env_file_content': None,
        }

    return {
        'dag_yaml_path': row[0],
        'env_file_path': row[1],
        'dag_yaml_content': row[2],
        'env_file_content': row[3],
    }


@_init_db
def get_pool_from_job_id(job_id: int) -> Optional[str]:
    """Get the pool from the job id."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        pool = session.execute(
            sqlalchemy.select(job_info_table.c.pool).where(
                job_info_table.c.spot_job_id == job_id)).fetchone()
        return pool[0] if pool else None


@_init_db
def set_current_cluster_name(job_id: int, current_cluster_name: str) -> None:
    """Set the current cluster name for a job."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(job_info_table).filter(
            job_info_table.c.spot_job_id == job_id).update(
                {job_info_table.c.current_cluster_name: current_cluster_name})
        session.commit()


@_init_db_async
async def set_job_id_on_pool_cluster_async(job_id: int,
                                           job_id_on_pool_cluster: int) -> None:
    """Set the job id on the pool cluster for a job."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        await session.execute(
            sqlalchemy.update(job_info_table).
            where(job_info_table.c.spot_job_id == job_id).values({
                job_info_table.c.job_id_on_pool_cluster: job_id_on_pool_cluster
            }))
        await session.commit()


@_init_db
def get_pool_submit_info(job_id: int) -> Tuple[Optional[str], Optional[int]]:
    """Get the cluster name and job id on the pool from the managed job id."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        info = session.execute(
            sqlalchemy.select(
                job_info_table.c.current_cluster_name,
                job_info_table.c.job_id_on_pool_cluster).where(
                    job_info_table.c.spot_job_id == job_id)).fetchone()
        if info is None:
            return None, None
        return info[0], info[1]


@_init_db_async
async def get_pool_submit_info_async(
        job_id: int) -> Tuple[Optional[str], Optional[int]]:
    """Get the cluster name and job id on the pool from the managed job id."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.select(job_info_table.c.current_cluster_name,
                              job_info_table.c.job_id_on_pool_cluster).where(
                                  job_info_table.c.spot_job_id == job_id))
        info = result.fetchone()
        if info is None:
            return None, None
        return info[0], info[1]


@_init_db_async
async def scheduler_set_launching_async(job_id: int):
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        await session.execute(
            sqlalchemy.update(job_info_table).where(
                sqlalchemy.and_(job_info_table.c.spot_job_id == job_id)).values(
                    {
                        job_info_table.c.schedule_state:
                            ManagedJobScheduleState.LAUNCHING.value
                    }))
        await session.commit()


@_init_db_async
async def scheduler_set_alive_async(job_id: int) -> None:
    """Do not call without holding the scheduler lock."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.update(job_info_table).where(
                sqlalchemy.and_(
                    job_info_table.c.spot_job_id == job_id,
                    job_info_table.c.schedule_state ==
                    ManagedJobScheduleState.LAUNCHING.value,
                )).values({
                    job_info_table.c.schedule_state:
                        ManagedJobScheduleState.ALIVE.value
                }))
        changes = result.rowcount
        await session.commit()
        assert changes == 1, (job_id, changes)


@_init_db
def scheduler_set_done(job_id: int, idempotent: bool = False) -> None:
    """Do not call without holding the scheduler lock."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        updated_count = session.query(job_info_table).filter(
            sqlalchemy.and_(
                job_info_table.c.spot_job_id == job_id,
                job_info_table.c.schedule_state !=
                ManagedJobScheduleState.DONE.value,
            )).update({
                job_info_table.c.schedule_state:
                    ManagedJobScheduleState.DONE.value
            })
        session.commit()
        if not idempotent:
            assert updated_count == 1, (job_id, updated_count)


@_init_db
def get_job_schedule_state(job_id: int) -> ManagedJobScheduleState:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        state = session.execute(
            sqlalchemy.select(job_info_table.c.schedule_state).where(
                job_info_table.c.spot_job_id == job_id)).fetchone()[0]
        return ManagedJobScheduleState(state)


@_init_db
def get_num_launching_jobs() -> int:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        return session.execute(
            sqlalchemy.select(
                sqlalchemy.func.count()  # pylint: disable=not-callable
            ).select_from(job_info_table).where(
                sqlalchemy.and_(
                    job_info_table.c.schedule_state ==
                    ManagedJobScheduleState.LAUNCHING.value,
                    # We only count jobs that are not in the pool, because the
                    # job in the pool does not actually calling the sky.launch.
                    job_info_table.c.pool.is_(None)))).fetchone()[0]


@_init_db
def get_num_alive_jobs(pool: Optional[str] = None) -> int:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        where_conditions = [
            job_info_table.c.schedule_state.in_([
                ManagedJobScheduleState.ALIVE_WAITING.value,
                ManagedJobScheduleState.LAUNCHING.value,
                ManagedJobScheduleState.ALIVE.value,
                ManagedJobScheduleState.ALIVE_BACKOFF.value,
            ])
        ]

        if pool is not None:
            where_conditions.append(job_info_table.c.pool == pool)

        return session.execute(
            sqlalchemy.select(
                sqlalchemy.func.count()  # pylint: disable=not-callable
            ).select_from(job_info_table).where(
                sqlalchemy.and_(*where_conditions))).fetchone()[0]


@_init_db
def get_nonterminal_job_ids_by_pool(pool: str,
                                    cluster_name: Optional[str] = None
                                   ) -> List[int]:
    """Get nonterminal job ids in a pool."""
    assert _SQLALCHEMY_ENGINE is not None

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        query = sqlalchemy.select(
            spot_table.c.spot_job_id.distinct()).select_from(
                spot_table.outerjoin(
                    job_info_table,
                    spot_table.c.spot_job_id == job_info_table.c.spot_job_id))
        and_conditions = [
            ~spot_table.c.status.in_([
                status.value for status in ManagedJobStatus.terminal_statuses()
            ]),
            job_info_table.c.pool == pool,
        ]
        if cluster_name is not None:
            and_conditions.append(
                job_info_table.c.current_cluster_name == cluster_name)
        query = query.where(sqlalchemy.and_(*and_conditions)).order_by(
            spot_table.c.spot_job_id.asc())
        rows = session.execute(query).fetchall()
        job_ids = [row[0] for row in rows if row[0] is not None]
        return job_ids


@_init_db_async
async def get_waiting_job_async(
        pid: int, pid_started_at: float) -> Optional[Dict[str, Any]]:
    """Get the next job that should transition to LAUNCHING.

    Selects the highest-priority WAITING or ALIVE_WAITING job and atomically
    transitions it to LAUNCHING state to prevent race conditions.

    Returns the job information if a job was successfully transitioned to
    LAUNCHING, or None if no suitable job was found.

    Backwards compatibility note: jobs submitted before #4485 will have no
    schedule_state and will be ignored by this SQL query.
    """
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        # Select the highest priority waiting job for update (locks the row)
        select_query = sqlalchemy.select(
            job_info_table.c.spot_job_id,
            job_info_table.c.schedule_state,
            job_info_table.c.pool,
        ).where(
            job_info_table.c.schedule_state.in_([
                ManagedJobScheduleState.WAITING.value,
            ])).order_by(
                job_info_table.c.priority.desc(),
                job_info_table.c.spot_job_id.asc(),
            ).limit(1).with_for_update()

        # Execute the select with row locking
        result = await session.execute(select_query)
        waiting_job_row = result.fetchone()

        if waiting_job_row is None:
            return None

        job_id = waiting_job_row[0]
        current_state = ManagedJobScheduleState(waiting_job_row[1])
        pool = waiting_job_row[2]

        # Update the job state to LAUNCHING
        update_result = await session.execute(
            sqlalchemy.update(job_info_table).where(
                sqlalchemy.and_(
                    job_info_table.c.spot_job_id == job_id,
                    job_info_table.c.schedule_state == current_state.value,
                )).values({
                    job_info_table.c.schedule_state:
                        ManagedJobScheduleState.LAUNCHING.value,
                    job_info_table.c.controller_pid: pid,
                    job_info_table.c.controller_pid_started_at: pid_started_at,
                }))

        if update_result.rowcount != 1:
            # Update failed, rollback and return None
            await session.rollback()
            return None

        # Commit the transaction
        await session.commit()

        return {
            'job_id': job_id,
            'pool': pool,
        }


@_init_db
def get_workspace(job_id: int) -> str:
    """Get the workspace of a job."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        workspace = session.execute(
            sqlalchemy.select(job_info_table.c.workspace).where(
                job_info_table.c.spot_job_id == job_id)).fetchone()
        job_workspace = workspace[0] if workspace else None
        if job_workspace is None:
            return constants.SKYPILOT_DEFAULT_WORKSPACE
        return job_workspace


# === HA Recovery Script functions ===


@_init_db
def get_ha_recovery_script(job_id: int) -> Optional[str]:
    """Get the HA recovery script for a job."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(ha_recovery_script_table).filter_by(
            job_id=job_id).first()
    if row is None:
        return None
    return row.script


@_init_db
def set_ha_recovery_script(job_id: int, script: str) -> None:
    """Set the HA recovery script for a job."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')
        insert_stmt = insert_func(ha_recovery_script_table).values(
            job_id=job_id, script=script)
        do_update_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[ha_recovery_script_table.c.job_id],
            set_={ha_recovery_script_table.c.script: script})
        session.execute(do_update_stmt)
        session.commit()


@_init_db
def remove_ha_recovery_script(job_id: int) -> None:
    """Remove the HA recovery script for a job."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(ha_recovery_script_table).filter_by(
            job_id=job_id).delete()
        session.commit()


@_init_db_async
async def get_latest_task_id_status_async(
        job_id: int) -> Union[Tuple[int, ManagedJobStatus], Tuple[None, None]]:
    """Returns the (task id, status) of the latest task of a job."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.select(
                spot_table.c.task_id,
                spot_table.c.status,
            ).where(spot_table.c.spot_job_id == job_id).order_by(
                spot_table.c.task_id.asc()))
        id_statuses = [
            (row[0], ManagedJobStatus(row[1])) for row in result.fetchall()
        ]

    if not id_statuses:
        return None, None
    task_id, status = next(
        ((tid, st) for tid, st in id_statuses if not st.is_terminal()),
        id_statuses[-1],
    )
    return task_id, status


@_init_db_async
async def set_starting_async(job_id: int, task_id: int, run_timestamp: str,
                             submit_time: float, resources_str: str,
                             specs: Dict[str, Union[str, int]],
                             callback_func: AsyncCallbackType):
    """Set the task to starting state."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    logger.info('Launching the spot cluster...')
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.update(spot_table).where(
                sqlalchemy.and_(
                    spot_table.c.spot_job_id == job_id,
                    spot_table.c.task_id == task_id,
                    spot_table.c.status == ManagedJobStatus.PENDING.value,
                    spot_table.c.end_at.is_(None),
                )).values({
                    spot_table.c.resources: resources_str,
                    spot_table.c.submitted_at: submit_time,
                    spot_table.c.status: ManagedJobStatus.STARTING.value,
                    spot_table.c.run_timestamp: run_timestamp,
                    spot_table.c.specs: json.dumps(specs),
                }))
        count = result.rowcount
        await session.commit()
        if count != 1:
            details = await _describe_task_transition_failure(
                session, job_id, task_id)
            message = ('Failed to set the task to starting. '
                       f'({count} rows updated. {details})')
            logger.error(message)
            raise exceptions.ManagedJobStatusError(message)
    await callback_func('SUBMITTED')
    await callback_func('STARTING')


@_init_db_async
async def set_started_async(job_id: int, task_id: int, start_time: float,
                            callback_func: AsyncCallbackType):
    """Set the task to started state."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    logger.info('Job started.')
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.update(spot_table).where(
                sqlalchemy.and_(
                    spot_table.c.spot_job_id == job_id,
                    spot_table.c.task_id == task_id,
                    spot_table.c.status.in_([
                        ManagedJobStatus.STARTING.value,
                        ManagedJobStatus.PENDING.value
                    ]),
                    spot_table.c.end_at.is_(None),
                )).values({
                    spot_table.c.status: ManagedJobStatus.RUNNING.value,
                    spot_table.c.start_at: start_time,
                    spot_table.c.last_recovered_at: start_time,
                }))
        count = result.rowcount
        await session.commit()
        if count != 1:
            details = await _describe_task_transition_failure(
                session, job_id, task_id)
            message = (f'Failed to set the task to started. '
                       f'({count} rows updated. {details})')
            logger.error(message)
            raise exceptions.ManagedJobStatusError(message)
    await callback_func('STARTED')


@_init_db_async
async def get_job_status_with_task_id_async(
        job_id: int, task_id: int) -> Optional[ManagedJobStatus]:
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.select(spot_table.c.status).where(
                sqlalchemy.and_(spot_table.c.spot_job_id == job_id,
                                spot_table.c.task_id == task_id)))
        status = result.fetchone()
        return ManagedJobStatus(status[0]) if status else None


@_init_db_async
async def set_recovering_async(job_id: int, task_id: int,
                               force_transit_to_recovering: bool,
                               callback_func: AsyncCallbackType):
    """Set the task to recovering state, and update the job duration."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    logger.info('=== Recovering... ===')
    current_time = time.time()

    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        if force_transit_to_recovering:
            status_condition = spot_table.c.status.in_(
                [s.value for s in ManagedJobStatus.processing_statuses()])
        else:
            status_condition = (
                spot_table.c.status == ManagedJobStatus.RUNNING.value)

        result = await session.execute(
            sqlalchemy.update(spot_table).where(
                sqlalchemy.and_(
                    spot_table.c.spot_job_id == job_id,
                    spot_table.c.task_id == task_id,
                    status_condition,
                    spot_table.c.end_at.is_(None),
                )).values({
                    spot_table.c.status: ManagedJobStatus.RECOVERING.value,
                    spot_table.c.job_duration: sqlalchemy.case(
                        (spot_table.c.last_recovered_at >= 0,
                         spot_table.c.job_duration + current_time -
                         spot_table.c.last_recovered_at),
                        else_=spot_table.c.job_duration),
                    spot_table.c.last_recovered_at: sqlalchemy.case(
                        (spot_table.c.last_recovered_at < 0, current_time),
                        else_=spot_table.c.last_recovered_at),
                }))
        count = result.rowcount
        await session.commit()
        if count != 1:
            details = await _describe_task_transition_failure(
                session, job_id, task_id)
            message = ('Failed to set the task to recovering with '
                       'force_transit_to_recovering='
                       f'{force_transit_to_recovering}. '
                       f'({count} rows updated. {details})')
            logger.error(message)
            raise exceptions.ManagedJobStatusError(message)
    await callback_func('RECOVERING')


@_init_db_async
async def set_recovered_async(job_id: int, task_id: int, recovered_time: float,
                              callback_func: AsyncCallbackType):
    """Set the task to recovered."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.update(spot_table).where(
                sqlalchemy.and_(
                    spot_table.c.spot_job_id == job_id,
                    spot_table.c.task_id == task_id,
                    spot_table.c.status == ManagedJobStatus.RECOVERING.value,
                    spot_table.c.end_at.is_(None),
                )).values({
                    spot_table.c.status: ManagedJobStatus.RUNNING.value,
                    spot_table.c.last_recovered_at: recovered_time,
                    spot_table.c.recovery_count: spot_table.c.recovery_count +
                                                 1,
                }))
        count = result.rowcount
        await session.commit()
        if count != 1:
            details = await _describe_task_transition_failure(
                session, job_id, task_id)
            message = (f'Failed to set the task to recovered. '
                       f'({count} rows updated. {details})')
            logger.error(message)
            raise exceptions.ManagedJobStatusError(message)
    logger.info('==== Recovered. ====')
    await callback_func('RECOVERED')


@_init_db_async
async def set_succeeded_async(job_id: int, task_id: int, end_time: float,
                              callback_func: AsyncCallbackType):
    """Set the task to succeeded, if it is in a non-terminal state."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.update(spot_table).where(
                sqlalchemy.and_(
                    spot_table.c.spot_job_id == job_id,
                    spot_table.c.task_id == task_id,
                    spot_table.c.status == ManagedJobStatus.RUNNING.value,
                    spot_table.c.end_at.is_(None),
                )).values({
                    spot_table.c.status: ManagedJobStatus.SUCCEEDED.value,
                    spot_table.c.end_at: end_time,
                }))
        count = result.rowcount
        await session.commit()
        if count != 1:
            details = await _describe_task_transition_failure(
                session, job_id, task_id)
            message = (f'Failed to set the task to succeeded. '
                       f'({count} rows updated. {details})')
            logger.error(message)
            raise exceptions.ManagedJobStatusError(message)
    await callback_func('SUCCEEDED')
    logger.info('Job succeeded.')


@_init_db_async
async def set_failed_async(
    job_id: int,
    task_id: Optional[int],
    failure_type: ManagedJobStatus,
    failure_reason: str,
    callback_func: Optional[AsyncCallbackType] = None,
    end_time: Optional[float] = None,
    override_terminal: bool = False,
):
    """Set an entire job or task to failed."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    assert failure_type.is_failed(), failure_type
    end_time = time.time() if end_time is None else end_time

    fields_to_set: Dict[str, Any] = {
        spot_table.c.status: failure_type.value,
        spot_table.c.failure_reason: failure_reason,
    }
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        # Get previous status
        result = await session.execute(
            sqlalchemy.select(
                spot_table.c.status).where(spot_table.c.spot_job_id == job_id))
        previous_status_row = result.fetchone()
        previous_status = ManagedJobStatus(previous_status_row[0])
        if previous_status == ManagedJobStatus.RECOVERING:
            fields_to_set[spot_table.c.last_recovered_at] = end_time
        where_conditions = [spot_table.c.spot_job_id == job_id]
        if task_id is not None:
            where_conditions.append(spot_table.c.task_id == task_id)

        # Handle failure_reason prepending when override_terminal is True
        if override_terminal:
            # Get existing failure_reason with row lock to prevent race
            # conditions
            existing_reason_result = await session.execute(
                sqlalchemy.select(spot_table.c.failure_reason).where(
                    sqlalchemy.and_(*where_conditions)).with_for_update())
            existing_reason_row = existing_reason_result.fetchone()
            if existing_reason_row and existing_reason_row[0]:
                # Prepend new failure reason to existing one
                fields_to_set[spot_table.c.failure_reason] = (
                    failure_reason + '. Previously: ' + existing_reason_row[0])
            fields_to_set[spot_table.c.end_at] = sqlalchemy.func.coalesce(
                spot_table.c.end_at, end_time)
        else:
            fields_to_set[spot_table.c.end_at] = end_time
            where_conditions.append(spot_table.c.end_at.is_(None))
        result = await session.execute(
            sqlalchemy.update(spot_table).where(
                sqlalchemy.and_(*where_conditions)).values(fields_to_set))
        count = result.rowcount
        await session.commit()
        updated = count > 0
    if callback_func and updated:
        await callback_func('FAILED')
    logger.info(failure_reason)


@_init_db_async
async def set_cancelling_async(job_id: int, callback_func: AsyncCallbackType):
    """Set tasks in the job as cancelling, if they are in non-terminal
    states."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.update(spot_table).where(
                sqlalchemy.and_(
                    spot_table.c.spot_job_id == job_id,
                    spot_table.c.end_at.is_(None),
                )).values(
                    {spot_table.c.status: ManagedJobStatus.CANCELLING.value}))
        count = result.rowcount
        await session.commit()
        updated = count > 0
    if updated:
        logger.info('Cancelling the job...')
        await callback_func('CANCELLING')
    else:
        logger.info('Cancellation skipped, job is already terminal')


@_init_db_async
async def set_cancelled_async(job_id: int, callback_func: AsyncCallbackType):
    """Set tasks in the job as cancelled, if they are in CANCELLING state."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.update(spot_table).where(
                sqlalchemy.and_(
                    spot_table.c.spot_job_id == job_id,
                    spot_table.c.status == ManagedJobStatus.CANCELLING.value,
                )).values({
                    spot_table.c.status: ManagedJobStatus.CANCELLED.value,
                    spot_table.c.end_at: time.time(),
                }))
        count = result.rowcount
        await session.commit()
        updated = count > 0
    if updated:
        logger.info('Job cancelled.')
        await callback_func('CANCELLED')
    else:
        logger.info('Cancellation skipped, job is not CANCELLING')


@_init_db_async
async def remove_ha_recovery_script_async(job_id: int) -> None:
    """Remove the HA recovery script for a job."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        await session.execute(
            sqlalchemy.delete(ha_recovery_script_table).where(
                ha_recovery_script_table.c.job_id == job_id))
        await session.commit()


async def get_status_async(job_id: int) -> Optional[ManagedJobStatus]:
    _, status = await get_latest_task_id_status_async(job_id)
    return status


@_init_db_async
async def get_job_schedule_state_async(job_id: int) -> ManagedJobScheduleState:
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.select(job_info_table.c.schedule_state).where(
                job_info_table.c.spot_job_id == job_id))
        state = result.fetchone()[0]
        return ManagedJobScheduleState(state)


@_init_db_async
async def scheduler_set_done_async(job_id: int,
                                   idempotent: bool = False) -> None:
    """Do not call without holding the scheduler lock."""
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        result = await session.execute(
            sqlalchemy.update(job_info_table).where(
                sqlalchemy.and_(
                    job_info_table.c.spot_job_id == job_id,
                    job_info_table.c.schedule_state !=
                    ManagedJobScheduleState.DONE.value,
                )).values({
                    job_info_table.c.schedule_state:
                        ManagedJobScheduleState.DONE.value
                }))
        updated_count = result.rowcount
        await session.commit()
        if not idempotent:
            assert updated_count == 1, (job_id, updated_count)


# ==== needed for codegen ====
# functions have no use outside of codegen, remove at your own peril


@_init_db
def set_job_info(job_id: int,
                 name: str,
                 workspace: str,
                 entrypoint: str,
                 pool: Optional[str],
                 pool_hash: Optional[str],
                 user_hash: Optional[str] = None):
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')
        insert_stmt = insert_func(job_info_table).values(
            spot_job_id=job_id,
            name=name,
            schedule_state=ManagedJobScheduleState.INACTIVE.value,
            workspace=workspace,
            entrypoint=entrypoint,
            pool=pool,
            pool_hash=pool_hash,
            user_hash=user_hash,
        )
        session.execute(insert_stmt)
        session.commit()


@_init_db
def reset_jobs_for_recovery() -> None:
    """Remove controller PIDs for live jobs, allowing them to be recovered."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(job_info_table).filter(
            # PID should be set.
            job_info_table.c.controller_pid.isnot(None),
            # Schedule state should be alive.
            job_info_table.c.schedule_state.isnot(None),
            (job_info_table.c.schedule_state !=
             ManagedJobScheduleState.INVALID.value),
            (job_info_table.c.schedule_state !=
             ManagedJobScheduleState.WAITING.value),
            (job_info_table.c.schedule_state !=
             ManagedJobScheduleState.DONE.value),
        ).update({
            job_info_table.c.controller_pid: None,
            job_info_table.c.controller_pid_started_at: None,
            job_info_table.c.schedule_state:
                (ManagedJobScheduleState.WAITING.value)
        })
        session.commit()


@_init_db
def get_all_job_ids_by_name(name: Optional[str]) -> List[int]:
    """Get all job ids by name."""
    assert _SQLALCHEMY_ENGINE is not None

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        query = sqlalchemy.select(
            spot_table.c.spot_job_id.distinct()).select_from(
                spot_table.outerjoin(
                    job_info_table,
                    spot_table.c.spot_job_id == job_info_table.c.spot_job_id))
        if name is not None:
            # We match the job name from `job_info` for the jobs submitted after
            # #1982, and from `spot` for the jobs submitted before #1982, whose
            # job_info is not available.
            name_condition = sqlalchemy.or_(
                job_info_table.c.name == name,
                sqlalchemy.and_(job_info_table.c.name.is_(None),
                                spot_table.c.task_name == name))
            query = query.where(name_condition)
        query = query.order_by(spot_table.c.spot_job_id.desc())
        rows = session.execute(query).fetchall()
        job_ids = [row[0] for row in rows if row[0] is not None]
        return job_ids


@_init_db_async
async def get_task_logs_to_clean_async(retention_seconds: int,
                                       batch_size) -> List[Dict[str, Any]]:
    """Get the logs of job tasks to clean.

    The logs of a task will only cleaned when:
    - the job schedule state is DONE
    - AND the end time of the task is older than the retention period
    """

    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        now = time.time()
        result = await session.execute(
            sqlalchemy.select(
                spot_table.c.spot_job_id,
                spot_table.c.task_id,
                spot_table.c.local_log_file,
            ).select_from(
                spot_table.join(
                    job_info_table,
                    spot_table.c.spot_job_id == job_info_table.c.spot_job_id,
                )).
            where(
                sqlalchemy.and_(
                    job_info_table.c.schedule_state.is_(
                        ManagedJobScheduleState.DONE.value),
                    spot_table.c.end_at.isnot(None),
                    spot_table.c.end_at < (now - retention_seconds),
                    spot_table.c.logs_cleaned_at.is_(None),
                    # The local log file is set AFTER the task is finished,
                    # add this condition to ensure the entire log file has
                    # been written.
                    spot_table.c.local_log_file.isnot(None),
                )).limit(batch_size))
        rows = result.fetchall()
        return [{
            'job_id': row[0],
            'task_id': row[1],
            'local_log_file': row[2]
        } for row in rows]


@_init_db_async
async def get_controller_logs_to_clean_async(
        retention_seconds: int, batch_size: int) -> List[Dict[str, Any]]:
    """Get the controller logs to clean.

    The controller logs will only cleaned when:
    - the job schedule state is DONE
    - AND the end time of the latest task is older than the retention period
    """

    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        now = time.time()

        result = await session.execute(
            sqlalchemy.select(job_info_table.c.spot_job_id,).select_from(
                job_info_table.join(
                    spot_table,
                    job_info_table.c.spot_job_id == spot_table.c.spot_job_id,
                )).where(
                    sqlalchemy.and_(
                        job_info_table.c.schedule_state.is_(
                            ManagedJobScheduleState.DONE.value),
                        spot_table.c.local_log_file.isnot(None),
                        job_info_table.c.controller_logs_cleaned_at.is_(None),
                    )).group_by(
                        job_info_table.c.spot_job_id,
                        job_info_table.c.current_cluster_name,
                    ).having(
                        sqlalchemy.func.max(
                            spot_table.c.end_at).isnot(None),).having(
                                sqlalchemy.func.max(spot_table.c.end_at) < (
                                    now - retention_seconds)).limit(batch_size))
        rows = result.fetchall()
        return [{'job_id': row[0]} for row in rows]


@_init_db_async
async def set_task_logs_cleaned_async(tasks: List[Tuple[int, int]],
                                      logs_cleaned_at: float):
    """Set the task logs cleaned at."""
    if not tasks:
        return
    # Deduplicate
    task_keys = list(dict.fromkeys(tasks))
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        await session.execute(
            sqlalchemy.update(spot_table).where(
                sqlalchemy.tuple_(spot_table.c.spot_job_id,
                                  spot_table.c.task_id).in_(task_keys)).values(
                                      logs_cleaned_at=logs_cleaned_at))
        await session.commit()


@_init_db_async
async def set_controller_logs_cleaned_async(job_ids: List[int],
                                            logs_cleaned_at: float):
    """Set the controller logs cleaned at."""
    if not job_ids:
        return
    # Deduplicate
    job_ids = list(dict.fromkeys(job_ids))
    assert _SQLALCHEMY_ENGINE_ASYNC is not None
    async with sql_async.AsyncSession(_SQLALCHEMY_ENGINE_ASYNC) as session:
        await session.execute(
            sqlalchemy.update(job_info_table).where(
                job_info_table.c.spot_job_id.in_(job_ids)).values(
                    controller_logs_cleaned_at=logs_cleaned_at))
        await session.commit()
