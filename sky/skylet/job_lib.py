"""Utilities for jobs on a remote cluster, backed by a sqlite database.

This is a remote utility module that provides job queue functionality.
"""
import enum
import functools
import getpass
import json
import os
import pathlib
import shlex
import signal
import sqlite3
import threading
import time
import typing
from typing import Any, Dict, List, Optional, Sequence, Tuple

import colorama
import filelock

from sky import global_user_state
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import message_utils
from sky.utils import subprocess_utils
from sky.utils.db import db_utils

if typing.TYPE_CHECKING:
    import psutil

    from sky.schemas.generated import jobsv1_pb2
else:
    psutil = adaptors_common.LazyImport('psutil')
    jobsv1_pb2 = adaptors_common.LazyImport('sky.schemas.generated.jobsv1_pb2')

logger = sky_logging.init_logger(__name__)

_LINUX_NEW_LINE = '\n'
_JOB_STATUS_LOCK = '~/.sky/locks/.job_{}.lock'
# JOB_CMD_IDENTIFIER is used for identifying the process retrieved
# with pid is the same driver process to guard against the case where
# the same pid is reused by a different process.
JOB_CMD_IDENTIFIER = 'echo "SKYPILOT_JOB_ID <{}>"'


def _get_lock_path(job_id: int) -> str:
    lock_path = os.path.expanduser(_JOB_STATUS_LOCK.format(job_id))
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    return lock_path


class JobInfoLoc(enum.IntEnum):
    """Job Info's Location in the DB record"""
    JOB_ID = 0
    JOB_NAME = 1
    USERNAME = 2
    SUBMITTED_AT = 3
    STATUS = 4
    RUN_TIMESTAMP = 5
    START_AT = 6
    END_AT = 7
    RESOURCES = 8
    PID = 9
    LOG_PATH = 10
    METADATA = 11


def create_table(cursor, conn):
    # Enable WAL mode to avoid locking issues.
    # See: issue #3863, #1441 and PR #1509
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

    # Pid column is used for keeping track of the driver process of a job. It
    # can be in three states:
    # -1: The job was submitted with SkyPilot older than #4318, where we use
    #     ray job submit to submit the job, i.e. no pid is recorded. This is for
    #     backward compatibility and should be removed after 0.10.0.
    # 0: The job driver process has never been started. When adding a job with
    #    INIT state, the pid will be set to 0 (the default -1 value is just for
    #    backward compatibility).
    # >=0: The job has been started. The pid is the driver process's pid.
    #      The driver can be actually running or finished.
    # TODO(SKY-1213): username is actually user hash, should rename.
    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS jobs (
        job_id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_name TEXT,
        username TEXT,
        submitted_at FLOAT,
        status TEXT,
        run_timestamp TEXT CANDIDATE KEY,
        start_at FLOAT DEFAULT -1,
        end_at FLOAT DEFAULT NULL,
        resources TEXT DEFAULT NULL,
        pid INTEGER DEFAULT -1,
        log_dir TEXT DEFAULT NULL,
        metadata TEXT DEFAULT '{}')""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS pending_jobs(
        job_id INTEGER,
        run_cmd TEXT,
        submit INTEGER,
        created_time INTEGER
    )""")

    db_utils.add_column_to_table(cursor, conn, 'jobs', 'end_at', 'FLOAT')
    db_utils.add_column_to_table(cursor, conn, 'jobs', 'resources', 'TEXT')
    db_utils.add_column_to_table(cursor, conn, 'jobs', 'pid',
                                 'INTEGER DEFAULT -1')
    db_utils.add_column_to_table(cursor, conn, 'jobs', 'log_dir',
                                 'TEXT DEFAULT NULL')
    db_utils.add_column_to_table(cursor,
                                 conn,
                                 'jobs',
                                 'metadata',
                                 'TEXT DEFAULT \'{}\'',
                                 value_to_replace_existing_entries='{}')
    conn.commit()


_DB = None
_db_init_lock = threading.Lock()


def init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _DB
        if _DB is not None:
            return func(*args, **kwargs)

        with _db_init_lock:
            if _DB is None:
                db_path = os.path.expanduser('~/.sky/jobs.db')
                os.makedirs(pathlib.Path(db_path).parents[0], exist_ok=True)
                _DB = db_utils.SQLiteConn(db_path, create_table)
        return func(*args, **kwargs)

    return wrapper


class JobStatus(enum.Enum):
    """Job status enum."""

    # 3 in-flux states: each can transition to any state below it.
    # The `job_id` has been generated, but the generated ray program has
    # not started yet. skylet can transit the state from INIT to FAILED
    # directly, if the ray program fails to start.
    # In the 'jobs' table, the `submitted_at` column will be set to the current
    # time, when the job is firstly created (in the INIT state).
    INIT = 'INIT'
    """The job has been submitted, but not started yet."""
    # The job is waiting for the required resources. (`ray job status`
    # shows RUNNING as the generated ray program has started, but blocked
    # by the placement constraints.)
    PENDING = 'PENDING'
    """The job is waiting for required resources."""
    # Running the user's setup script.
    # Our update_job_status() can temporarily (for a short period) set
    # the status to SETTING_UP, if the generated ray program has not set
    # the status to PENDING or RUNNING yet.
    SETTING_UP = 'SETTING_UP'
    """The job is running the user's setup script."""
    # The job is running.
    # In the 'jobs' table, the `start_at` column will be set to the current
    # time, when the job is firstly transitioned to RUNNING.
    RUNNING = 'RUNNING'
    """The job is running."""
    # The job driver process failed. This happens when the job driver process
    # finishes when the status in job table is still not set to terminal state.
    # We should keep this state before the SUCCEEDED, as our job status update
    # relies on the order of the statuses to keep the latest status.
    FAILED_DRIVER = 'FAILED_DRIVER'
    """The job driver process failed."""
    # 3 terminal states below: once reached, they do not transition.
    # The job finished successfully.
    SUCCEEDED = 'SUCCEEDED'
    """The job finished successfully."""
    # The job fails due to the user code or a system restart.
    FAILED = 'FAILED'
    """The job fails due to the user code."""
    # The job setup failed. It needs to be placed after the `FAILED` state,
    # so that the status set by our generated ray program will not be
    # overwritten by ray's job status (FAILED). This is for a better UX, so
    # that the user can find out the reason of the failure quickly.
    FAILED_SETUP = 'FAILED_SETUP'
    """The job setup failed."""
    # The job is cancelled by the user.
    CANCELLED = 'CANCELLED'
    """The job is cancelled by the user."""

    @classmethod
    def nonterminal_statuses(cls) -> List['JobStatus']:
        return [cls.INIT, cls.SETTING_UP, cls.PENDING, cls.RUNNING]

    def is_terminal(self) -> bool:
        return self not in self.nonterminal_statuses()

    @classmethod
    def user_code_failure_states(cls) -> Sequence['JobStatus']:
        return (cls.FAILED, cls.FAILED_SETUP)

    def __lt__(self, other: 'JobStatus') -> bool:
        return list(JobStatus).index(self) < list(JobStatus).index(other)

    def colored_str(self) -> str:
        color = _JOB_STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'

    @classmethod
    def from_protobuf(
            cls,
            protobuf_value: 'jobsv1_pb2.JobStatus') -> Optional['JobStatus']:
        """Convert protobuf JobStatus enum to Python enum value."""
        protobuf_to_enum = {
            jobsv1_pb2.JOB_STATUS_INIT: cls.INIT,
            jobsv1_pb2.JOB_STATUS_PENDING: cls.PENDING,
            jobsv1_pb2.JOB_STATUS_SETTING_UP: cls.SETTING_UP,
            jobsv1_pb2.JOB_STATUS_RUNNING: cls.RUNNING,
            jobsv1_pb2.JOB_STATUS_FAILED_DRIVER: cls.FAILED_DRIVER,
            jobsv1_pb2.JOB_STATUS_SUCCEEDED: cls.SUCCEEDED,
            jobsv1_pb2.JOB_STATUS_FAILED: cls.FAILED,
            jobsv1_pb2.JOB_STATUS_FAILED_SETUP: cls.FAILED_SETUP,
            jobsv1_pb2.JOB_STATUS_CANCELLED: cls.CANCELLED,
            jobsv1_pb2.JOB_STATUS_UNSPECIFIED: None,
        }
        if protobuf_value not in protobuf_to_enum:
            raise ValueError(
                f'Unknown protobuf JobStatus value: {protobuf_value}')
        return protobuf_to_enum[protobuf_value]

    def to_protobuf(self) -> 'jobsv1_pb2.JobStatus':
        """Convert this Python enum value to protobuf enum value."""
        enum_to_protobuf = {
            JobStatus.INIT: jobsv1_pb2.JOB_STATUS_INIT,
            JobStatus.PENDING: jobsv1_pb2.JOB_STATUS_PENDING,
            JobStatus.SETTING_UP: jobsv1_pb2.JOB_STATUS_SETTING_UP,
            JobStatus.RUNNING: jobsv1_pb2.JOB_STATUS_RUNNING,
            JobStatus.FAILED_DRIVER: jobsv1_pb2.JOB_STATUS_FAILED_DRIVER,
            JobStatus.SUCCEEDED: jobsv1_pb2.JOB_STATUS_SUCCEEDED,
            JobStatus.FAILED: jobsv1_pb2.JOB_STATUS_FAILED,
            JobStatus.FAILED_SETUP: jobsv1_pb2.JOB_STATUS_FAILED_SETUP,
            JobStatus.CANCELLED: jobsv1_pb2.JOB_STATUS_CANCELLED,
        }
        if self not in enum_to_protobuf:
            raise ValueError(f'Unknown JobStatus value: {self}')
        return enum_to_protobuf[self]


