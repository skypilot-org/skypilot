"""The database for managed jobs status."""
# TODO(zhwu): maybe use file based status instead of database, so
# that we can easily switch to a s3-based storage.
import enum
import pathlib
import sqlite3
import time
import typing
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import colorama

from sky import sky_logging
from sky.utils import db_utils

if typing.TYPE_CHECKING:
    import sky

CallbackType = Callable[[str], None]

logger = sky_logging.init_logger(__name__)


def _get_db_path() -> str:
    """Workaround to collapse multi-step Path ops for type checker.
    Ensures _DB_PATH is str, avoiding Union[Path, str] inference.
    """
    path = pathlib.Path('~/.sky/spot_jobs.db')
    path = path.expanduser().absolute()
    path.parents[0].mkdir(parents=True, exist_ok=True)
    return str(path)


_DB_PATH = _get_db_path()

# Module-level connection/cursor; thread-safe as the module is only imported
# once.
_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

# === Database schema ===
# `spot` table contains all the finest-grained tasks, including all the
# tasks of a managed job (called spot for legacy reason, as it is generalized
# from the previous managed spot jobs). All tasks of the same job will have the
# same `spot_job_id`.
# The `job_name` column is now deprecated. It now holds the task's name, i.e.,
# the same content as the `task_name` column.
# The `job_id` is now not really a job id, but a only a unique
# identifier/primary key for all the tasks. We will use `spot_job_id`
# to identify the spot job.
# TODO(zhwu): schema migration may be needed.
_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS spot (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT,
    resources TEXT,
    submitted_at FLOAT,
    status TEXT,
    run_timestamp TEXT CANDIDATE KEY,
    start_at FLOAT DEFAULT NULL,
    end_at FLOAT DEFAULT NULL,
    last_recovered_at FLOAT DEFAULT -1,
    recovery_count INTEGER DEFAULT 0,
    job_duration FLOAT DEFAULT 0,
    failure_reason TEXT,
    spot_job_id INTEGER,
    task_id INTEGER DEFAULT 0,
    task_name TEXT)""")
_CONN.commit()

db_utils.add_column_to_table(_CURSOR, _CONN, 'spot', 'failure_reason', 'TEXT')
# Create a new column `spot_job_id`, which is the same for tasks of the
# same managed job.
# The original `job_id` no longer has an actual meaning, but only a legacy
# identifier for all tasks in database.
db_utils.add_column_to_table(_CURSOR,
                             _CONN,
                             'spot',
                             'spot_job_id',
                             'INTEGER',
                             copy_from='job_id')
db_utils.add_column_to_table(_CURSOR,
                             _CONN,
                             'spot',
                             'task_id',
                             'INTEGER DEFAULT 0',
                             value_to_replace_existing_entries=0)
db_utils.add_column_to_table(_CURSOR,
                             _CONN,
                             'spot',
                             'task_name',
                             'TEXT',
                             copy_from='job_name')

# `job_info` contains the mapping from job_id to the job_name.
# In the future, it may contain more information about each job.
_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS job_info (
    spot_job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT)""")
