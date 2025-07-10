"""The database for managed jobs status."""
# TODO(zhwu): maybe use file based status instead of database, so
# that we can easily switch to a s3-based storage.
import enum
import functools
import json
import os
import pathlib
import threading
import time
import typing
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import colorama
import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext import declarative

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import db_utils

if typing.TYPE_CHECKING:
    from sqlalchemy.engine import row

    import sky

CallbackType = Callable[[str], None]

logger = sky_logging.init_logger(__name__)

_SQLALCHEMY_ENGINE: Optional[sqlalchemy.engine.Engine] = None
_DB_INIT_LOCK = threading.Lock()

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
    sqlalchemy.Column('spot_job_id', sqlalchemy.Integer),
    sqlalchemy.Column('task_id', sqlalchemy.Integer, server_default='0'),
    sqlalchemy.Column('task_name', sqlalchemy.Text),
    sqlalchemy.Column('specs', sqlalchemy.Text),
    sqlalchemy.Column('local_log_file', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('metadata', sqlalchemy.Text, server_default='{}'),
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
    sqlalchemy.Column('dag_yaml_path', sqlalchemy.Text),
    sqlalchemy.Column('env_file_path', sqlalchemy.Text),
    sqlalchemy.Column('user_hash', sqlalchemy.Text),
    sqlalchemy.Column('workspace', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('priority',
                      sqlalchemy.Integer,
                      server_default=str(constants.DEFAULT_PRIORITY)),
    sqlalchemy.Column('entrypoint', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('original_user_yaml_path',
                      sqlalchemy.Text,
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
                session.commit()
        except sqlalchemy_exc.OperationalError as e:
            if 'database is locked' not in str(e):
                raise
            # If the database is locked, it is OK to continue, as the WAL mode
            # is not critical and is likely to be enabled by other processes.

    # Create tables if they don't exist
    db_utils.add_tables_to_db_sqlalchemy(Base.metadata, engine)

    # Backward compatibility: add columns that not exist in older databases
    with orm.Session(engine) as session:
        db_utils.add_column_to_table_sqlalchemy(session, 'spot',
                                                'failure_reason',
                                                sqlalchemy.Text())
        db_utils.add_column_to_table_sqlalchemy(session,
                                                'spot',
                                                'spot_job_id',
                                                sqlalchemy.Integer(),
                                                copy_from='job_id')
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'spot',
            'task_id',
            sqlalchemy.Integer(),
            default_statement='DEFAULT 0',
            value_to_replace_existing_entries=0)
        db_utils.add_column_to_table_sqlalchemy(session,
                                                'spot',
                                                'task_name',
                                                sqlalchemy.Text(),
                                                copy_from='job_name')
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'spot',
            'specs',
            sqlalchemy.Text(),
            value_to_replace_existing_entries=json.dumps({
                'max_restarts_on_errors': 0,
            }))
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'spot',
            'local_log_file',
            sqlalchemy.Text(),
            default_statement='DEFAULT NULL')

        db_utils.add_column_to_table_sqlalchemy(
            session,
            'spot',
            'metadata',
            sqlalchemy.Text(),
            default_statement='DEFAULT \'{}\'',
            value_to_replace_existing_entries='{}')

        db_utils.add_column_to_table_sqlalchemy(session, 'job_info',
                                                'schedule_state',
                                                sqlalchemy.Text())
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'job_info',
            'controller_pid',
            sqlalchemy.Integer(),
            default_statement='DEFAULT NULL')
        db_utils.add_column_to_table_sqlalchemy(session, 'job_info',
                                                'dag_yaml_path',
                                                sqlalchemy.Text())
        db_utils.add_column_to_table_sqlalchemy(session, 'job_info',
                                                'env_file_path',
                                                sqlalchemy.Text())
        db_utils.add_column_to_table_sqlalchemy(session, 'job_info',
                                                'user_hash', sqlalchemy.Text())
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'job_info',
            'workspace',
            sqlalchemy.Text(),
            default_statement='DEFAULT NULL',
            value_to_replace_existing_entries='default')
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'job_info',
            'priority',
            sqlalchemy.Integer(),
            value_to_replace_existing_entries=constants.DEFAULT_PRIORITY)
        db_utils.add_column_to_table_sqlalchemy(session, 'job_info',
                                                'entrypoint', sqlalchemy.Text())
        db_utils.add_column_to_table_sqlalchemy(session, 'job_info',
                                                'original_user_yaml_path',
                                                sqlalchemy.Text())
        session.commit()


