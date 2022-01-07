"""Sky job utils, backed by a sqlite database.

This is a remote utility module that provides job queue functionality.
"""
import os
import pathlib
import sqlite3
import subprocess
import time
from typing import Any, Dict, List, Optional

import pendulum
import prettytable


SKY_REMOTE_WORKDIR = '~/sky_workdir'
SKY_LOGS_DIRECTORY = 'sky_logs'

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
        username TEXT,
        job_id INTEGER PRIMARY KEY,
        submitted_at INTEGER,
        status TEXT,
        run_id TEXT)""")

_CONN.commit()


def reserve_next_job_id(username: str, run_id: str) -> int:
    job_submitted_at = int(time.time())
    rows = _CURSOR.execute('select max(job_id) from jobs')
    job_id = 100
    for row in rows:
        if row[0] is not None:
            job_id = row[0]
    job_id += 1
    _CURSOR.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?, ?)',
                    (username, job_id, job_submitted_at, 'INIT', run_id))
    _CONN.commit()
    print(job_id)


def change_status(job_id: int, status: str) -> None:
    _CURSOR.execute('UPDATE jobs SET status=(?) WHERE job_id=(?)',
                    (status, job_id))
    _CONN.commit()


def _get_jobs(username: Optional[str],
              status_list: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    if status_list is None:
        status_list = [
            'PENDING', 'RUNNING', 'INIT', 'STOPPED', 'SUCCEEDED', 'FAILED'
        ]
    if username is None:
        rows = _CURSOR.execute(
            f"""\
            SELECT * FROM jobs
            WHERE status IN ({','.join(['?'] * len(status_list))})
            ORDER BY job_id DESC""",
            (*status_list,),
        )
    else:
        rows = _CURSOR.execute(
            f"""\
            SELECT * FROM jobs
            WHERE status IN ({','.join(['?'] * len(status_list))})
            AND username=(?)
            ORDER BY job_id DESC""",
            (*status_list, username),
        )

    records = []
    for user, job_id, submitted_at, status, run_id in rows:
        records.append({
            'username': user,
            'job_id': job_id,
            'submitted_at': submitted_at,
            'status': status,
            'run_id': run_id,
        })
    return records


def _update_status():
    running_jobs = _get_jobs(username=None, status_list=['RUNNING'])

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
        change_status(job['job_id'], ray_status)


def _readable_time_duration(start: int):
    duration = pendulum.now().subtract(seconds=time.time() - start)
    diff = duration.diff_for_humans()
    diff = diff.replace('second', 'sec')
    diff = diff.replace('minute', 'min')
    diff = diff.replace('hour', 'hr')
    return diff


def _show_job_queue(jobs):
    job_table = prettytable.PrettyTable()
    job_table.field_names = ['JOB', 'USER', 'SUBMITTED', 'STATUS', 'LOG']
    job_table.align['LOG'] = 'l'

    for job in jobs:
        job_table.add_row([
            job['job_id'],
            job['username'],
            _readable_time_duration(job['submitted_at']),
            job['status'],
            os.path.join('sky_logs', job['run_id']),
        ])
    print(job_table)


def show_jobs(username: Optional[str], all_jobs: bool):
    _update_status()
    status_list = ['PENDING', 'RUNNING']
    if all_jobs:
        status_list = None

    jobs = _get_jobs(username, status_list=status_list)
    _show_job_queue(jobs)


def log_dir(job_id: int) -> str:
    """Returns the path to the log file for a job and the status."""
    _update_status()
    rows = _CURSOR.execute(
        """\
            SELECT * FROM jobs
            WHERE job_id=(?)""", (job_id,))
    for row in rows:
        status = row[3]
        run_id = row[4]
    return os.path.join(SKY_REMOTE_WORKDIR, SKY_LOGS_DIRECTORY, run_id), status