# We have two steps for job submissions:
# 1. Client reserve a job id from the job table by adding a INIT state job.
# 2. Client updates the job status to PENDING by actually submitting the job's
#    command to the scheduler.
# In normal cases, the two steps happens very close to each other through two
# consecutive SSH connections.
# We should update status for INIT job that has been staying in INIT state for
# a while (60 seconds), which likely fails to reach step 2.
# TODO(zhwu): This number should be tuned based on heuristics.
_INIT_SUBMIT_GRACE_PERIOD = 60

_PRE_RESOURCE_STATUSES = [JobStatus.PENDING]


class JobScheduler:
    """Base class for job scheduler"""

    @init_db
    def queue(self, job_id: int, cmd: str) -> None:
        assert _DB is not None
        _DB.cursor.execute('INSERT INTO pending_jobs VALUES (?,?,?,?)',
                           (job_id, cmd, 0, int(time.time())))
        _DB.conn.commit()
        set_status(job_id, JobStatus.PENDING)
        self.schedule_step()

    @init_db
    def remove_job_no_lock(self, job_id: int) -> None:
        assert _DB is not None
        _DB.cursor.execute(f'DELETE FROM pending_jobs WHERE job_id={job_id!r}')
        _DB.conn.commit()

    @init_db
    def _run_job(self, job_id: int, run_cmd: str):
        assert _DB is not None
        _DB.cursor.execute(
            (f'UPDATE pending_jobs SET submit={int(time.time())} '
             f'WHERE job_id={job_id!r}'))
        _DB.conn.commit()
        pid = subprocess_utils.launch_new_process_tree(run_cmd)
        # TODO(zhwu): Backward compatibility, remove this check after 0.10.0.
        # This is for the case where the job is submitted with SkyPilot older
        # than #4318, using ray job submit.
        if 'job submit' in run_cmd:
            pid = -1
        _DB.cursor.execute((f'UPDATE jobs SET pid={pid} '
                            f'WHERE job_id={job_id!r}'))
        _DB.conn.commit()

    def schedule_step(self, force_update_jobs: bool = False) -> None:
        if force_update_jobs:
            update_status()
        pending_job_ids = self._get_pending_job_ids()
        # TODO(zhwu, mraheja): One optimization can be allowing more than one
        # job staying in the pending state after ray job submit, so that to be
        # faster to schedule a large amount of jobs.
        for job_id in pending_job_ids:
            with filelock.FileLock(_get_lock_path(job_id)):
                pending_job = _get_pending_job(job_id)
                if pending_job is None:
                    # Pending job can be removed by another thread, due to the
                    # job being scheduled already.
                    continue
                run_cmd = pending_job['run_cmd']
                submit = pending_job['submit']
                created_time = pending_job['created_time']
                # We don't have to refresh the job status before checking, as
                # the job status will only be stale in rare cases where ray job
                # crashes; or the job stays in INIT state for a long time.
                # In those cases, the periodic JobSchedulerEvent event will
                # update the job status every 300 seconds.
                status = get_status_no_lock(job_id)
                if (status not in _PRE_RESOURCE_STATUSES or
                        created_time < psutil.boot_time()):
                    # Job doesn't exist, is running/cancelled, or created
                    # before the last reboot.
                    self.remove_job_no_lock(job_id)
                    continue
                if submit:
                    # Next job waiting for resources
                    return
                self._run_job(job_id, run_cmd)
                return

    def _get_pending_job_ids(self) -> List[int]:
        """Returns the job ids in the pending jobs table

        The information contains job_id, run command, submit time,
        creation time.
        """
        raise NotImplementedError