def initialize_and_get_db() -> sqlalchemy.engine.Engine:
    global _SQLALCHEMY_ENGINE
    if _SQLALCHEMY_ENGINE is not None:
        return _SQLALCHEMY_ENGINE
    with _DB_INIT_LOCK:
        if _SQLALCHEMY_ENGINE is None:
            conn_string = None
            if os.environ.get(constants.ENV_VAR_IS_SKYPILOT_SERVER) is not None:
                conn_string = skypilot_config.get_nested(('db',), None)
            if conn_string:
                logger.debug(f'using db URI from {conn_string}')
                engine = sqlalchemy.create_engine(conn_string,
                                                  poolclass=sqlalchemy.NullPool)
            else:
                db_path = os.path.expanduser('~/.sky/spot_jobs.db')
                pathlib.Path(db_path).parents[0].mkdir(parents=True,
                                                       exist_ok=True)
                engine = sqlalchemy.create_engine('sqlite:///' + db_path)
            create_table(engine)
            _SQLALCHEMY_ENGINE = engine
    return _SQLALCHEMY_ENGINE


def _init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        initialize_and_get_db()
        return func(*args, **kwargs)

    return wrapper


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
    return {
        '_job_id': r['job_id'],  # from spot table
        '_task_name': r['job_name'],  # deprecated, from spot table
        'resources': r['resources'],
        'submitted_at': r['submitted_at'],
        'status': r['status'],
        'run_timestamp': r['run_timestamp'],
        'start_at': r['start_at'],
        'end_at': r['end_at'],
        'last_recovered_at': r['last_recovered_at'],
        'recovery_count': r['recovery_count'],
        'job_duration': r['job_duration'],
        'failure_reason': r['failure_reason'],
        'job_id': r[spot_table.c.spot_job_id],  # ambiguous, use table.column
        'task_id': r['task_id'],
        'task_name': r['task_name'],
        'specs': r['specs'],
        'local_log_file': r['local_log_file'],
        'metadata': r['metadata'],
        # columns from job_info table (some may be None for legacy jobs)
        '_job_info_job_id': r[job_info_table.c.spot_job_id
                             ],  # ambiguous, use table.column
        'job_name': r['name'],  # from job_info table
        'schedule_state': r['schedule_state'],
        'controller_pid': r['controller_pid'],
        'dag_yaml_path': r['dag_yaml_path'],
        'env_file_path': r['env_file_path'],
        'user_hash': r['user_hash'],
        'workspace': r['workspace'],
        'priority': r['priority'],
        'entrypoint': r['entrypoint'],
        'original_user_yaml_path': r['original_user_yaml_path'],
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


# === Status transition functions ===
@_init_db
def set_job_info(job_id: int, name: str, workspace: str, entrypoint: str):
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
            entrypoint=entrypoint)
        session.execute(insert_stmt)
        session.commit()


@_init_db
def set_job_info_without_job_id(name: str, workspace: str,
                                entrypoint: str) -> int:
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


