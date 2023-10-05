"""Sky job lib, backed by a sqlite database.

This is a remote utility module that provides job queue functionality.
"""
import enum
import getpass
import json
import os
import pathlib
import shlex
import subprocess
import time
import typing
from typing import Any, Dict, List, Optional, Tuple

import colorama
import filelock
import psutil

from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import db_utils
from sky.utils import log_utils

if typing.TYPE_CHECKING:
    from ray.dashboard.modules.job import pydantic_models as ray_pydantic

logger = sky_logging.init_logger(__name__)

_JOB_STATUS_LOCK = '~/.sky/locks/.job_{}.lock'


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


_DB_PATH = os.path.expanduser('~/.sky/jobs.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)


def create_table(cursor, conn):
    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS jobs (
        job_id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_name TEXT,
        username TEXT,
        submitted_at FLOAT,
        status TEXT,
        run_timestamp TEXT CANDIDATE KEY,
        start_at FLOAT DEFAULT -1)""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS pending_jobs(
        job_id INTEGER,
        run_cmd TEXT,
        submit INTEGER,
        created_time INTEGER
    )""")

    db_utils.add_column_to_table(cursor, conn, 'jobs', 'end_at', 'FLOAT')
    db_utils.add_column_to_table(cursor, conn, 'jobs', 'resources', 'TEXT')

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


# Only update status of the jobs after this many seconds of job submission,
# to avoid race condition with `ray job` to make sure it job has been
# correctly updated.
# TODO(zhwu): This number should be tuned based on heuristics.
_PENDING_SUBMIT_GRACE_PERIOD = 60

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
        subprocess.Popen(run_cmd, shell=True, stdout=subprocess.DEVNULL)

    def schedule_step(self) -> None:
        job_owner = getpass.getuser()
        jobs = self._get_jobs()
        if len(jobs) > 0:
            update_status(job_owner)
        # TODO(zhwu, mraheja): One optimization can be allowing more than one
        # job staying in the pending state after ray job submit, so that to be
        # faster to schedule a large amount of jobs.
        for job_id, run_cmd, submit, created_time in jobs:
            with filelock.FileLock(_get_lock_path(job_id)):
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

    def _get_jobs(self) -> List[Tuple[int, str, int, int]]:
        """Returns the metadata for jobs in the pending jobs table

        The information contains job_id, run command, submit time,
        creation time.
        """
        raise NotImplementedError


class FIFOScheduler(JobScheduler):
    """First in first out job scheduler"""

    def _get_jobs(self) -> List[Tuple[int, str, int, int]]:
        return list(
            _CURSOR.execute('SELECT * FROM pending_jobs ORDER BY job_id'))


scheduler = FIFOScheduler()

_JOB_STATUS_TO_COLOR = {
    JobStatus.INIT: colorama.Fore.BLUE,
    JobStatus.SETTING_UP: colorama.Fore.BLUE,
    JobStatus.PENDING: colorama.Fore.BLUE,
    JobStatus.RUNNING: colorama.Fore.GREEN,
    JobStatus.SUCCEEDED: colorama.Fore.GREEN,
    JobStatus.FAILED: colorama.Fore.RED,
    JobStatus.FAILED_SETUP: colorama.Fore.RED,
    JobStatus.CANCELLED: colorama.Fore.YELLOW,
}

_RAY_TO_JOB_STATUS_MAP = {
    # These are intentionally set this way, because:
    # 1. when the ray status indicates the job is PENDING the generated
    # python program has been `ray job submit` from the job queue
    # and is now PENDING
    # 2. when the ray status indicates the job is RUNNING the job can be in
    # setup or resources may not be allocated yet, i.e. the job should be
    # PENDING.
    # For case 2, update_job_status() would compare this mapped PENDING to
    # the status in our jobs DB and take the max. This is because the job's
    # generated ray program is the only place that can determine a job has
    # reserved resources and actually started running: it will set the
    # status in the DB to SETTING_UP or RUNNING.
    # If there is no setup specified in the task, as soon as it is started
    # (ray's status becomes RUNNING), i.e. it will be very rare that the job
    # will be set to SETTING_UP by the update_job_status, as our generated
    # ray program will set the status to PENDING immediately.
    'PENDING': JobStatus.PENDING,
    'RUNNING': JobStatus.PENDING,
    'SUCCEEDED': JobStatus.SUCCEEDED,
    'FAILED': JobStatus.FAILED,
    'STOPPED': JobStatus.CANCELLED,
}


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


def make_ray_job_id(sky_job_id: int, job_owner: str) -> str:
    return f'{sky_job_id}-{job_owner}'


def make_job_command_with_user_switching(username: str,
                                         command: str) -> List[str]:
    return ['sudo', '-H', 'su', '--login', username, '-c', command]


def add_job(job_name: str, username: str, run_timestamp: str,
            resources_str: str) -> int:
    """Atomically reserve the next available job id for the user."""
    job_submitted_at = time.time()
    # job_id will autoincrement with the null value
    _CURSOR.execute('INSERT INTO jobs VALUES (null, ?, ?, ?, ?, ?, ?, null, ?)',
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

    This function should only be called by the spot controller,
    which is ok to use `submitted_at` instead of `start_at`,
    because the spot job duration need to include both setup
    and running time and the job will not stay in PENDING
    state.

    The normal job duration will use `start_at` instead of
    `submitted_at` (in `format_job_queue()`), because the job
    may stay in PENDING if the cluster is busy.
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
    port = json.load(open(port_path))['ray_port']
    return port


def get_job_submission_port():
    """Get the dashboard port Skypilot-internal Ray cluster uses.

    If the port file does not exist, the cluster was launched before #1790,
    return the default port.
    """
    port_path = os.path.expanduser(constants.SKY_REMOTE_RAY_PORT_FILE)
    if not os.path.exists(port_path):
        return 8265
    port = json.load(open(port_path))['ray_dashboard_port']
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


def _get_pending_jobs():
    rows = _CURSOR.execute(
        'SELECT job_id, created_time, submit FROM pending_jobs')
    rows = list(rows)
    return {
        job_id: {
            'created_time': created_time,
            'submit': submit
        } for job_id, created_time, submit in rows
    }


def update_job_status(job_owner: str,
                      job_ids: List[int],
                      silent: bool = False) -> List[JobStatus]:
    """Updates and returns the job statuses matching our `JobStatus` semantics.

    This function queries `ray job status` and processes those results to match
    our semantics.

    Though we update job status actively in the generated ray program and
    during job cancelling, we still need this to handle the staleness problem,
    caused by instance restarting and other corner cases (if any).

    This function should only be run on the remote instance with ray==2.4.0.
    """
    if len(job_ids) == 0:
        return []

    # TODO: if too slow, directly query against redis.
    ray_job_ids = [make_ray_job_id(job_id, job_owner) for job_id in job_ids]

    job_client = _create_ray_job_submission_client()

    # In ray 2.4.0, job_client.list_jobs returns a list of JobDetails,
    # which contains the job status (str) and submission_id (str).
    job_detail_lists: List['ray_pydantic.JobDetails'] = job_client.list_jobs()

    pending_jobs = _get_pending_jobs()
    job_details = {}
    ray_job_ids_set = set(ray_job_ids)
    for job_detail in job_detail_lists:
        if job_detail.submission_id in ray_job_ids_set:
            job_details[job_detail.submission_id] = job_detail
    job_statuses: List[Optional[JobStatus]] = [None] * len(ray_job_ids)
    for i, ray_job_id in enumerate(ray_job_ids):
        job_id = job_ids[i]
        if ray_job_id in job_details:
            ray_status = job_details[ray_job_id].status
            job_statuses[i] = _RAY_TO_JOB_STATUS_MAP[ray_status]
        if job_id in pending_jobs:
            if pending_jobs[job_id]['created_time'] < psutil.boot_time():
                # The job is stale as it is created before the instance
                # is booted, e.g. the instance is rebooted.
                job_statuses[i] = JobStatus.FAILED
            # Gives a 60 second grace period between job being submit from
            # the pending table until appearing in ray jobs.
            if (pending_jobs[job_id]['submit'] > 0 and
                    pending_jobs[job_id]['submit'] <
                    time.time() - _PENDING_SUBMIT_GRACE_PERIOD):
                # For jobs submitted outside of the grace period, we will
                # consider the ray job status.
                continue
            else:
                # Reset the job status to PENDING even though it may not appear
                # in the ray jobs, so that it will not be considered as stale.
                job_statuses[i] = JobStatus.PENDING

    assert len(job_statuses) == len(job_ids), (job_statuses, job_ids)

    statuses = []
    for job_id, status in zip(job_ids, job_statuses):
        # Per-job status lock is required because between the job status
        # query and the job status update, the job status in the databse
        # can be modified by the generated ray program.
        with filelock.FileLock(_get_lock_path(job_id)):
            original_status = get_status_no_lock(job_id)
            assert original_status is not None, (job_id, status)
            if status is None:
                status = original_status
                if (original_status is not None and
                        not original_status.is_terminal()):
                    # The job may be stale, when the instance is restarted
                    # (the ray redis is volatile). We need to reset the
                    # status of the task to FAILED if its original status
                    # is RUNNING or PENDING.
                    status = JobStatus.FAILED
                    _set_status_no_lock(job_id, status)
                    if not silent:
                        logger.info(f'Updated job {job_id} status to {status}')
            else:
                # Taking max of the status is necessary because:
                # 1. It avoids race condition, where the original status has
                # already been set to later state by the job. We skip the
                # update.
                # 2. _RAY_TO_JOB_STATUS_MAP would map `ray job status`'s
                # `RUNNING` to our JobStatus.SETTING_UP; if a job has already
                # been set to JobStatus.PENDING or JobStatus.RUNNING by the
                # generated ray program, `original_status` (job status from our
                # DB) would already have that value. So we take the max here to
                # keep it at later status.
                status = max(status, original_status)
                if status != original_status:  # Prevents redundant update.
                    _set_status_no_lock(job_id, status)
                    if not silent:
                        logger.info(f'Updated job {job_id} status to {status}')
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
        """, (JobStatus.FAILED.value, *in_progress_status))
    _CONN.commit()