class FIFOScheduler(JobScheduler):
    """First in first out job scheduler"""

    @init_db
    def _get_pending_job_ids(self) -> List[int]:
        assert _DB is not None
        rows = _DB.cursor.execute(
            'SELECT job_id FROM pending_jobs ORDER BY job_id').fetchall()
        return [row[0] for row in rows]


scheduler = FIFOScheduler()

_JOB_STATUS_TO_COLOR = {
    JobStatus.INIT: colorama.Fore.BLUE,
    JobStatus.SETTING_UP: colorama.Fore.BLUE,
    JobStatus.PENDING: colorama.Fore.BLUE,
    JobStatus.RUNNING: colorama.Fore.GREEN,
    JobStatus.FAILED_DRIVER: colorama.Fore.RED,
    JobStatus.SUCCEEDED: colorama.Fore.GREEN,
    JobStatus.FAILED: colorama.Fore.RED,
    JobStatus.FAILED_SETUP: colorama.Fore.RED,
    JobStatus.CANCELLED: colorama.Fore.YELLOW,
}


def make_job_command_with_user_switching(username: str,
                                         command: str) -> List[str]:
    return ['sudo', '-H', 'su', '--login', username, '-c', command]


@init_db
def add_job(job_name: str,
            username: str,
            run_timestamp: str,
            resources_str: str,
            metadata: str = '{}') -> Tuple[int, str]:
    """Atomically reserve the next available job id for the user."""
    assert _DB is not None
    job_submitted_at = time.time()
    # job_id will autoincrement with the null value
    _DB.cursor.execute(
        'INSERT INTO jobs VALUES (null, ?, ?, ?, ?, ?, ?, null, ?, 0, null, ?)',
        (job_name, username, job_submitted_at, JobStatus.INIT.value,
         run_timestamp, None, resources_str, metadata))
    _DB.conn.commit()
    rows = _DB.cursor.execute('SELECT job_id FROM jobs WHERE run_timestamp=(?)',
                              (run_timestamp,))
    for row in rows:
        job_id = row[0]
    assert job_id is not None
    log_dir = os.path.join(constants.SKY_LOGS_DIRECTORY, f'{job_id}-{job_name}')
    set_log_dir_no_lock(job_id, log_dir)
    return job_id, log_dir


@init_db
def set_log_dir_no_lock(job_id: int, log_dir: str) -> None:
    """Set the log directory for the job.

    We persist the log directory for the job to allow changing the log directory
    generation logic over versions.

    Args:
        job_id: The ID of the job.
        log_dir: The log directory for the job.
    """
    assert _DB is not None
    _DB.cursor.execute('UPDATE jobs SET log_dir=(?) WHERE job_id=(?)',
                       (log_dir, job_id))
    _DB.conn.commit()


@init_db
def get_log_dir_for_job(job_id: int) -> Optional[str]:
    """Get the log directory for the job.

    Args:
        job_id: The ID of the job.
    """
    assert _DB is not None
    rows = _DB.cursor.execute('SELECT log_dir FROM jobs WHERE job_id=(?)',
                              (job_id,))
    for row in rows:
        return row[0]
    return None


@init_db
def _set_status_no_lock(job_id: int, status: JobStatus) -> None:
    """Setting the status of the job in the database."""
    assert _DB is not None
    assert status != JobStatus.RUNNING, (
        'Please use set_job_started() to set job status to RUNNING')
    if status.is_terminal():
        end_at = time.time()
        # status does not need to be set if the end_at is not null, since
        # the job must be in a terminal state already.
        # Don't check the end_at for FAILED_SETUP, so that the generated
        # ray program can overwrite the status.
        check_end_at_str = ' AND end_at IS NULL'
        if status != JobStatus.FAILED_SETUP:
            check_end_at_str = ''
        _DB.cursor.execute(
            'UPDATE jobs SET status=(?), end_at=(?) '
            f'WHERE job_id=(?) {check_end_at_str}',
            (status.value, end_at, job_id))
    else:
        _DB.cursor.execute(
            'UPDATE jobs SET status=(?), end_at=NULL '
            'WHERE job_id=(?)', (status.value, job_id))
    _DB.conn.commit()


def set_status(job_id: int, status: JobStatus) -> None:
    # TODO(mraheja): remove pylint disabling when filelock version updated
    # pylint: disable=abstract-class-instantiated
    with filelock.FileLock(_get_lock_path(job_id)):
        _set_status_no_lock(job_id, status)


@init_db
def set_job_started(job_id: int) -> None:
    # TODO(mraheja): remove pylint disabling when filelock version updated.
    # pylint: disable=abstract-class-instantiated
    assert _DB is not None
    with filelock.FileLock(_get_lock_path(job_id)):
        _DB.cursor.execute(
            'UPDATE jobs SET status=(?), start_at=(?), end_at=NULL '
            'WHERE job_id=(?)', (JobStatus.RUNNING.value, time.time(), job_id))
        _DB.conn.commit()


@init_db
def get_status_no_lock(job_id: int) -> Optional[JobStatus]:
    """Get the status of the job with the given id.

    This function can return a stale status if there is a concurrent update.
    Make sure the caller will not be affected by the stale status, e.g. getting
    the status in a while loop as in `log_lib._follow_job_logs`. Otherwise, use
    `get_status`.
    """
    assert _DB is not None
    rows = _DB.cursor.execute('SELECT status FROM jobs WHERE job_id=(?)',
                              (job_id,))
    for (status,) in rows:
        if status is None:
            return None
        return JobStatus(status)
    return None