@_init_db
def set_starting(job_id: int, task_id: int, run_timestamp: str,
                 submit_time: float, resources_str: str,
                 specs: Dict[str, Union[str,
                                        int]], callback_func: CallbackType):
    """Set the task to starting state.

    Args:
        job_id: The managed job ID.
        task_id: The task ID.
        run_timestamp: The run_timestamp of the run. This will be used to
        determine the log directory of the managed task.
        submit_time: The time when the managed task is submitted.
        resources_str: The resources string of the managed task.
        specs: The specs of the managed task.
        callback_func: The callback function.
    """
    assert _SQLALCHEMY_ENGINE is not None
    # Use the timestamp in the `run_timestamp` ('sky-2022-10...'), to make
    # the log directory and submission time align with each other, so as to
    # make it easier to find them based on one of the values.
    # Also, using the earlier timestamp should be closer to the term
    # `submit_at`, which represents the time the managed task is submitted.
    logger.info('Launching the spot cluster...')
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(spot_table).filter(
            sqlalchemy.and_(
                spot_table.c.spot_job_id == job_id,
                spot_table.c.task_id == task_id,
                spot_table.c.status == ManagedJobStatus.PENDING.value,
                spot_table.c.end_at.is_(None),
            )).update({
                spot_table.c.resources: resources_str,
                spot_table.c.submitted_at: submit_time,
                spot_table.c.status: ManagedJobStatus.STARTING.value,
                spot_table.c.run_timestamp: run_timestamp,
                spot_table.c.specs: json.dumps(specs),
            })
        session.commit()
        if count != 1:
            raise exceptions.ManagedJobStatusError(
                'Failed to set the task to starting. '
                f'({count} rows updated)')
    # SUBMITTED is no longer used, but we keep it for backward compatibility.
    # TODO(cooperc): remove this in v0.12.0
    callback_func('SUBMITTED')
    callback_func('STARTING')


