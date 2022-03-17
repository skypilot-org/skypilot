"""Sky job lib, backed by a sqlite database.

This is a remote utility module that provides job queue functionality.
"""
import enum
import os
import pathlib
import re
import sqlite3
import subprocess
import time
from typing import Any, Dict, List, Optional

import pendulum

from sky.skylet import util_lib

SKY_LOGS_DIRECTORY = '~/sky_logs'


class JobStatus(enum.Enum):
    """Job status"""
    INIT = 'INIT'
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'


_RAY_TO_JOB_STATUS_MAP = {
    'RUNNING': JobStatus.RUNNING,
    'succeeded': JobStatus.SUCCEEDED,
    'failed': JobStatus.FAILED,
    'stopped': JobStatus.CANCELLED,
}

ANSI_ESCAPE = re.compile(r'(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]')


class JobInfoLoc(enum.IntEnum):
    """Job Info's Location in the DB record"""
    JOB_ID = 0
    JOB_NAME = 1
    USERNAME = 2
    SUBMITTED_AT = 3
    STATUS = 4
    RUN_TIMESTAMP = 5
    START_AT = 6


_DB_PATH = os.path.expanduser('~/.sky/jobs.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)

_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT,
    username TEXT,
    submitted_at INTEGER,
    status TEXT,
    run_timestamp TEXT CANDIDATE KEY,
    start_at INTEGER)""")

_CONN.commit()


def add_job(job_name: str, username: str, run_timestamp: str) -> int:
    """Atomically reserve the next available job id for the user."""
    job_submitted_at = int(time.time())
    # job_id will autoincrement with the null value
    _CURSOR.execute('INSERT INTO jobs VALUES (null, ?, ?, ?, ?, ?, ?)',
                    (job_name, username, job_submitted_at, JobStatus.INIT.value,
                     run_timestamp, None))
    _CONN.commit()
    rows = _CURSOR.execute('SELECT job_id FROM jobs WHERE run_timestamp=(?)',
                           (run_timestamp,))
    for row in rows:
        job_id = row[0]
    assert job_id is not None
    return job_id


def set_status(job_id: int, status: JobStatus) -> None:
    assert status != JobStatus.RUNNING, (
        'Please use set_job_started() to set job status to RUNNING')
    _CURSOR.execute('UPDATE jobs SET status=(?) WHERE job_id=(?)',
                    (status.value, job_id))
    _CONN.commit()


def get_status(job_id: int) -> Optional[JobStatus]:
    _update_status()
    rows = _CURSOR.execute('SELECT status FROM jobs WHERE job_id=(?)',
                           (job_id,))
    for row in rows:
        return row[0]


def set_job_started(job_id: int) -> None:
    _CURSOR.execute('UPDATE jobs SET status=(?), start_at=(?) WHERE job_id=(?)',
                    (JobStatus.RUNNING.value, int(time.time()), job_id))
    _CONN.commit()


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
            'status': JobStatus[row[JobInfoLoc.STATUS.value]],
            'run_timestamp': row[JobInfoLoc.RUN_TIMESTAMP.value],
            'start_at': row[JobInfoLoc.START_AT.value],
        })
    return records


def _get_jobs(
        username: Optional[str],
        status_list: Optional[List[JobStatus]] = None) -> List[Dict[str, Any]]:
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


def query_job_status(job_ids: List[int]) -> List[JobStatus]:
    """Return the status of the jobs based on the `ray job status` command.

    Though we update job status actively in ray program and job cancelling,
    we still need this to handle staleness problem, caused by instance
    restarting and other corner cases (if any).
    """
    if len(job_ids) == 0:
        return []

    # TODO: if too slow, directly query against redis.
    test_cmd = [
        (
            f'(ray job status --address 127.0.0.1:8265 {job} 2>&1 | '
            # Not a typo, ray has inconsistent output for job status.
            # succeeded: Job 'job_id' succeeded
            # running: job 'job_id': RUNNING
            # stopped: Job 'job_id' was stopped
            # failed: Job 'job_id' failed
            f'grep "ob \'{job}\'" || echo "not found")') for job in job_ids
    ]
    test_cmd = ' && '.join(test_cmd)
    proc = subprocess.run(test_cmd,
                          shell=True,
                          check=True,
                          executable='/bin/bash',
                          stdout=subprocess.PIPE)
    stdout = proc.stdout.decode('utf-8')

    results = stdout.strip().split('\n')
    assert len(results) == len(job_ids), (results, job_ids)

    # Process the results
    job_status_list = []
    for job_id, res in zip(job_ids, results):
        # Replace the color codes in the output
        res = ANSI_ESCAPE.sub('', res.strip().rstrip('.'))
        if res == 'not found':
            # The job may be stale, when the instance is restarted (the ray
            # redis is volatile). We need to reset the status of the task to
            # FAILED if its original status is RUNNING or PENDING.
            rows = _CURSOR.execute('SELECT * FROM jobs WHERE job_id=(?)',
                                   (job_id,))
            for row in rows:
                status = JobStatus[row[JobInfoLoc.STATUS.value]]
            if status in [JobStatus.RUNNING, JobStatus.PENDING]:
                status = JobStatus.FAILED
        else:
            ray_status = res.rpartition(' ')[-1]
            status = _RAY_TO_JOB_STATUS_MAP[ray_status]
        job_status_list.append(status)
    return job_status_list


def _update_status() -> None:
    running_jobs = _get_jobs(username=None, status_list=[JobStatus.RUNNING])
    running_job_ids = [job['job_id'] for job in running_jobs]

    job_status = query_job_status(running_job_ids)
    # Process the results
    for job, status in zip(running_jobs, job_status):
        if status != JobStatus.RUNNING:
            set_status(job['job_id'], status)


def _readable_time_duration(start: Optional[int]) -> str:
    if start is None:
        return '-'
    duration = pendulum.now().subtract(seconds=time.time() - start)
    diff = duration.diff_for_humans()
    diff = diff.replace('second', 'sec')
    diff = diff.replace('minute', 'min')
    diff = diff.replace('hour', 'hr')
    return diff


def _show_job_queue(jobs) -> None:
    job_table = util_lib.create_table(
        ['ID', 'NAME', 'USER', 'SUBMITTED', 'STARTED', 'STATUS', 'LOG'])

    for job in jobs:
        job_table.add_row([
            job['job_id'],
            job['job_name'],
            job['username'],
            _readable_time_duration(job['submitted_at']),
            _readable_time_duration(job['start_at']),
            job['status'].value,
            os.path.join(SKY_LOGS_DIRECTORY, job['run_timestamp']),
        ])
    print(job_table)


def show_jobs(username: Optional[str], all_jobs: bool) -> None:
    """Show the job queue.

    Args:
        username: The username to show jobs for. Show all the users if None.
        all_jobs: Whether to show all jobs, not just the pending/running ones.
    """
    _update_status()
    status_list = [JobStatus.PENDING, JobStatus.RUNNING]
    if all_jobs:
        status_list = None

    jobs = _get_jobs(username, status_list=status_list)
    _show_job_queue(jobs)


def cancel_jobs(jobs: Optional[List[int]]) -> None:
    """Cancel the jobs.

    Args:
        jobs: The job ids to cancel. If None, cancel all the jobs.
    """
    # Update the status of the jobs to avoid setting the status of staled
    # jobs to CANCELLED.
    _update_status()
    if jobs is None:
        job_records = _get_jobs(None, [JobStatus.PENDING, JobStatus.RUNNING])
    else:
        job_records = _get_jobs_by_ids(jobs)
    jobs = [job['job_id'] for job in job_records]
    cancel_cmd = [
        f'ray job stop --address 127.0.0.1:8265 {job_id}' for job_id in jobs
    ]
    cancel_cmd = ';'.join(cancel_cmd)
    subprocess.run(cancel_cmd, shell=True, check=False, executable='/bin/bash')
    for job in job_records:
        if job['status'] in [JobStatus.PENDING, JobStatus.RUNNING]:
            set_status(job['job_id'], JobStatus.CANCELLED)


def log_dir(job_id: int) -> Optional[str]:
    """Returns the relative path to the log file for a job."""
    _CURSOR.execute(
        """\
            SELECT * FROM jobs
            WHERE job_id=(?)""", (job_id,))
    row = _CURSOR.fetchone()
    if row is None:
        return None, None
    run_timestamp = row[JobInfoLoc.RUN_TIMESTAMP.value]
    return os.path.join(SKY_LOGS_DIRECTORY, run_timestamp)