def get_status(job_id: int) -> Optional[JobStatus]:
    # TODO(mraheja): remove pylint disabling when filelock version updated.
    # pylint: disable=abstract-class-instantiated
    with filelock.FileLock(_get_lock_path(job_id)):
        return get_status_no_lock(job_id)


@init_db
def get_statuses_payload(job_ids: List[Optional[int]]) -> str:
    return message_utils.encode_payload(get_statuses(job_ids))


@init_db
def get_statuses(job_ids: List[int]) -> Dict[int, Optional[str]]:
    assert _DB is not None
    # Per-job lock is not required here, since the staled job status will not
    # affect the caller.
    query_str = ','.join(['?'] * len(job_ids))
    rows = _DB.cursor.execute(
        f'SELECT job_id, status FROM jobs WHERE job_id IN ({query_str})',
        job_ids)
    statuses: Dict[int, Optional[str]] = {job_id: None for job_id in job_ids}
    for (job_id, status) in rows:
        statuses[job_id] = status
    return statuses


@init_db
def get_jobs_info(user_hash: Optional[str] = None,
                  all_jobs: bool = False) -> List['jobsv1_pb2.JobInfo']:
    """Get detailed job information.

    Similar to dump_job_queue but returns structured protobuf objects instead
    of encoded strings.

    Args:
        user_hash: The user hash to show jobs for. Show all the users if None.
        all_jobs: Whether to show all jobs, not just the pending/running ones.
    """
    assert _DB is not None

    status_list: Optional[List[JobStatus]] = [
        JobStatus.SETTING_UP, JobStatus.PENDING, JobStatus.RUNNING
    ]
    if all_jobs:
        status_list = None

    jobs = _get_jobs(user_hash, status_list=status_list)
    jobs_info = []
    for job in jobs:
        jobs_info.append(
            jobsv1_pb2.JobInfo(job_id=job['job_id'],
                               job_name=job['job_name'],
                               username=job['username'],
                               submitted_at=job['submitted_at'],
                               status=job['status'].to_protobuf(),
                               run_timestamp=job['run_timestamp'],
                               start_at=job['start_at'],
                               end_at=job['end_at'],
                               resources=job['resources'],
                               pid=job['pid'],
                               log_path=os.path.join(
                                   constants.SKY_LOGS_DIRECTORY,
                                   job['run_timestamp']),
                               metadata=json.dumps(job['metadata'])))
    return jobs_info


def load_statuses_payload(
        statuses_payload: str) -> Dict[Optional[int], Optional[JobStatus]]:
    original_statuses = message_utils.decode_payload(statuses_payload)
    statuses = dict()
    for job_id, status in original_statuses.items():
        # json.dumps will convert all keys to strings. Integers will
        # become string representations of integers, e.g. "1" instead of 1;
        # `None` will become "null" instead of None. Here we use
        # json.loads to convert them back to their original values.
        # See docstr of core::job_status for the meaning of `statuses`.
        statuses[json.loads(job_id)] = (JobStatus(status)
                                        if status is not None else None)
    return statuses


@init_db
def get_latest_job_id() -> Optional[int]:
    assert _DB is not None
    rows = _DB.cursor.execute(
        'SELECT job_id FROM jobs ORDER BY job_id DESC LIMIT 1')
    for (job_id,) in rows:
        return job_id
    return None


@init_db
def get_job_submitted_or_ended_timestamp_payload(job_id: int,
                                                 get_ended_time: bool) -> str:
    """Get the job submitted/ended timestamp.

    This function should only be called by the jobs controller, which is ok to
    use `submitted_at` instead of `start_at`, because the managed job duration
    need to include both setup and running time and the job will not stay in
    PENDING state.

    The normal job duration will use `start_at` instead of `submitted_at` (in
    `table_utils.format_job_queue()`), because the job may stay in PENDING if
    the cluster is busy.
    """
    return message_utils.encode_payload(
        get_job_submitted_or_ended_timestamp(job_id, get_ended_time))


@init_db
def get_job_submitted_or_ended_timestamp(
        job_id: int, get_ended_time: bool) -> Optional[float]:
    """Get the job submitted timestamp.

    Returns the raw timestamp or None if job doesn't exist.
    """
    assert _DB is not None
    field = 'end_at' if get_ended_time else 'submitted_at'
    rows = _DB.cursor.execute(f'SELECT {field} FROM jobs WHERE job_id=(?)',
                              (job_id,))
    for (timestamp,) in rows:
        return timestamp
    return None


def get_ray_port():
    """Get the port Skypilot-internal Ray cluster uses.

    If the port file does not exist, the cluster was launched before #1790,
    return the default port.
    """
    port_path = os.path.expanduser(constants.SKY_REMOTE_RAY_PORT_FILE)
    if not os.path.exists(port_path):
        return 6379
    port = json.load(open(port_path, 'r', encoding='utf-8'))['ray_port']
    return port


def get_job_submission_port():
    """Get the dashboard port Skypilot-internal Ray cluster uses.

    If the port file does not exist, the cluster was launched before #1790,
    return the default port.
    """
    port_path = os.path.expanduser(constants.SKY_REMOTE_RAY_PORT_FILE)
    if not os.path.exists(port_path):
        return 8265
    port = json.load(open(port_path, 'r',
                          encoding='utf-8'))['ray_dashboard_port']
    return port


def _get_records_from_rows(rows) -> List[Dict[str, Any]]:
    records = []
    for row in rows:
        if row[0] is None:
            break
        # TODO: use namedtuple instead of dict
        records.append({
            'job_id': row[JobInfoLoc.JOB_ID.value],
            'job_name': row[JobInfoLoc.JOB_NAME.value],
            'username': row[JobInfoLoc.USERNAME.value],
            'submitted_at': row[JobInfoLoc.SUBMITTED_AT.value],
            'status': JobStatus(row[JobInfoLoc.STATUS.value]),
            'run_timestamp': row[JobInfoLoc.RUN_TIMESTAMP.value],
            'start_at': row[JobInfoLoc.START_AT.value],
            'end_at': row[JobInfoLoc.END_AT.value],
            'resources': row[JobInfoLoc.RESOURCES.value],
            'pid': row[JobInfoLoc.PID.value],
            'metadata': json.loads(row[JobInfoLoc.METADATA.value]),
        })
    return records