@_init_db
def set_backoff_pending(job_id: int, task_id: int):
    """Set the task to PENDING state if it is in backoff.

    This should only be used to transition from STARTING or RECOVERING back to
    PENDING.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(spot_table).filter(
            sqlalchemy.and_(
                spot_table.c.spot_job_id == job_id,
                spot_table.c.task_id == task_id,
                spot_table.c.status.in_([
                    ManagedJobStatus.STARTING.value,
                    ManagedJobStatus.RECOVERING.value
                ]),
                spot_table.c.end_at.is_(None),
            )).update({spot_table.c.status: ManagedJobStatus.PENDING.value})
        session.commit()
        logger.debug('back to PENDING')
        if count != 1:
            raise exceptions.ManagedJobStatusError(
                'Failed to set the task back to pending. '
                f'({count} rows updated)')
    # Do not call callback_func here, as we don't use the callback for PENDING.


@_init_db
def set_restarting(job_id: int, task_id: int, recovering: bool):
    """Set the task back to STARTING or RECOVERING from PENDING.

    This should not be used for the initial transition from PENDING to STARTING.
    In that case, use set_starting instead. This function should only be used
    after using set_backoff_pending to transition back to PENDING during
    launch retry backoff.
    """
    assert _SQLALCHEMY_ENGINE is not None
    target_status = ManagedJobStatus.STARTING.value
    if recovering:
        target_status = ManagedJobStatus.RECOVERING.value
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(spot_table).filter(
            sqlalchemy.and_(
                spot_table.c.spot_job_id == job_id,
                spot_table.c.task_id == task_id,
                spot_table.c.status == ManagedJobStatus.PENDING.value,
                spot_table.c.end_at.is_(None),
            )).update({spot_table.c.status: target_status})
        session.commit()
        logger.debug(f'back to {target_status}')
        if count != 1:
            raise exceptions.ManagedJobStatusError(
                f'Failed to set the task back to {target_status}. '
                f'({count} rows updated)')
    # Do not call callback_func here, as it should only be invoked for the
    # initial (pre-`set_backoff_pending`) transition to STARTING or RECOVERING.


@_init_db
def set_started(job_id: int, task_id: int, start_time: float,
                callback_func: CallbackType):
    """Set the task to started state."""
    assert _SQLALCHEMY_ENGINE is not None
    logger.info('Job started.')
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(spot_table).filter(
            sqlalchemy.and_(
                spot_table.c.spot_job_id == job_id,
                spot_table.c.task_id == task_id,
                spot_table.c.status.in_([
                    ManagedJobStatus.STARTING.value,
                    # If the task is empty, we will jump straight
                    # from PENDING to RUNNING
                    ManagedJobStatus.PENDING.value
                ]),
                spot_table.c.end_at.is_(None),
            )).update({
                spot_table.c.status: ManagedJobStatus.RUNNING.value,
                spot_table.c.start_at: start_time,
                spot_table.c.last_recovered_at: start_time,
            })
        session.commit()
        if count != 1:
            raise exceptions.ManagedJobStatusError(
                f'Failed to set the task to started. '
                f'({count} rows updated)')
    callback_func('STARTED')


@_init_db
def set_recovering(job_id: int, task_id: int, force_transit_to_recovering: bool,
                   callback_func: CallbackType):
    """Set the task to recovering state, and update the job duration."""
    assert _SQLALCHEMY_ENGINE is not None
    logger.info('=== Recovering... ===')
    # NOTE: if we are resuming from a controller failure and the previous status
    # is STARTING, the initial value of `last_recovered_at` might not be set
    # yet (default value -1). In this case, we should not add current timestamp.
    # Otherwise, the job duration will be incorrect (~55 years from 1970).
    current_time = time.time()

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if force_transit_to_recovering:
            # For the HA job controller, it is possible that the jobs came from
            # any processing status to recovering. But it should not be any
            # terminal status as such jobs will not be recovered; and it should
            # not be CANCELLING as we will directly trigger a cleanup.
            status_condition = spot_table.c.status.in_(
                [s.value for s in ManagedJobStatus.processing_statuses()])
        else:
            status_condition = (
                spot_table.c.status == ManagedJobStatus.RUNNING.value)

        count = session.query(spot_table).filter(
            sqlalchemy.and_(
                spot_table.c.spot_job_id == job_id,
                spot_table.c.task_id == task_id,
                status_condition,
                spot_table.c.end_at.is_(None),
            )).update({
                spot_table.c.status: ManagedJobStatus.RECOVERING.value,
                spot_table.c.job_duration: sqlalchemy.case(
                    (spot_table.c.last_recovered_at >= 0,
                     spot_table.c.job_duration + current_time -
                     spot_table.c.last_recovered_at),
                    else_=spot_table.c.job_duration),
                spot_table.c.last_recovered_at: sqlalchemy.case(
                    (spot_table.c.last_recovered_at < 0, current_time),
                    else_=spot_table.c.last_recovered_at),
            })
        session.commit()
        if count != 1:
            raise exceptions.ManagedJobStatusError(
                f'Failed to set the task to recovering. '
                f'({count} rows updated)')
    callback_func('RECOVERING')


@_init_db
def set_recovered(job_id: int, task_id: int, recovered_time: float,
                  callback_func: CallbackType):
    """Set the task to recovered."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(spot_table).filter(
            sqlalchemy.and_(
                spot_table.c.spot_job_id == job_id,
                spot_table.c.task_id == task_id,
                spot_table.c.status == ManagedJobStatus.RECOVERING.value,
                spot_table.c.end_at.is_(None),
            )).update({
                spot_table.c.status: ManagedJobStatus.RUNNING.value,
                spot_table.c.last_recovered_at: recovered_time,
                spot_table.c.recovery_count: spot_table.c.recovery_count + 1,
            })
        session.commit()
        if count != 1:
            raise exceptions.ManagedJobStatusError(
                f'Failed to set the task to recovered. '
                f'({count} rows updated)')
    logger.info('==== Recovered. ====')
    callback_func('RECOVERED')