def update_status(job_owner: str) -> None:
    # This will be called periodically by the skylet to update the status
    # of the jobs in the database, to avoid stale job status.
    # NOTE: there might be a INIT job in the database set to FAILED by this
    # function, as the ray job status does not exist due to the app
    # not submitted yet. It will be then reset to PENDING / RUNNING when the
    # app starts.
    nonterminal_jobs = _get_jobs(username=None,
                                 status_list=JobStatus.nonterminal_statuses())
    nonterminal_job_ids = [job['job_id'] for job in nonterminal_jobs]

    update_job_status(job_owner, nonterminal_job_ids)


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


def cancel_jobs_encoded_results(job_owner: str,
                                jobs: Optional[List[int]],
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

    # TODO(zhwu): `job_client.stop_job` will wait for the jobs to be killed, but
    # when the memory is not enough, this will keep waiting.
    job_client = _create_ray_job_submission_client()
    cancelled_ids = []

    # Sequentially cancel the jobs to avoid the resource number bug caused by
    # ray cluster (tracked in #1262).
    for job in job_records:
        job_id = make_ray_job_id(job['job_id'], job_owner)
        # Job is locked to ensure that pending queue does not start it while
        # it is being cancelled
        with filelock.FileLock(_get_lock_path(job['job_id'])):
            try:
                job_client.stop_job(job_id)
            except RuntimeError as e:
                # If the request to the job server fails, we should not
                # set the job to CANCELLED.
                if 'does not exist' not in str(e):
                    logger.warning(str(e))
                    continue

            if job['status'] in [
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

    _PREFIX = ['import os', 'from sky.skylet import job_lib, log_lib']

    @classmethod
    def add_job(cls, job_name: Optional[str], username: str, run_timestamp: str,
                resources_str: str) -> str:
        if job_name is None:
            job_name = '-'
        code = [
            'job_id = job_lib.add_job('
            f'{job_name!r}, '
            f'{username!r}, '
            f'{run_timestamp!r}, '
            f'{resources_str!r})',
            'print("Job ID: " + str(job_id), flush=True)',
        ]
        return cls._build(code)

    @classmethod
    def queue_job(cls, job_id: int, cmd: str) -> str:
        code = ['job_lib.scheduler.queue('
                f'{job_id!r},'
                f'{cmd!r})']
        return cls._build(code)

    @classmethod
    def update_status(cls, job_owner: str) -> str:
        code = [
            f'job_lib.update_status({job_owner!r})',
        ]
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
                    job_owner: str,
                    job_ids: Optional[List[int]],
                    cancel_all: bool = False) -> str:
        """See job_lib.cancel_jobs()."""
        code = [
            (f'cancelled = job_lib.cancel_jobs_encoded_results({job_owner!r},'
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
                  job_owner: str,
                  job_id: Optional[int],
                  spot_job_id: Optional[int],
                  follow: bool = True) -> str:
        # pylint: disable=line-too-long
        code = [
            f'job_id = {job_id} if {job_id} is not None else job_lib.get_latest_job_id()',
            'run_timestamp = job_lib.get_run_timestamp(job_id)',
            f'log_dir = None if run_timestamp is None else os.path.join({constants.SKY_LOGS_DIRECTORY!r}, run_timestamp)',
            f'log_lib.tail_logs({job_owner!r}, job_id, log_dir, {spot_job_id!r}, follow={follow})',
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
        return f'python3 -u -c {shlex.quote(code)}'
