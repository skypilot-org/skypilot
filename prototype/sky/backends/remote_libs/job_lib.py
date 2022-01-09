"""Sky job lib, backed by a sqlite database.

This is a remote utility module that provides job queue functionality.
"""
import enum
import os
import pathlib
import sqlite3
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

import pendulum
import prettytable

SKY_REMOTE_WORKDIR = '~/sky_workdir'
SKY_LOGS_DIRECTORY = 'sky_logs'


class JobStatus(enum.Enum):
    """Job status"""
    INIT = 'INIT'
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    STOPPED = 'STOPPED'


class JobInfoLoc(enum.IntEnum):
    """Job Info's Location in the DB record"""
    JOB_ID = 0
    USERNAME = 1
    SUBMITTED = 2
    STATUS = 3
    RUN_ID = 4


_DB_PATH = os.path.expanduser('~/.sky/jobs.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)

_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

try:
    _CURSOR.execute('select * from jobs limit 0')
except sqlite3.OperationalError:
    # Tables do not exist, create them.
    _CURSOR.execute("""\
      CREATE TABLE jobs (
        job_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        submitted_at INTEGER,
        status TEXT,
        run_id TEXT CANDIDATE KEY)""")

_CONN.commit()


def add_job(username: str, run_id: str) -> int:
    """Reserve the next available job id for the user."""
    job_submitted_at = int(time.time())
    # job_id will autoincrement with the null value
    _CURSOR.execute('INSERT INTO jobs VALUES (null, ?, ?, ?, ?)', (
        username,
        job_submitted_at,
        JobStatus.INIT.value,
        run_id,
    ))
    _CONN.commit()
    rows = _CURSOR.execute('SELECT job_id FROM jobs WHERE run_id=(?)',
                           (run_id,))
    for row in rows:
        job_id = row[0]
    assert job_id is not None
    print(job_id)


def set_status(job_id: int, status: str) -> None:
    _CURSOR.execute('UPDATE jobs SET status=(?) WHERE job_id=(?)',
                    (status, job_id))
    _CONN.commit()


def _get_jobs(username: Optional[str],
              status_list: Optional[List[JobStatus]] = None
             ) -> List[Dict[str, Any]]:
    if status_list is None:
        status_list = [
            JobStatus.PENDING, JobStatus.RUNNING, JobStatus.SUCCEEDED,
            JobStatus.FAILED, JobStatus.STOPPED, JobStatus.INIT
        ]
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

    records = []
    for row in rows:
        if row[0] is None:
            break
        records.append({
            'job_id': row[JobInfoLoc.JOB_ID.value],
            'username': row[JobInfoLoc.USERNAME.value],
            'submitted_at': row[JobInfoLoc.SUBMITTED.value],
            'status': JobStatus[row[JobInfoLoc.STATUS.value]],
            'run_id': row[JobInfoLoc.RUN_ID.value],
        })
    return records


def _update_status() -> None:
    running_jobs = _get_jobs(username=None, status_list=[JobStatus.RUNNING])

    if len(running_jobs) == 0:
        return

    test_cmd = [
        (f'ray job status --address 127.0.0.1:8265 {job["job_id"]} 2>&1 | '
         'grep "Job status"') for job in running_jobs
    ]
    test_cmd = ' && '.join(test_cmd)
    proc = subprocess.run(test_cmd,
                          shell=True,
                          check=True,
                          executable='/bin/bash',
                          stdout=subprocess.PIPE)
    stdout = proc.stdout.decode('utf-8')

    results = stdout.strip().split('\n')
    assert len(results) == len(running_jobs), (results, running_jobs)

    # Process the results
    for i, job in enumerate(running_jobs):
        ray_status = results[i].strip().rstrip('.')
        ray_status = ray_status.rpartition(' ')[-1]
        set_status(job['job_id'], ray_status)


def _readable_time_duration(start: int) -> str:
    duration = pendulum.now().subtract(seconds=time.time() - start)
    diff = duration.diff_for_humans()
    diff = diff.replace('second', 'sec')
    diff = diff.replace('minute', 'min')
    diff = diff.replace('hour', 'hr')
    return diff


def _show_job_queue(jobs) -> None:
    job_table = prettytable.PrettyTable()
    job_table.field_names = ['JOB', 'USER', 'SUBMITTED', 'STATUS', 'LOG']
    job_table.align['LOG'] = 'l'

    for job in jobs:
        job_table.add_row([
            job['job_id'],
            job['username'],
            _readable_time_duration(job['submitted_at']),
            job['status'].value,
            os.path.join('sky_logs', job['run_id']),
        ])
    print(job_table)


def show_jobs(username: Optional[str], all_jobs: bool) -> None:
    """Show the job queue.

    Args:
        username: The username to show jobs for. Show all the users if None.
        all_jobs: Whether to show the completed jobs.
    """
    _update_status()
    status_list = [JobStatus.PENDING, JobStatus.RUNNING]
    if all_jobs:
        status_list = None

    jobs = _get_jobs(username, status_list=status_list)
    _show_job_queue(jobs)


def cancel_jobs(jobs: Optional[List[str]]) -> None:
    """Cancel the jobs.

    Args:
        jobs: The job ids to cancel. If None, cancel all the jobs.
    """
    if jobs is None:
        job_records = _get_jobs(None, [JobStatus.PENDING, JobStatus.RUNNING])
        jobs = [job['job_id'] for job in job_records]
    cancel_cmd = [
        f'ray job stop --address 127.0.0.1:8265 {job_id}' for job_id in jobs
    ]
    cancel_cmd = ';'.join(cancel_cmd)
    subprocess.run(cancel_cmd, shell=True, check=True, executable='/bin/bash')
    for job_id in jobs:
        set_status(job_id, JobStatus.STOPPED.value)


def log_dir(job_id: int) -> Tuple[Optional[str], Optional[str]]:
    """Returns the path to the log file for a job and the status."""
    _update_status()
    rows = _CURSOR.execute(
        """\
            SELECT * FROM jobs
            WHERE job_id=(?)""", (job_id,))
    for row in rows:
        status = row[JobInfoLoc.STATUS.value]
        run_id = row[JobInfoLoc.RUN_ID.value]
    return os.path.join(SKY_REMOTE_WORKDIR, SKY_LOGS_DIRECTORY, run_id), status