@_init_db
def set_succeeded(job_id: int, task_id: int, end_time: float,
                  callback_func: CallbackType):
    """Set the task to succeeded, if it is in a non-terminal state."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(spot_table).filter(
            sqlalchemy.and_(
                spot_table.c.spot_job_id == job_id,
                spot_table.c.task_id == task_id,
                spot_table.c.status == ManagedJobStatus.RUNNING.value,
                spot_table.c.end_at.is_(None),
            )).update({
                spot_table.c.status: ManagedJobStatus.SUCCEEDED.value,
                spot_table.c.end_at: end_time,
            })
        session.commit()
        if count != 1:
            raise exceptions.ManagedJobStatusError(
                f'Failed to set the task to succeeded. '
                f'({count} rows updated)')
    callback_func('SUCCEEDED')
    logger.info('Job succeeded.')


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
        if override_terminal:
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
def set_cancelling(job_id: int, callback_func: CallbackType):
    """Set tasks in the job as cancelling, if they are in non-terminal states.

    task_id is not needed, because we expect the job should be cancelled
    as a whole, and we should not cancel a single task.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(spot_table).filter(
            sqlalchemy.and_(
                spot_table.c.spot_job_id == job_id,
                spot_table.c.end_at.is_(None),
            )).update({spot_table.c.status: ManagedJobStatus.CANCELLING.value})
        session.commit()
        updated = count > 0
    if updated:
        logger.info('Cancelling the job...')
        callback_func('CANCELLING')
    else:
        logger.info('Cancellation skipped, job is already terminal')


@_init_db
def set_cancelled(job_id: int, callback_func: CallbackType):
    """Set tasks in the job as cancelled, if they are in CANCELLING state.

    The set_cancelling should be called before this function.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(spot_table).filter(
            sqlalchemy.and_(
                spot_table.c.spot_job_id == job_id,
                spot_table.c.status == ManagedJobStatus.CANCELLING.value,
            )).update({
                spot_table.c.status: ManagedJobStatus.CANCELLED.value,
                spot_table.c.end_at: time.time(),
            })
        session.commit()
        updated = count > 0
    if updated:
        logger.info('Job cancelled.')
        callback_func('CANCELLED')
    else:
        logger.info('Cancellation skipped, job is not CANCELLING')


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
                                    all_users: bool = False) -> List[int]:
    """Get non-terminal job ids by name."""
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
            where_conditions.append(
                job_info_table.c.user_hash == common_utils.get_user_hash())
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
def get_schedule_live_jobs(job_id: Optional[int]) -> List[Dict[str, Any]]:
    """Get jobs from the database that have a live schedule_state.

    This should return job(s) that are not INACTIVE, WAITING, or DONE.  So a
    returned job should correspond to a live job controller process, with one
    exception: the job may have just transitioned from WAITING to LAUNCHING, but
    the controller process has not yet started.
    """
    assert _SQLALCHEMY_ENGINE is not None

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        query = sqlalchemy.select(
            job_info_table.c.spot_job_id,
            job_info_table.c.schedule_state,
            job_info_table.c.controller_pid,
        ).where(~job_info_table.c.schedule_state.in_([
            ManagedJobScheduleState.INACTIVE.value,
            ManagedJobScheduleState.WAITING.value,
            ManagedJobScheduleState.DONE.value,
        ]))

        if job_id is not None:
            query = query.where(job_info_table.c.spot_job_id == job_id)

        query = query.order_by(job_info_table.c.spot_job_id.desc())

        rows = session.execute(query).fetchall()
        jobs = []
        for row in rows:
            job_dict = {
                'job_id': row[0],
                'schedule_state': ManagedJobScheduleState(row[1]),
                'controller_pid': row[2],
            }
            jobs.append(job_dict)
        return jobs


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
def get_job_status_with_task_id(job_id: int,
                                task_id: int) -> Optional[ManagedJobStatus]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        status = session.execute(
            sqlalchemy.select(spot_table.c.status).where(
                sqlalchemy.and_(spot_table.c.spot_job_id == job_id,
                                spot_table.c.task_id == task_id))).fetchone()
        return ManagedJobStatus(status[0]) if status else None


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
def get_managed_jobs(job_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get managed jobs from the database."""
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
    if job_id is not None:
        query = query.where(spot_table.c.spot_job_id == job_id)
    query = query.order_by(spot_table.c.spot_job_id.desc(),
                           spot_table.c.task_id.asc())
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
        yaml_path = job_dict.get('original_user_yaml_path')
        if yaml_path:
            try:
                with open(yaml_path, 'r', encoding='utf-8') as f:
                    job_dict['user_yaml'] = f.read()
            except (FileNotFoundError, IOError, OSError):
                job_dict['user_yaml'] = None
        else:
            job_dict['user_yaml'] = None

        jobs.append(job_dict)
    return jobs


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
def get_local_log_file(job_id: int, task_id: Optional[int]) -> Optional[str]:
    """Get the local log directory for a job."""
    assert _SQLALCHEMY_ENGINE is not None

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        where_conditions = [spot_table.c.spot_job_id == job_id]
        if task_id is not None:
            where_conditions.append(spot_table.c.task_id == task_id)
        local_log_file = session.execute(
            sqlalchemy.select(spot_table.c.local_log_file).where(
                sqlalchemy.and_(*where_conditions))).fetchone()
        return local_log_file[-1] if local_log_file else None