_CONN.commit()

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
columns = [
    '_job_id',
    '_task_name',
    'resources',
    'submitted_at',
    'status',
    'run_timestamp',
    'start_at',
    'end_at',
    'last_recovered_at',
    'recovery_count',
    'job_duration',
    'failure_reason',
    'job_id',
    'task_id',
    'task_name',
    # columns from the job_info table
    '_job_info_job_id',  # This should be the same as job_id
    'job_name'
]


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
        ManagedJobStatus = [PENDING, SUBMITTED, STARTING, RUNNING, ...]
    Mapping from JobStatus to ManagedJobStatus:
        INIT            ->  STARTING/RECOVERING
        SETTING_UP      ->  RUNNING
        PENDING         ->  RUNNING
        RUNNING         ->  RUNNING
        SUCCEEDED       ->  SUCCEEDED
        FAILED          ->  FAILED
        FAILED_SETUP    ->  FAILED_SETUP
    Note that the JobStatus will not be stuck in PENDING, because each cluster
    is dedicated to a managed job, i.e. there should always be enough resource
    to run the job and the job will be immediately transitioned to RUNNING.
    """
    # PENDING: Waiting for the jobs controller to have a slot to run the
    # controller process.
    # The submitted_at timestamp of the managed job in the 'spot' table will be
    # set to the time when the job is firstly submitted by the user (set to
    # PENDING).
    PENDING = 'PENDING'
    # SUBMITTED: The jobs controller starts the controller process.
    SUBMITTED = 'SUBMITTED'
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
    # Terminal statuses
    # SUCCEEDED: The job is finished successfully.
    SUCCEEDED = 'SUCCEEDED'
    # CANCELLING: The job is requested to be cancelled by the user, and the
    # controller is cleaning up the cluster.
    CANCELLING = 'CANCELLING'
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
            cls.CANCELLING,
            cls.CANCELLED,
        ]

    @classmethod
    def failure_statuses(cls) -> List['ManagedJobStatus']:
        return [
            cls.FAILED, cls.FAILED_SETUP, cls.FAILED_PRECHECKS,
            cls.FAILED_NO_RESOURCE, cls.FAILED_CONTROLLER
        ]


_SPOT_STATUS_TO_COLOR = {
    ManagedJobStatus.PENDING: colorama.Fore.BLUE,
    ManagedJobStatus.SUBMITTED: colorama.Fore.BLUE,
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
}


# === Status transition functions ===
def set_job_name(job_id: int, name: str):
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            INSERT INTO job_info
            (spot_job_id, name)
            VALUES (?, ?)""", (job_id, name))


def set_pending(job_id: int, task_id: int, task_name: str, resources_str: str):
    """Set the task to pending state."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            INSERT INTO spot
            (spot_job_id, task_id, task_name, resources, status)
            VALUES (?, ?, ?, ?, ?)""",
            (job_id, task_id, task_name, resources_str,
             ManagedJobStatus.PENDING.value))


def set_submitted(job_id: int, task_id: int, run_timestamp: str,
                  submit_time: float, resources_str: str,
                  callback_func: CallbackType):
    """Set the task to submitted.

    Args:
        job_id: The managed job ID.
        task_id: The task ID.
        run_timestamp: The run_timestamp of the run. This will be used to
            determine the log directory of the managed task.
        submit_time: The time when the managed task is submitted.
        resources_str: The resources string of the managed task.
    """
    # Use the timestamp in the `run_timestamp` ('sky-2022-10...'), to make
    # the log directory and submission time align with each other, so as to
    # make it easier to find them based on one of the values.
    # Also, using the earlier timestamp should be closer to the term
    # `submit_at`, which represents the time the managed task is submitted.
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            UPDATE spot SET
            resources=(?),
            submitted_at=(?),
            status=(?),
            run_timestamp=(?)
            WHERE spot_job_id=(?) AND
            task_id=(?)""",
            (resources_str, submit_time, ManagedJobStatus.SUBMITTED.value,
             run_timestamp, job_id, task_id))
    callback_func('SUBMITTED')


def set_starting(job_id: int, task_id: int, callback_func: CallbackType):
    """Set the task to starting state."""
    logger.info('Launching the spot cluster...')
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            UPDATE spot SET status=(?)
            WHERE spot_job_id=(?) AND
            task_id=(?)""", (ManagedJobStatus.STARTING.value, job_id, task_id))
    callback_func('STARTING')


def set_started(job_id: int, task_id: int, start_time: float,
                callback_func: CallbackType):
    """Set the task to started state."""
    logger.info('Job started.')
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            UPDATE spot SET status=(?), start_at=(?), last_recovered_at=(?)
            WHERE spot_job_id=(?) AND
            task_id=(?)""",
            (
                ManagedJobStatus.RUNNING.value,
                start_time,
                start_time,
                job_id,
                task_id,
            ),
        )
    callback_func('STARTED')


def set_recovering(job_id: int, task_id: int, callback_func: CallbackType):
    """Set the task to recovering state, and update the job duration."""
    logger.info('=== Recovering... ===')
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
                UPDATE spot SET
                status=(?), job_duration=job_duration+(?)-last_recovered_at
                WHERE spot_job_id=(?) AND
                task_id=(?)""",
            (ManagedJobStatus.RECOVERING.value, time.time(), job_id, task_id))
    callback_func('RECOVERING')