@init_db
def _get_jobs(
        user_hash: Optional[str],
        status_list: Optional[List[JobStatus]] = None) -> List[Dict[str, Any]]:
    """Returns jobs with the given fields, sorted by job_id, descending."""
    assert _DB is not None
    if status_list is None:
        status_list = list(JobStatus)
    status_str_list = [repr(status.value) for status in status_list]
    filter_str = f'WHERE status IN ({",".join(status_str_list)})'
    params = []
    if user_hash is not None:
        # We use the old username field for compatibility.
        filter_str += ' AND username=(?)'
        params.append(user_hash)
    rows = _DB.cursor.execute(
        f'SELECT * FROM jobs {filter_str} ORDER BY job_id DESC', params)
    records = _get_records_from_rows(rows)
    return records


@init_db
def _get_jobs_by_ids(job_ids: List[int]) -> List[Dict[str, Any]]:
    assert _DB is not None
    rows = _DB.cursor.execute(
        f"""\
        SELECT * FROM jobs
        WHERE job_id IN ({','.join(['?'] * len(job_ids))})
        ORDER BY job_id DESC""",
        (*job_ids,),
    )
    records = _get_records_from_rows(rows)
    return records


@init_db
def _get_pending_job(job_id: int) -> Optional[Dict[str, Any]]:
    assert _DB is not None
    rows = _DB.cursor.execute(
        'SELECT created_time, submit, run_cmd FROM pending_jobs '
        f'WHERE job_id={job_id!r}')
    for row in rows:
        created_time, submit, run_cmd = row
        return {
            'created_time': created_time,
            'submit': submit,
            'run_cmd': run_cmd
        }
    return None


def _is_job_driver_process_running(job_pid: int, job_id: int) -> bool:
    """Check if the job driver process is running.

    We check the cmdline to avoid the case where the same pid is reused by a
    different process.
    """
    if job_pid <= 0:
        return False
    try:
        job_process = psutil.Process(job_pid)
        return job_process.is_running() and any(
            JOB_CMD_IDENTIFIER.format(job_id) in line
            for line in job_process.cmdline())
    except psutil.NoSuchProcess:
        return False


def update_job_status(job_ids: List[int],
                      silent: bool = False) -> List[JobStatus]:
    """Updates and returns the job statuses matching our `JobStatus` semantics.

    This function queries `ray job status` and processes those results to match
    our semantics.

    Though we update job status actively in the generated ray program and
    during job cancelling, we still need this to handle the staleness problem,
    caused by instance restarting and other corner cases (if any).

    This function should only be run on the remote instance with ray>=2.4.0.
    """
    echo = logger.info if not silent else logger.debug
    if not job_ids:
        return []

    statuses = []
    for job_id in job_ids:
        # Per-job status lock is required because between the job status
        # query and the job status update, the job status in the databse
        # can be modified by the generated ray program.
        with filelock.FileLock(_get_lock_path(job_id)):
            status = None
            job_record = _get_jobs_by_ids([job_id])[0]
            original_status = job_record['status']
            job_submitted_at = job_record['submitted_at']
            job_pid = job_record['pid']

            pid_query_time = time.time()
            failed_driver_transition_message = None
            if original_status == JobStatus.INIT:
                if (job_submitted_at >= psutil.boot_time() and job_submitted_at
                        >= pid_query_time - _INIT_SUBMIT_GRACE_PERIOD):
                    # The job id is reserved, but the job is not submitted yet.
                    # We should keep it in INIT.
                    status = JobStatus.INIT
                else:
                    # We always immediately submit job after the job id is
                    # allocated, i.e. INIT -> PENDING, if a job stays in INIT
                    # for too long, it is likely the job submission process
                    # was killed before the job is submitted. We should set it
                    # to FAILED then. Note, if ray job indicates the job is
                    # running, we will change status to PENDING below.
                    failed_driver_transition_message = (
                        f'INIT job {job_id} is stale, setting to FAILED_DRIVER')
                    status = JobStatus.FAILED_DRIVER

            # job_pid is 0 if the job is not submitted yet.
            # job_pid is -1 if the job is submitted with SkyPilot older than
            # #4318, using ray job submit. We skip the checking for those
            # jobs.
            if job_pid > 0:
                if _is_job_driver_process_running(job_pid, job_id):
                    status = JobStatus.PENDING
                else:
                    # By default, if the job driver process does not exist,
                    # the actual SkyPilot job is one of the following:
                    # 1. Still pending to be submitted.
                    # 2. Submitted and finished.
                    # 3. Driver failed without correctly setting the job
                    #    status in the job table.
                    # Although we set the status to FAILED_DRIVER, it can be
                    # overridden to PENDING if the job is not submitted, or
                    # any other terminal status if the job driver process
                    # finished correctly.
                    failed_driver_transition_message = (
                        f'Job {job_id} driver process is not running, but '
                        'the job state is not in terminal states, setting '
                        'it to FAILED_DRIVER')
                    status = JobStatus.FAILED_DRIVER
            elif job_pid < 0:
                # TODO(zhwu): Backward compatibility, remove after 0.10.0.
                # We set the job status to PENDING instead of actually
                # checking ray job status and let the status in job table
                # take effect in the later max.
                status = JobStatus.PENDING

            pending_job = _get_pending_job(job_id)
            if pending_job is not None:
                if pending_job['created_time'] < psutil.boot_time():
                    failed_driver_transition_message = (
                        f'Job {job_id} is stale, setting to FAILED_DRIVER: '
                        f'created_time={pending_job["created_time"]}, '
                        f'boot_time={psutil.boot_time()}')
                    # The job is stale as it is created before the instance
                    # is booted, e.g. the instance is rebooted.
                    status = JobStatus.FAILED_DRIVER
                elif pending_job['submit'] <= 0:
                    # The job is not submitted (submit <= 0), we set it to
                    # PENDING.
                    # For submitted jobs, the driver should have been started,
                    # because the job_lib.JobScheduler.schedule_step() have
                    # the submit field and driver process pid set in the same
                    # job lock.
                    # The job process check in the above section should
                    # correctly figured out the status and we don't overwrite
                    # it here. (Note: the FAILED_DRIVER status will be
                    # overridden by the actual job terminal status in the table
                    # if the job driver process finished correctly.)
                    status = JobStatus.PENDING

            assert original_status is not None, (job_id, status)
            if status is None:
                # The job is submitted but the job driver process pid is not
                # set in the database. This is guarding against the case where
                # the schedule_step() function is interrupted (e.g., VM stop)
                # at the middle of starting a new process and setting the pid.
                status = original_status
                if (original_status is not None and
                        not original_status.is_terminal()):
                    echo(f'Job {job_id} status is None, setting it to '
                         'FAILED_DRIVER.')
                    # The job may be stale, when the instance is restarted. We
                    # need to reset the job status to FAILED_DRIVER if its
                    # original status is in nonterminal_statuses.
                    echo(f'Job {job_id} is in a unknown state, setting it to '
                         'FAILED_DRIVER')
                    status = JobStatus.FAILED_DRIVER
                    _set_status_no_lock(job_id, status)
            else:
                # Taking max of the status is necessary because:
                # 1. The original status has already been set to later
                #    terminal state by a finished job driver.
                # 2. Job driver process check would map any running job process
                #    to `PENDING`, so we need to take the max to keep it at
                #    later status for jobs actually started in SETTING_UP or
                #    RUNNING.
                status = max(status, original_status)
                assert status is not None, (job_id, status, original_status)
                if status != original_status:  # Prevents redundant update.
                    _set_status_no_lock(job_id, status)
                    echo(f'Updated job {job_id} status to {status}')
                    if (status == JobStatus.FAILED_DRIVER and
                            failed_driver_transition_message is not None):
                        echo(failed_driver_transition_message)
        statuses.append(status)
    return statuses


