"""The database for spot jobs status."""
# TODO(zhwu): maybe use file based status instead of database, so
# that we can easily switch to a s3-based storage.
import enum
import pathlib
import sqlite3
import time
from typing import Any, Dict, List

from sky import sky_logging
from sky.skylet import log_utils

logger = sky_logging.init_logger(__name__)

_DB_PATH = pathlib.Path('~/.sky/job.db')
_DB_PATH.expanduser()
_DB_PATH.parents[0].mkdir(parents=True, exist_ok=True)

_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS spot (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT,
    resources TEXT,
    submitted_at INTEGER,
    status TEXT,
    run_timestamp TEXT CANDIDATE KEY,
    start_at INTEGER,
    end_at INTERGER,
    last_recovered_at INTEGER DEFAULT -1,
    recovery_count INTEGER DEFAULT 0,
    job_duration INTEGER,)""")
# job_duration is the time a job actually runs, excluding the provision
# and recovery time. If the job is not finished:
# total_job_duration = now() - last_recovered_at + job_duration
columns = [
    'job_id', 'job_name', 'resources', 'submitted_at', 'status',
    'run_timestamp', 'start_at', 'end_at', 'last_recovered_at',
    'recovery_count', 'job_duration'
]


class SpotStatus(enum.Enum):
    """Spot job status, designed to be in serverless style"""
    SUBMITTED = 'SUBMITTED'
    STARTING = 'STARTING'
    RUNNING = 'RUNNING'
    RECOVERING = 'RECOVERING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'

    def is_terminal(self) -> bool:
        return self in (self.SUCCEEDED, self.FAILED, self.CANCELLED)


def submit(name: str, run_timestamp: str, resources_str: str) -> int:
    """Insert a new spot job, returns the success."""
    _CURSOR.execute(
        """\
        INSERT INTO spot
        (job_id, job_name, resources, submitted_at, status, run_timestamp)
        VALUES (null, ?, ?, ?, ?, ?))""",
        (name, resources_str, time.time(), SpotStatus.SUBMITTED, run_timestamp))
    _CONN.commit()
    job_id = _CURSOR.execute(
        """\
        SELECT job_id FROM spot WHERE run_timestamp = ?""",
        (run_timestamp,)).fetchone()
    assert job_id is not None, 'Failed to add job'
    return job_id


def starting(job_id: int):
    logger.info('Launching the spot cluster...')
    _CURSOR.execute("""\
        UPDATE spot SET status=(?) WHERE job_id=(?)""",
                    (SpotStatus.STARTING, job_id))
    _CONN.commit()


def started(job_id: int):
    logger.info('Job started.')
    _CURSOR.execute(
        """\
        UPDATE spot SET status=(?), start_at=(?) WHERE job_id=(?)""",
        (SpotStatus.RUNNING, time.time(), job_id))
    _CONN.commit()


def recovering(job_id: int):
    logger.info('=== Recovering... ===')
    _CURSOR.execute(
        """\
            UPDATE spot SET
            status=(?), job_duration=job_duration+(?)-last_recovered_at
            WHERE job_id=(?)""", (SpotStatus.RECOVERING, time.time(), job_id))
    _CONN.commit()


def recovered(job_id: int):
    _CURSOR.execute(
        """\
        UPDATE spot SET
        status=(?), last_recovered_at=(?), recovery_count=recovery_count+1
        WHERE job_id=(?)""", (SpotStatus.RUNNING, time.time(), job_id))
    _CONN.commit()
    logger.info('==== Recovered. ====')


def succeeded(job_id: int):
    _CURSOR.execute(
        """\
        UPDATE spot SET
        status=(?), end_at=(?), job_duration=job_duration+(?)-start_at
        WHERE job_id=(?)""",
        (SpotStatus.SUCCEEDED, time.time(), time.time(), job_id))
    _CONN.commit()
    logger.info('Job succeeded.')


def failed(job_id: int):
    _CURSOR.execute(
        """\
        UPDATE spot SET
        status=(?), end_at=(?), job_duration=job_duration+(?)-start_at
        WHERE job_id=(?)""",
        (SpotStatus.FAILED, time.time(), time.time(), job_id))
    _CONN.commit()
    logger.info('Job failed.')


def cancelled(job_id: int):
    _CURSOR.execute(
        """\
        UPDATE spot SET
        status=(?), end_at=(?), job_duration=job_duration+(?)-start_at
        WHERE job_id=(?)""",
        (SpotStatus.CANCELLED, time.time(), time.time(), job_id))
    _CONN.commit()
    logger.info('Job cancelled.')


def get_spot_jobs() -> List[Dict[str, Any]]:
    """Get spot clusters' status."""
    rows = _CURSOR.execute("""\
        SELECT * FROM spot ORDER BY submitted_at DESC""")
    jobs = []
    for row in rows:
        jobs.append(dict(zip(columns, row)))
    return jobs


def show_jobs():
    """Show all spot jobs."""
    jobs = get_spot_jobs()

    job_table = log_utils.create_table([
        'ID', 'NAME', 'RESOURCES', 'SUBMITTED', 'STARTED', 'TOT. DURATION',
        'JOB DURATION', 'STATUS', 'RECOVER CNT'
    ])
    for job in jobs:
        job_table.add_row([
            job['job_id'],
            job['job_name'],
            job['resources'],
            log_utils.readable_time_duration(job['submitted_at']),
            log_utils.readable_time_duration(job['start_at']),
            log_utils.readable_time_duration(job['start_at'],
                                             job['end_at'],
                                             absolute=True),
            log_utils.readable_time_duration(job['last_recovered_at'] -
                                             job['job_duration'],
                                             job['end_at'],
                                             absolute=True),
            job['status'].value,
        ])
    print(job_table)