def set_recovered(job_id: int, task_id: int, recovered_time: float,
                  callback_func: CallbackType):
    """Set the task to recovered."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            UPDATE spot SET
            status=(?), last_recovered_at=(?), recovery_count=recovery_count+1
            WHERE spot_job_id=(?) AND
            task_id=(?)""",
            (ManagedJobStatus.RUNNING.value, recovered_time, job_id, task_id))
    logger.info('==== Recovered. ====')
    callback_func('RECOVERED')


def set_succeeded(job_id: int, task_id: int, end_time: float,
                  callback_func: CallbackType):
    """Set the task to succeeded, if it is in a non-terminal state."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            UPDATE spot SET
            status=(?), end_at=(?)
            WHERE spot_job_id=(?) AND task_id=(?)
            AND end_at IS null""",
            (ManagedJobStatus.SUCCEEDED.value, end_time, job_id, task_id))

    callback_func('SUCCEEDED')
    logger.info('Job succeeded.')


def set_failed(
    job_id: int,
    task_id: Optional[int],
    failure_type: ManagedJobStatus,
    failure_reason: str,
    callback_func: Optional[CallbackType] = None,
    end_time: Optional[float] = None,
):
    """Set an entire job or task to failed, if they are in non-terminal states.

    Args:
        job_id: The job id.
        task_id: The task id. If None, all non-finished tasks of the job will
            be set to failed.
        failure_type: The failure type. One of ManagedJobStatus.FAILED_*.
        failure_reason: The failure reason.
        end_time: The end time. If None, the current time will be used.
    """
    assert failure_type.is_failed(), failure_type
    end_time = time.time() if end_time is None else end_time

    fields_to_set = {
        'end_at': end_time,
        'status': failure_type.value,
        'failure_reason': failure_reason,
    }
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        previous_status = cursor.execute(
            'SELECT status FROM spot WHERE spot_job_id=(?)',
            (job_id,)).fetchone()
        previous_status = ManagedJobStatus(previous_status[0])
        if previous_status in [ManagedJobStatus.RECOVERING]:
            # If the job is recovering, we should set the
            # last_recovered_at to the end_time, so that the
            # end_at - last_recovered_at will not be affect the job duration
            # calculation.
            fields_to_set['last_recovered_at'] = end_time
        set_str = ', '.join(f'{k}=(?)' for k in fields_to_set)
        task_str = '' if task_id is None else f' AND task_id={task_id}'

        cursor.execute(
            f"""\
            UPDATE spot SET
            {set_str}
            WHERE spot_job_id=(?){task_str} AND end_at IS null""",
            (*list(fields_to_set.values()), job_id))
    if callback_func:
        callback_func('FAILED')
    logger.info(failure_reason)


def set_cancelling(job_id: int, callback_func: CallbackType):
    """Set tasks in the job as cancelling, if they are in non-terminal states.

    task_id is not needed, because we expect the job should be cancelled
    as a whole, and we should not cancel a single task.
    """
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute(
            """\
            UPDATE spot SET
            status=(?), end_at=(?)
            WHERE spot_job_id=(?) AND end_at IS null""",
            (ManagedJobStatus.CANCELLING.value, time.time(), job_id))
        if rows.rowcount > 0:
            logger.info('Cancelling the job...')
            callback_func('CANCELLING')


def set_cancelled(job_id: int, callback_func: CallbackType):
    """Set tasks in the job as cancelled, if they are in CANCELLING state.

    The set_cancelling should be called before this function.
    """
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute(
            """\
            UPDATE spot SET
            status=(?), end_at=(?)
            WHERE spot_job_id=(?) AND status=(?)""",
            (ManagedJobStatus.CANCELLED.value, time.time(), job_id,
             ManagedJobStatus.CANCELLING.value))
        if rows.rowcount > 0:
            logger.info('Job cancelled.')
            callback_func('CANCELLED')