@init_db
def fail_all_jobs_in_progress() -> None:
    assert _DB is not None
    in_progress_status = [
        status.value for status in JobStatus.nonterminal_statuses()
    ]
    _DB.cursor.execute(
        f"""\
        UPDATE jobs SET status=(?)
        WHERE status IN ({','.join(['?'] * len(in_progress_status))})
        """, (JobStatus.FAILED_DRIVER.value, *in_progress_status))
    _DB.conn.commit()


def update_status() -> None:
    # This signal file suggests that the controller is recovering from a
    # failure. See sky/jobs/utils.py::update_managed_jobs_statuses for more
    # details. When recovering, we should not update the job status to failed
    # driver as they will be recovered later.
    if os.path.exists(
            os.path.expanduser(
                constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE)):
        return
    # This will be called periodically by the skylet to update the status
    # of the jobs in the database, to avoid stale job status.
    nonterminal_jobs = _get_jobs(user_hash=None,
                                 status_list=JobStatus.nonterminal_statuses())
    nonterminal_job_ids = [job['job_id'] for job in nonterminal_jobs]

    update_job_status(nonterminal_job_ids)


@init_db
def is_cluster_idle() -> bool:
    """Returns if the cluster is idle (no in-flight jobs)."""
    assert _DB is not None
    in_progress_status = [
        status.value for status in JobStatus.nonterminal_statuses()
    ]
    rows = _DB.cursor.execute(
        f"""\
        SELECT COUNT(*) FROM jobs
        WHERE status IN ({','.join(['?'] * len(in_progress_status))})
        """, in_progress_status)
    for (count,) in rows:
        return count == 0
    assert False, 'Should not reach here'


def dump_job_queue(user_hash: Optional[str], all_jobs: bool) -> str:
    """Get the job queue in encoded json format.

    Args:
        user_hash: The user hash to show jobs for. Show all the users if None.
        all_jobs: Whether to show all jobs, not just the pending/running ones.
    """
    status_list: Optional[List[JobStatus]] = [
        JobStatus.SETTING_UP, JobStatus.PENDING, JobStatus.RUNNING
    ]
    if all_jobs:
        status_list = None

    jobs = _get_jobs(user_hash, status_list=status_list)
    for job in jobs:
        job['status'] = job['status'].value
        job['log_path'] = os.path.join(constants.SKY_LOGS_DIRECTORY,
                                       job.pop('run_timestamp'))
    return message_utils.encode_payload(jobs)


def load_job_queue(payload: str) -> List[Dict[str, Any]]:
    """Load the job queue from encoded json format.

    Args:
        payload: The encoded payload string to load.
    """
    jobs = message_utils.decode_payload(payload)
    for job in jobs:
        job['status'] = JobStatus(job['status'])
        job['user_hash'] = job['username']
        user = global_user_state.get_user(job['user_hash'])
        job['username'] = user.name if user is not None else None
    return jobs


# TODO(zhwu): Backward compatibility for jobs submitted before #4318, remove
# after 0.10.0.
def _create_ray_job_submission_client():
    """Import the ray job submission client."""
    try:
        import ray  # pylint: disable=import-outside-toplevel
    except ImportError:
        logger.error('Failed to import ray')
        raise
    try:
        # pylint: disable=import-outside-toplevel
        from ray import job_submission
    except ImportError:
        logger.error(
            f'Failed to import job_submission with ray=={ray.__version__}')
        raise
    port = get_job_submission_port()
    return job_submission.JobSubmissionClient(
        address=f'http://127.0.0.1:{port}')


def _make_ray_job_id(sky_job_id: int) -> str:
    return f'{sky_job_id}-{getpass.getuser()}'


def cancel_jobs_encoded_results(jobs: Optional[List[int]],
                                cancel_all: bool = False,
                                user_hash: Optional[str] = None) -> str:
    """Cancel jobs.

    Args:
        jobs: Job IDs to cancel.
        cancel_all: Whether to cancel all jobs.
        user_hash: If specified, cancels the jobs for the specified user only.
            Otherwise, applies to all users.

    Returns:
        Encoded job IDs that are actually cancelled. Caller should use
        message_utils.decode_payload() to parse.
    """
    return message_utils.encode_payload(cancel_jobs(jobs, cancel_all,
                                                    user_hash))