# === Scheduler state functions ===
# Only the scheduler should call these functions. They may require holding the
# scheduler lock to work correctly.


@_init_db
def scheduler_set_waiting(job_id: int, dag_yaml_path: str,
                          original_user_yaml_path: str, env_file_path: str,
                          user_hash: str, priority: int) -> bool:
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
            sqlalchemy.and_(
                job_info_table.c.spot_job_id == job_id,
                job_info_table.c.schedule_state ==
                ManagedJobScheduleState.INACTIVE.value,
            )
        ).update({
            job_info_table.c.schedule_state:
                ManagedJobScheduleState.WAITING.value,
            job_info_table.c.dag_yaml_path: dag_yaml_path,
            job_info_table.c.original_user_yaml_path: original_user_yaml_path,
            job_info_table.c.env_file_path: env_file_path,
            job_info_table.c.user_hash: user_hash,
            job_info_table.c.priority: priority,
        })
        session.commit()
        # For a recovery run, the job may already be in the WAITING state.
        assert updated_count <= 1, (job_id, updated_count)
        return updated_count == 0


@_init_db
def scheduler_set_launching(job_id: int,
                            current_state: ManagedJobScheduleState) -> None:
    """Do not call without holding the scheduler lock."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        updated_count = session.query(job_info_table).filter(
            sqlalchemy.and_(
                job_info_table.c.spot_job_id == job_id,
                job_info_table.c.schedule_state == current_state.value,
            )).update({
                job_info_table.c.schedule_state:
                    ManagedJobScheduleState.LAUNCHING.value
            })
        session.commit()
        assert updated_count == 1, (job_id, updated_count)


@_init_db
def scheduler_set_alive(job_id: int) -> None:
    """Do not call without holding the scheduler lock."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        updated_count = session.query(job_info_table).filter(
            sqlalchemy.and_(
                job_info_table.c.spot_job_id == job_id,
                job_info_table.c.schedule_state ==
                ManagedJobScheduleState.LAUNCHING.value,
            )).update({
                job_info_table.c.schedule_state:
                    ManagedJobScheduleState.ALIVE.value
            })
        session.commit()
        assert updated_count == 1, (job_id, updated_count)


