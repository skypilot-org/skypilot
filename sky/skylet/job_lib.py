"""Utilities for jobs on a remote cluster, backed by a sqlite database.

This is a remote utility module that provides job queue functionality.
"""
import enum
import getpass
import json
import os
import pathlib
import shlex
import signal
import sqlite3
import subprocess
import time
from typing import Any, Dict, List, Optional

import colorama
import filelock
import psutil

from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import db_utils
from sky.utils import log_utils

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


_DB_PATH = os.path.expanduser('~/.sky/jobs.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)


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
        pid INTEGER DEFAULT -1)""")

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
    conn.commit()


_DB = db_utils.SQLiteConn(_DB_PATH, create_table)
_CURSOR = _DB.cursor
_CONN = _DB.conn


class JobStatus(enum.Enum):
    """Job status"""

    # 3 in-flux states: each can transition to any state below it.
    # The `job_id` has been generated, but the generated ray program has
    # not started yet. skylet can transit the state from INIT to FAILED
    # directly, if the ray program fails to start.
    # In the 'jobs' table, the `submitted_at` column will be set to the current
    # time, when the job is firstly created (in the INIT state).
    INIT = 'INIT'
    # The job is waiting for the required resources. (`ray job status`
    # shows RUNNING as the generated ray program has started, but blocked
    # by the placement constraints.)
    PENDING = 'PENDING'
    # Running the user's setup script (only in effect if --detach-setup is
    # set). Our update_job_status() can temporarily (for a short period) set
    # the status to SETTING_UP, if the generated ray program has not set
    # the status to PENDING or RUNNING yet.
    SETTING_UP = 'SETTING_UP'
    # The job is running.
    # In the 'jobs' table, the `start_at` column will be set to the current
    # time, when the job is firstly transitioned to RUNNING.
    RUNNING = 'RUNNING'
    # The job driver process failed. This happens when the job driver process
    # finishes when the status in job table is still not set to terminal state.
    # We should keep this state before the SUCCEEDED, as our job status update
    # relies on the order of the statuses to keep the latest status.
    FAILED_DRIVER = 'FAILED_DRIVER'
    # 3 terminal states below: once reached, they do not transition.
    # The job finished successfully.
    SUCCEEDED = 'SUCCEEDED'
    # The job fails due to the user code or a system restart.
    FAILED = 'FAILED'
    # The job setup failed (only in effect if --detach-setup is set). It
    # needs to be placed after the `FAILED` state, so that the status
    # set by our generated ray program will not be overwritten by
    # ray's job status (FAILED).
    # This is for a better UX, so that the user can find out the reason
    # of the failure quickly.
    FAILED_SETUP = 'FAILED_SETUP'
    # The job is cancelled by the user.
    CANCELLED = 'CANCELLED'

    @classmethod
    def nonterminal_statuses(cls) -> List['JobStatus']:
        return [cls.INIT, cls.SETTING_UP, cls.PENDING, cls.RUNNING]

    def is_terminal(self):
        return self not in self.nonterminal_statuses()

    def __lt__(self, other):
        return list(JobStatus).index(self) < list(JobStatus).index(other)

    def colored_str(self):
        color = _JOB_STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'


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

    def queue(self, job_id: int, cmd: str) -> None:
        _CURSOR.execute('INSERT INTO pending_jobs VALUES (?,?,?,?)',
                        (job_id, cmd, 0, int(time.time())))
        _CONN.commit()
        set_status(job_id, JobStatus.PENDING)
        self.schedule_step()

    def remove_job_no_lock(self, job_id: int) -> None:
        _CURSOR.execute(f'DELETE FROM pending_jobs WHERE job_id={job_id!r}')
        _CONN.commit()

    def _run_job(self, job_id: int, run_cmd: str):
        _CURSOR.execute((f'UPDATE pending_jobs SET submit={int(time.time())} '
                         f'WHERE job_id={job_id!r}'))
        _CONN.commit()
        # Use nohup to ensure the job driver process is a separate process tree,
        # instead of being a child of the current process. This is important to
        # avoid a chain of driver processes (job driver can call schedule_step()
        # to submit new jobs, and the new job can also call schedule_step()
        # recursively).
        #
        # echo $! will output the PID of the last background process started
        # in the current shell, so we can retrieve it and record in the DB.
        #
        # TODO(zhwu): A more elegant solution is to use another daemon process
        # to be in charge of starting these driver processes, instead of
        # starting them in the current process.
        wrapped_cmd = (f'nohup bash -c {shlex.quote(run_cmd)} '
                       '</dev/null >/dev/null 2>&1 & echo $!')
        proc = subprocess.run(wrapped_cmd,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL,
                              start_new_session=True,
                              check=True,
                              shell=True,
                              text=True)
        # Get the PID of the detached process
        pid = int(proc.stdout.strip())

        # TODO(zhwu): Backward compatibility, remove this check after 0.10.0.
        # This is for the case where the job is submitted with SkyPilot older
        # than #4318, using ray job submit.
        if 'job submit' in run_cmd:
            pid = -1
        _CURSOR.execute((f'UPDATE jobs SET pid={pid} '
                         f'WHERE job_id={job_id!r}'))
        _CONN.commit()

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

    def _get_pending_job_ids(self) -> List[int]:
        rows = _CURSOR.execute(
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


def add_job(job_name: str, username: str, run_timestamp: str,
            resources_str: str) -> int:
    """Atomically reserve the next available job id for the user."""
    job_submitted_at = time.time()
    # job_id will autoincrement with the null value
    _CURSOR.execute(
        'INSERT INTO jobs VALUES (null, ?, ?, ?, ?, ?, ?, null, ?, 0)',
        (job_name, username, job_submitted_at, JobStatus.INIT.value,
         run_timestamp, None, resources_str))
    _CONN.commit()
    rows = _CURSOR.execute('SELECT job_id FROM jobs WHERE run_timestamp=(?)',
                           (run_timestamp,))
    for row in rows:
        job_id = row[0]
    assert job_id is not None
    return job_id


def _set_status_no_lock(job_id: int, status: JobStatus) -> None:
    """Setting the status of the job in the database."""
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
        _CURSOR.execute(
            'UPDATE jobs SET status=(?), end_at=(?) '
            f'WHERE job_id=(?) {check_end_at_str}',
            (status.value, end_at, job_id))
    else:
        _CURSOR.execute(
            'UPDATE jobs SET status=(?), end_at=NULL '
            'WHERE job_id=(?)', (status.value, job_id))
    _CONN.commit()


def set_status(job_id: int, status: JobStatus) -> None:
    # TODO(mraheja): remove pylint disabling when filelock version updated
    # pylint: disable=abstract-class-instantiated
    with filelock.FileLock(_get_lock_path(job_id)):
        _set_status_no_lock(job_id, status)


def set_job_started(job_id: int) -> None:
    # TODO(mraheja): remove pylint disabling when filelock version updated.
    # pylint: disable=abstract-class-instantiated
    with filelock.FileLock(_get_lock_path(job_id)):
        _CURSOR.execute(
            'UPDATE jobs SET status=(?), start_at=(?), end_at=NULL '
            'WHERE job_id=(?)', (JobStatus.RUNNING.value, time.time(), job_id))
        _CONN.commit()


def get_status_no_lock(job_id: int) -> Optional[JobStatus]:
    """Get the status of the job with the given id.

    This function can return a stale status if there is a concurrent update.
    Make sure the caller will not be affected by the stale status, e.g. getting
    the status in a while loop as in `log_lib._follow_job_logs`. Otherwise, use
    `get_status`.
    """
    rows = _CURSOR.execute('SELECT status FROM jobs WHERE job_id=(?)',
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


def get_statuses_payload(job_ids: List[Optional[int]]) -> str:
    # Per-job lock is not required here, since the staled job status will not
    # affect the caller.
    query_str = ','.join(['?'] * len(job_ids))
    rows = _CURSOR.execute(
        f'SELECT job_id, status FROM jobs WHERE job_id IN ({query_str})',
        job_ids)
    statuses = {job_id: None for job_id in job_ids}
    for (job_id, status) in rows:
        statuses[job_id] = status
    return common_utils.encode_payload(statuses)


def load_statuses_payload(
        statuses_payload: str) -> Dict[Optional[int], Optional[JobStatus]]:
    original_statuses = common_utils.decode_payload(statuses_payload)
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


def get_latest_job_id() -> Optional[int]:
    rows = _CURSOR.execute(
        'SELECT job_id FROM jobs ORDER BY job_id DESC LIMIT 1')
    for (job_id,) in rows:
        return job_id
    return None


def get_job_submitted_or_ended_timestamp_payload(job_id: int,
                                                 get_ended_time: bool) -> str:
    """Get the job submitted/ended timestamp.

    This function should only be called by the jobs controller, which is ok to
    use `submitted_at` instead of `start_at`, because the managed job duration
    need to include both setup and running time and the job will not stay in
    PENDING state.

    The normal job duration will use `start_at` instead of `submitted_at` (in
    `format_job_queue()`), because the job may stay in PENDING if the cluster is
    busy.
    """
    field = 'end_at' if get_ended_time else 'submitted_at'
    rows = _CURSOR.execute(f'SELECT {field} FROM jobs WHERE job_id=(?)',
                           (job_id,))
    for (timestamp,) in rows:
        return common_utils.encode_payload(timestamp)
    return common_utils.encode_payload(None)


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
        })
    return records


def _get_jobs(
        username: Optional[str],
        status_list: Optional[List[JobStatus]] = None) -> List[Dict[str, Any]]:
    """Returns jobs with the given fields, sorted by job_id, descending."""
    if status_list is None:
        status_list = list(JobStatus)
    status_str_list = [status.value for status in status_list]
    if username is None:
        rows = _CURSOR.execute(
            f"""\
            SELECT * FROM jobs
            WHERE status IN ({','.join(['?'] * len(status_list))})
            ORDER BY job_id DESC""",
            (*status_str_list,),
        )
    else:
        rows = _CURSOR.execute(
            f"""\
            SELECT * FROM jobs
            WHERE status IN ({','.join(['?'] * len(status_list))})
            AND username=(?)
            ORDER BY job_id DESC""",
            (*status_str_list, username),
        )

    records = _get_records_from_rows(rows)
    return records


def _get_jobs_by_ids(job_ids: List[int]) -> List[Dict[str, Any]]:
    rows = _CURSOR.execute(
        f"""\
        SELECT * FROM jobs
        WHERE job_id IN ({','.join(['?'] * len(job_ids))})
        ORDER BY job_id DESC""",
        (*job_ids,),
    )
    records = _get_records_from_rows(rows)
    return records


def _get_pending_job(job_id: int) -> Optional[Dict[str, Any]]:
    rows = _CURSOR.execute(
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
                # TODO(zhwu): Backward compatibility, remove after 0.9.0.
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


def fail_all_jobs_in_progress() -> None:
    in_progress_status = [
        status.value for status in JobStatus.nonterminal_statuses()
    ]
    _CURSOR.execute(
        f"""\
        UPDATE jobs SET status=(?)
        WHERE status IN ({','.join(['?'] * len(in_progress_status))})
        """, (JobStatus.FAILED_DRIVER.value, *in_progress_status))
    _CONN.commit()


def update_status() -> None:
    # This will be called periodically by the skylet to update the status
    # of the jobs in the database, to avoid stale job status.
    nonterminal_jobs = _get_jobs(username=None,
                                 status_list=JobStatus.nonterminal_statuses())
    nonterminal_job_ids = [job['job_id'] for job in nonterminal_jobs]

    update_job_status(nonterminal_job_ids)


def is_cluster_idle() -> bool:
    """Returns if the cluster is idle (no in-flight jobs)."""
    in_progress_status = [
        status.value for status in JobStatus.nonterminal_statuses()
    ]
    rows = _CURSOR.execute(
        f"""\
        SELECT COUNT(*) FROM jobs
        WHERE status IN ({','.join(['?'] * len(in_progress_status))})
        """, in_progress_status)
    for (count,) in rows:
        return count == 0
    assert False, 'Should not reach here'


def format_job_queue(jobs: List[Dict[str, Any]]):
    """Format the job queue for display.

    Usage:
        jobs = get_job_queue()
        print(format_job_queue(jobs))
    """
    job_table = log_utils.create_table([
        'ID', 'NAME', 'SUBMITTED', 'STARTED', 'DURATION', 'RESOURCES', 'STATUS',
        'LOG'
    ])
    for job in jobs:
        job_table.add_row([
            job['job_id'],
            job['job_name'],
            log_utils.readable_time_duration(job['submitted_at']),
            log_utils.readable_time_duration(job['start_at']),
            log_utils.readable_time_duration(job['start_at'],
                                             job['end_at'],
                                             absolute=True),
            job['resources'],
            job['status'].colored_str(),
            job['log_path'],
        ])
    return job_table


def dump_job_queue(username: Optional[str], all_jobs: bool) -> str:
    """Get the job queue in encoded json format.

    Args:
        username: The username to show jobs for. Show all the users if None.
        all_jobs: Whether to show all jobs, not just the pending/running ones.
    """
    status_list: Optional[List[JobStatus]] = [
        JobStatus.SETTING_UP, JobStatus.PENDING, JobStatus.RUNNING
    ]
    if all_jobs:
        status_list = None

    jobs = _get_jobs(username, status_list=status_list)
    for job in jobs:
        job['status'] = job['status'].value
        job['log_path'] = os.path.join(constants.SKY_LOGS_DIRECTORY,
                                       job.pop('run_timestamp'))
    return common_utils.encode_payload(jobs)


def load_job_queue(payload: str) -> List[Dict[str, Any]]:
    """Load the job queue from encoded json format.

    Args:
        payload: The encoded payload string to load.
    """
    jobs = common_utils.decode_payload(payload)
    for job in jobs:
        job['status'] = JobStatus(job['status'])
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
                                cancel_all: bool = False) -> str:
    """Cancel jobs.

    Args:
        jobs: Job IDs to cancel. (See `cancel_all` for special semantics.)
        cancel_all: Whether to cancel all jobs. If True, asserts `jobs` is
            set to None. If False and `jobs` is None, cancel the latest
            running job.

    Returns:
        Encoded job IDs that are actually cancelled. Caller should use
        common_utils.decode_payload() to parse.
    """
    if cancel_all:
        # Cancel all in-progress jobs.
        assert jobs is None, ('If cancel_all=True, usage is to set jobs=None')
        job_records = _get_jobs(
            None, [JobStatus.PENDING, JobStatus.SETTING_UP, JobStatus.RUNNING])
    else:
        if jobs is None:
            # Cancel the latest (largest job ID) running job.
            job_records = _get_jobs(None, [JobStatus.RUNNING])[:1]
        else:
            # Cancel jobs with specified IDs.
            job_records = _get_jobs_by_ids(jobs)

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
                    # TODO(zhwu): Backward compatibility, remove after 0.9.0.
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
    return common_utils.encode_payload(cancelled_ids)


def get_run_timestamp(job_id: Optional[int]) -> Optional[str]:
    """Returns the relative path to the log file for a job."""
    _CURSOR.execute(
        """\
            SELECT * FROM jobs
            WHERE job_id=(?)""", (job_id,))
    row = _CURSOR.fetchone()
    if row is None:
        return None
    run_timestamp = row[JobInfoLoc.RUN_TIMESTAMP.value]
    return run_timestamp


def run_timestamp_with_globbing_payload(job_ids: List[Optional[str]]) -> str:
    """Returns the relative paths to the log files for job with globbing."""
    query_str = ' OR '.join(['job_id GLOB (?)'] * len(job_ids))
    _CURSOR.execute(
        f"""\
            SELECT * FROM jobs
            WHERE {query_str}""", job_ids)
    rows = _CURSOR.fetchall()
    run_timestamps = {}
    for row in rows:
        job_id = row[JobInfoLoc.JOB_ID.value]
        run_timestamp = row[JobInfoLoc.RUN_TIMESTAMP.value]
        run_timestamps[str(job_id)] = run_timestamp
    return common_utils.encode_payload(run_timestamps)


class JobLibCodeGen:
    """Code generator for job utility functions.

    Usage:

      >> codegen = JobLibCodeGen.add_job(...)
    """

    _PREFIX = [
        'import os',
        'import getpass',
        'from sky.skylet import job_lib, log_lib, constants',
    ]

    @classmethod
    def add_job(cls, job_name: Optional[str], username: str, run_timestamp: str,
                resources_str: str) -> str:
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
            '\njob_id = job_lib.add_job('
            f'{job_name!r},'
            f'{username!r},'
            f'{run_timestamp!r},'
            f'{resources_str!r})',
            'print("Job ID: " + str(job_id), flush=True)',
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
    def get_job_queue(cls, username: Optional[str], all_jobs: bool) -> str:
        code = [
            'job_queue = job_lib.dump_job_queue('
            f'{username!r}, {all_jobs})', 'print(job_queue, flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def cancel_jobs(cls,
                    job_ids: Optional[List[int]],
                    cancel_all: bool = False) -> str:
        """See job_lib.cancel_jobs()."""
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
            'run_timestamp = job_lib.get_run_timestamp(job_id)',
            f'log_dir = None if run_timestamp is None else os.path.join({constants.SKY_LOGS_DIRECTORY!r}, run_timestamp)',
            f'tail_log_kwargs = {{"job_id": job_id, "log_dir": log_dir, "managed_job_id": {managed_job_id!r}, "follow": {follow}}}',
            f'{_LINUX_NEW_LINE}if getattr(constants, "SKYLET_LIB_VERSION", 1) > 1: tail_log_kwargs["tail"] = {tail}',
            f'{_LINUX_NEW_LINE}log_lib.tail_logs(**tail_log_kwargs)',
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
    def get_run_timestamp_with_globbing(cls,
                                        job_ids: Optional[List[str]]) -> str:
        code = [
            f'job_ids = {job_ids} if {job_ids} is not None '
            'else [job_lib.get_latest_job_id()]',
            'log_dirs = job_lib.run_timestamp_with_globbing_payload(job_ids)',
            'print(log_dirs, flush=True)',
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        code = ';'.join(code)
        return f'{constants.SKY_PYTHON_CMD} -u -c {shlex.quote(code)}'