# ======== utility functions ========
def get_nonterminal_job_ids_by_name(name: Optional[str]) -> List[int]:
    """Get non-terminal job ids by name."""
    statuses = ', '.join(['?'] * len(ManagedJobStatus.terminal_statuses()))
    field_values = [
        status.value for status in ManagedJobStatus.terminal_statuses()
    ]

    name_filter = ''
    if name is not None:
        # We match the job name from `job_info` for the jobs submitted after
        # #1982, and from `spot` for the jobs submitted before #1982, whose
        # job_info is not available.
        name_filter = ('AND (job_info.name=(?) OR '
                       '(job_info.name IS NULL AND spot.task_name=(?)))')
        field_values.extend([name, name])

    # Left outer join is used here instead of join, because the job_info does
    # not contain the managed jobs submitted before #1982.
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute(
            f"""\
            SELECT DISTINCT spot.spot_job_id
            FROM spot
            LEFT OUTER JOIN job_info
            ON spot.spot_job_id=job_info.spot_job_id
            WHERE status NOT IN
            ({statuses})
            {name_filter}
            ORDER BY spot.spot_job_id DESC""", field_values).fetchall()
        job_ids = [row[0] for row in rows if row[0] is not None]
        return job_ids


def _get_all_task_ids_statuses(
        job_id: int) -> List[Tuple[int, ManagedJobStatus]]:
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        id_statuses = cursor.execute(
            """\
            SELECT task_id, status FROM spot
            WHERE spot_job_id=(?)
            ORDER BY task_id ASC""", (job_id,)).fetchall()
        return [(row[0], ManagedJobStatus(row[1])) for row in id_statuses]


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
    if len(id_statuses) == 0:
        return None, None
    task_id, status = id_statuses[-1]
    for task_id, status in id_statuses:
        if not status.is_terminal():
            break
    return task_id, status


def get_status(job_id: int) -> Optional[ManagedJobStatus]:
    _, status = get_latest_task_id_status(job_id)
    return status


def get_failure_reason(job_id: int) -> Optional[str]:
    """Get the failure reason of a job.

    If the job has multiple tasks, we return the first failure reason.
    """
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        reason = cursor.execute(
            """\
            SELECT failure_reason FROM spot
            WHERE spot_job_id=(?)
            ORDER BY task_id ASC""", (job_id,)).fetchall()
        reason = [r[0] for r in reason if r[0] is not None]
        if len(reason) == 0:
            return None
        return reason[0]


def get_managed_jobs(job_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get managed jobs from the database."""
    job_filter = '' if job_id is None else f'WHERE spot.spot_job_id={job_id}'

    # Join spot and job_info tables to get the job name for each task.
    # We use LEFT OUTER JOIN mainly for backward compatibility, as for an
    # existing controller before #1982, the job_info table may not exist,
    # and all the managed jobs created before will not present in the
    # job_info.
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute(f"""\
            SELECT *
            FROM spot
            LEFT OUTER JOIN job_info
            ON spot.spot_job_id=job_info.spot_job_id
            {job_filter}
            ORDER BY spot.spot_job_id DESC, spot.task_id ASC""").fetchall()
        jobs = []
        for row in rows:
            job_dict = dict(zip(columns, row))
            job_dict['status'] = ManagedJobStatus(job_dict['status'])
            if job_dict['job_name'] is None:
                job_dict['job_name'] = job_dict['task_name']
            jobs.append(job_dict)
        return jobs


def get_task_name(job_id: int, task_id: int) -> str:
    """Get the task name of a job."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        task_name = cursor.execute(
            """\
            SELECT task_name FROM spot
            WHERE spot_job_id=(?)
            AND task_id=(?)""", (job_id, task_id)).fetchone()
        return task_name[0]


def get_latest_job_id() -> Optional[int]:
    """Get the latest job id."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute("""\
            SELECT spot_job_id FROM spot
            WHERE task_id=0
            ORDER BY submitted_at DESC LIMIT 1""")
        for (job_id,) in rows:
            return job_id
        return None