@_init_db
def scheduler_set_alive_backoff(job_id: int) -> None:
    """Do not call without holding the scheduler lock."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        updated_count = session.query(job_info_table).filter(
            sqlalchemy.and_(
                job_info_table.c.spot_job_id == job_id,
                job_info_table.c.schedule_state ==
                ManagedJobScheduleState.LAUNCHING.value,
            )).update({
                job_info_table.c.schedule_state:
                    ManagedJobScheduleState.ALIVE_BACKOFF.value
            })
        session.commit()
        assert updated_count == 1, (job_id, updated_count)


@_init_db
def scheduler_set_alive_waiting(job_id: int) -> None:
    """Do not call without holding the scheduler lock."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        updated_count = session.query(job_info_table).filter(
            sqlalchemy.and_(
                job_info_table.c.spot_job_id == job_id,
                job_info_table.c.schedule_state.in_([
                    ManagedJobScheduleState.ALIVE.value,
                    ManagedJobScheduleState.ALIVE_BACKOFF.value,
                ]))).update({
                    job_info_table.c.schedule_state:
                        ManagedJobScheduleState.ALIVE_WAITING.value
                })
        session.commit()
        assert updated_count == 1, (job_id, updated_count)


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
def set_job_controller_pid(job_id: int, pid: int):
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        updated_count = session.query(job_info_table).filter_by(
            spot_job_id=job_id).update({job_info_table.c.controller_pid: pid})
        session.commit()
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
                job_info_table.c.schedule_state ==
                ManagedJobScheduleState.LAUNCHING.value)).fetchone()[0]


@_init_db
def get_num_alive_jobs() -> int:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        return session.execute(
            sqlalchemy.select(
                sqlalchemy.func.count()  # pylint: disable=not-callable
            ).select_from(job_info_table).where(
                job_info_table.c.schedule_state.in_([
                    ManagedJobScheduleState.ALIVE_WAITING.value,
                    ManagedJobScheduleState.LAUNCHING.value,
                    ManagedJobScheduleState.ALIVE.value,
                    ManagedJobScheduleState.ALIVE_BACKOFF.value,
                ]))).fetchone()[0]


@_init_db
def get_waiting_job() -> Optional[Dict[str, Any]]:
    """Get the next job that should transition to LAUNCHING.

    Selects the highest-priority WAITING or ALIVE_WAITING job, provided its
    priority is greater than or equal to any currently LAUNCHING or
    ALIVE_BACKOFF job.

    Backwards compatibility note: jobs submitted before #4485 will have no
    schedule_state and will be ignored by this SQL query.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # Get the highest-priority WAITING or ALIVE_WAITING job whose priority
        # is greater than or equal to the highest priority LAUNCHING or
        # ALIVE_BACKOFF job's priority.
        # First, get the max priority of LAUNCHING or ALIVE_BACKOFF jobs
        max_priority_subquery = sqlalchemy.select(
            sqlalchemy.func.max(job_info_table.c.priority)).where(
                job_info_table.c.schedule_state.in_([
                    ManagedJobScheduleState.LAUNCHING.value,
                    ManagedJobScheduleState.ALIVE_BACKOFF.value,
                ])).scalar_subquery()
        # Main query for waiting jobs
        query = sqlalchemy.select(
            job_info_table.c.spot_job_id,
            job_info_table.c.schedule_state,
            job_info_table.c.dag_yaml_path,
            job_info_table.c.env_file_path,
        ).where(
            sqlalchemy.and_(
                job_info_table.c.schedule_state.in_([
                    ManagedJobScheduleState.WAITING.value,
                    ManagedJobScheduleState.ALIVE_WAITING.value,
                ]),
                job_info_table.c.priority >= sqlalchemy.func.coalesce(
                    max_priority_subquery, 0),
            )).order_by(
                job_info_table.c.priority.desc(),
                job_info_table.c.spot_job_id.asc(),
            ).limit(1)
        waiting_job_row = session.execute(query).fetchone()
        if waiting_job_row is None:
            return None

        return {
            'job_id': waiting_job_row[0],
            'schedule_state': ManagedJobScheduleState(waiting_job_row[1]),
            'dag_yaml_path': waiting_job_row[2],
            'env_file_path': waiting_job_row[3],
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