def cancel_jobs(jobs: Optional[List[int]],
                cancel_all: bool = False,
                user_hash: Optional[str] = None) -> List[int]:
    job_records = []
    all_status = [JobStatus.PENDING, JobStatus.SETTING_UP, JobStatus.RUNNING]
    if jobs is None and not cancel_all:
        # Cancel the latest (largest job ID) running job from current user.
        job_records = _get_jobs(user_hash, [JobStatus.RUNNING])[:1]
    elif cancel_all:
        job_records = _get_jobs(user_hash, all_status)
    if jobs is not None:
        job_records.extend(_get_jobs_by_ids(jobs))

    cancelled_ids = []
    # Sequentially cancel the jobs to avoid the resource number bug caused by
    # ray cluster (tracked in #1262).
    for job_record in job_records:
        job_id = job_record['job_id']
        # Job is locked to ensure that pending queue does not start it while
        # it is being cancelled
        with filelock.FileLock(_get_lock_path(job_id)):
            job = _get_jobs_by_ids([job_id])[0]
            if _is_job_driver_process_running(job['pid'], job_id):
                # Not use process.terminate() as that will only terminate the
                # process shell process, not the ray driver process
                # under the shell.
                #
                # We don't kill all the children of the process, like
                # subprocess_utils.kill_process_daemon() does, but just the
                # process group here, because the underlying job driver can
                # start other jobs with `schedule_step`, causing the other job
                # driver processes to be children of the current job driver
                # process.
                #
                # Killing the process group is enough as the underlying job
                # should be able to clean itself up correctly by ray driver.
                #
                # The process group pid should be the same as the job pid as we
                # use start_new_session=True, but we use os.getpgid() to be
                # extra cautious.
                job_pgid = os.getpgid(job['pid'])
                os.killpg(job_pgid, signal.SIGTERM)
                # We don't have to start a daemon to forcefully kill the process
                # as our job driver process will clean up the underlying
                # child processes.
            elif job['pid'] < 0:
                try:
                    # TODO(zhwu): Backward compatibility, remove after 0.10.0.
                    # The job was submitted with ray job submit before #4318.
                    job_client = _create_ray_job_submission_client()
                    job_client.stop_job(_make_ray_job_id(job['job_id']))
                except RuntimeError as e:
                    # If the request to the job server fails, we should not
                    # set the job to CANCELLED.
                    if 'does not exist' not in str(e):
                        logger.warning(str(e))
                        continue
            # Get the job status again to avoid race condition.
            job_status = get_status_no_lock(job['job_id'])
            if job_status in [
                    JobStatus.PENDING, JobStatus.SETTING_UP, JobStatus.RUNNING
            ]:
                _set_status_no_lock(job['job_id'], JobStatus.CANCELLED)
                cancelled_ids.append(job['job_id'])

        scheduler.schedule_step()
    return cancelled_ids


@init_db
def get_run_timestamp(job_id: Optional[int]) -> Optional[str]:
    """Returns the relative path to the log file for a job."""
    assert _DB is not None
    _DB.cursor.execute(
        """\
            SELECT * FROM jobs
            WHERE job_id=(?)""", (job_id,))
    row = _DB.cursor.fetchone()
    if row is None:
        return None
    run_timestamp = row[JobInfoLoc.RUN_TIMESTAMP.value]
    return run_timestamp


@init_db
def get_log_dir_for_jobs(job_ids: List[Optional[str]]) -> str:
    """Returns the relative paths to the log files for jobs with globbing,
    encoded."""
    job_to_dir = get_job_log_dirs(job_ids)
    job_to_dir_str: Dict[str, str] = {}
    for job_id, log_dir in job_to_dir.items():
        job_to_dir_str[str(job_id)] = log_dir
    return message_utils.encode_payload(job_to_dir_str)


@init_db
def get_job_log_dirs(job_ids: List[int]) -> Dict[int, str]:
    """Returns the relative paths to the log files for jobs with globbing."""
    assert _DB is not None
    query_str = ' OR '.join(['job_id GLOB (?)'] * len(job_ids))
    _DB.cursor.execute(
        f"""\
            SELECT * FROM jobs
            WHERE {query_str}""", job_ids)
    rows = _DB.cursor.fetchall()
    job_to_dir: Dict[int, str] = {}
    for row in rows:
        job_id = row[JobInfoLoc.JOB_ID.value]
        if row[JobInfoLoc.LOG_PATH.value]:
            job_to_dir[job_id] = row[JobInfoLoc.LOG_PATH.value]
        else:
            run_timestamp = row[JobInfoLoc.RUN_TIMESTAMP.value]
            job_to_dir[job_id] = os.path.join(constants.SKY_LOGS_DIRECTORY,
                                              run_timestamp)
    return job_to_dir


class JobLibCodeGen:
    """Code generator for job utility functions.

    Usage:

      >> codegen = JobLibCodeGen.add_job(...)
    """

    _PREFIX = [
        'import os',
        'import getpass',
        'import sys',
        'from sky import exceptions',
        'from sky.skylet import log_lib, job_lib, constants',
    ]

    @classmethod
    def add_job(cls, job_name: Optional[str], username: str, run_timestamp: str,
                resources_str: str, metadata: str) -> str:
        if job_name is None:
            job_name = '-'
        code = [
            # We disallow job submission when SKYLET_VERSION is older than 9, as
            # it was using ray job submit before #4318, and switched to raw
            # process. Using the old skylet version will cause the job status
            # to be stuck in PENDING state or transition to FAILED_DRIVER state.
            '\nif int(constants.SKYLET_VERSION) < 9: '
            'raise RuntimeError("SkyPilot runtime is too old, which does not '
            'support submitting jobs.")',
            '\nresult = None',
            '\nif int(constants.SKYLET_VERSION) < 15: '
            '\n result = job_lib.add_job('
            f'{job_name!r},'
            f'{username!r},'
            f'{run_timestamp!r},'
            f'{resources_str!r})',
            '\nelse: '
            '\n result = job_lib.add_job('
            f'{job_name!r},'
            f'{username!r},'
            f'{run_timestamp!r},'
            f'{resources_str!r},'
            f'metadata={metadata!r})',
            ('\nif isinstance(result, tuple):'
             '\n  print("Job ID: " + str(result[0]), flush=True)'
             '\n  print("Log Dir: " + str(result[1]), flush=True)'
             '\nelse:'
             '\n  print("Job ID: " + str(result), flush=True)'),
        ]
        return cls._build(code)

    @classmethod
    def queue_job(cls, job_id: int, cmd: str) -> str:
        code = [
            'job_lib.scheduler.queue('
            f'{job_id!r},'
            f'{cmd!r})',
        ]
        return cls._build(code)

    @classmethod
    def update_status(cls) -> str:
        code = ['job_lib.update_status()']
        return cls._build(code)

    @classmethod
    def get_job_queue(cls, user_hash: Optional[str], all_jobs: bool) -> str:
        # TODO(SKY-1214): combine get_job_queue with get_job_statuses.
        code = [
            'job_queue = job_lib.dump_job_queue('
            f'{user_hash!r}, {all_jobs})',
            'print(job_queue, flush=True)',
        ]
        return cls._build(code)

    @classmethod
    def cancel_jobs(cls,
                    job_ids: Optional[List[int]],
                    cancel_all: bool = False,
                    user_hash: Optional[str] = None) -> str:
        """See job_lib.cancel_jobs()."""
        code = [
            (f'cancelled = job_lib.cancel_jobs_encoded_results('
             f'jobs={job_ids!r}, cancel_all={cancel_all}, '
             f'user_hash={user_hash!r})'),
            # Print cancelled IDs. Caller should parse by decoding.
            'print(cancelled, flush=True)',
        ]
        # TODO(zhwu): Backward compatibility, remove after 0.12.0.
        if user_hash is None:
            code = [
                (f'cancelled = job_lib.cancel_jobs_encoded_results('
                 f' {job_ids!r}, {cancel_all})'),
                # Print cancelled IDs. Caller should parse by decoding.
                'print(cancelled, flush=True)',
            ]
        return cls._build(code)

    @classmethod
    def fail_all_jobs_in_progress(cls) -> str:
        # Used only for restarting a cluster.
        code = ['job_lib.fail_all_jobs_in_progress()']
        return cls._build(code)

    @classmethod
    def tail_logs(cls,
                  job_id: Optional[int],
                  managed_job_id: Optional[int],
                  follow: bool = True,
                  tail: int = 0) -> str:
        # pylint: disable=line-too-long

        code = [
            # We use != instead of is not because 1 is not None will print a warning:
            # <stdin>:1: SyntaxWarning: "is not" with a literal. Did you mean "!="?
            f'job_id = {job_id} if {job_id} != None else job_lib.get_latest_job_id()',
            # For backward compatibility, use the legacy generation rule for
            # jobs submitted before 0.11.0.
            ('log_dir = None\n'
             'if hasattr(job_lib, "get_log_dir_for_job"):\n'
             '  log_dir = job_lib.get_log_dir_for_job(job_id)\n'
             'if log_dir is None:\n'
             '  run_timestamp = job_lib.get_run_timestamp(job_id)\n'
             f'  log_dir = None if run_timestamp is None else os.path.join({constants.SKY_LOGS_DIRECTORY!r}, run_timestamp)'
            ),
            # Add a newline to leave the if indent block above.
            f'\ntail_log_kwargs = {{"job_id": job_id, "log_dir": log_dir, "managed_job_id": {managed_job_id!r}, "follow": {follow}}}',
            f'{_LINUX_NEW_LINE}if getattr(constants, "SKYLET_LIB_VERSION", 1) > 1: tail_log_kwargs["tail"] = {tail}',
            f'{_LINUX_NEW_LINE}log_lib.tail_logs(**tail_log_kwargs)',
            # After tailing, check the job status and exit with appropriate code
            'job_status = job_lib.get_status(job_id)',
            # Backward compatibility for returning exit code: Skylet versions 2
            # and older did not have JobExitCode, so we use 0 for those versions
            # TODO: Remove this special handling after 0.10.0.
            'exit_code = exceptions.JobExitCode.from_job_status(job_status) if getattr(constants, "SKYLET_LIB_VERSION", 1) > 2 else 0',
            # Fix for dashboard: When follow=False and job is still running (NOT_FINISHED=101),
            # exit with success (0) since fetching current logs is a successful operation.
            # This prevents shell wrappers from printing "command terminated with exit code 101".
            f'exit_code = 0 if not {follow} and exit_code == 101 else exit_code',
            'sys.exit(exit_code)',
        ]
        return cls._build(code)

    @classmethod
    def get_job_status(cls, job_ids: Optional[List[int]] = None) -> str:
        # Prints "Job <id> <status>" for UX; caller should parse the last token.
        code = [
            f'job_ids = {job_ids} if {job_ids} is not None '
            'else [job_lib.get_latest_job_id()]',
            'job_statuses = job_lib.get_statuses_payload(job_ids)',
            'print(job_statuses, flush=True)',
        ]
        return cls._build(code)

    @classmethod
    def get_job_submitted_or_ended_timestamp_payload(
            cls,
            job_id: Optional[int] = None,
            get_ended_time: bool = False) -> str:
        code = [
            f'job_id = {job_id} if {job_id} is not None '
            'else job_lib.get_latest_job_id()',
            'job_time = '
            'job_lib.get_job_submitted_or_ended_timestamp_payload('
            f'job_id, {get_ended_time})',
            'print(job_time, flush=True)',
        ]
        return cls._build(code)

    @classmethod
    def get_log_dirs_for_jobs(cls, job_ids: Optional[List[str]]) -> str:
        code = [
            f'job_ids = {job_ids} if {job_ids} is not None '
            'else [job_lib.get_latest_job_id()]',
            # TODO(aylei): backward compatibility, remove after 0.12.0.
            'log_dirs = job_lib.get_log_dir_for_jobs(job_ids) if '
            'hasattr(job_lib, "get_log_dir_for_jobs") else '
            'job_lib.run_timestamp_with_globbing_payload(job_ids)',
            'print(log_dirs, flush=True)',
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        code = ';'.join(code)
        return f'{constants.SKY_PYTHON_CMD} -u -c {shlex.quote(code)}'
