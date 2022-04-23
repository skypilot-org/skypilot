"""The database for spot jobs status."""
# TODO(zhwu): maybe use file based status instead of database, so
# that we can easily switch to a s3-based storage.
import enum
import pathlib
import sqlite3
import time
from typing import Any, Dict, List, Optional

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

_DB_PATH = pathlib.Path('~/.sky/spot_jobs.db')
_DB_PATH = _DB_PATH.expanduser().absolute()
_DB_PATH.parents[0].mkdir(parents=True, exist_ok=True)

_CONN = sqlite3.connect(str(_DB_PATH))
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
    job_duration INTEGER DEFAULT 0)""")
# job_duration is the time a job actually runs before last_recover,
# excluding the provision and recovery time.
# If the job is not finished:
# total_job_duration = now() - last_recovered_at + job_duration
# If the job is not finished:
# total_job_duration = end_at - last_recovered_at + job_duration

_CONN.commit()
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

    @classmethod
    def terminal_status(cls) -> List['SpotStatus']:
        return (cls.SUCCEEDED, cls.FAILED, cls.CANCELLED)


# === Status transition functions ===
def submit(name: str, run_timestamp: str, resources_str: str) -> int:
    """Insert a new spot job, returns the success."""
    _CURSOR.execute(
        """\
        INSERT INTO spot
        (job_id, job_name, resources, submitted_at, status, run_timestamp)
        VALUES (null, ?, ?, ?, ?, ?)""",
        (name, resources_str, time.time(), SpotStatus.SUBMITTED.value,
         run_timestamp))
    _CONN.commit()
    job_id = _CURSOR.execute(
        """\
        SELECT job_id FROM spot WHERE run_timestamp = ?""",
        (run_timestamp,)).fetchone()[0]
    assert job_id is not None, 'Failed to add job'
    return job_id


def starting(job_id: int):
    logger.info('Launching the spot cluster...')
    _CURSOR.execute("""\
        UPDATE spot SET status=(?) WHERE job_id=(?)""",
                    (SpotStatus.STARTING.value, job_id))
    _CONN.commit()


def started(job_id: int):
    logger.info('Job started.')
    _CURSOR.execute(
        """\
        UPDATE spot SET status=(?), start_at=(?), last_recovered_at=(?)
        WHERE job_id=(?)""",
        (SpotStatus.RUNNING.value, time.time(), time.time(), job_id))
    _CONN.commit()


def recovering(job_id: int):
    logger.info('=== Recovering... ===')
    _CURSOR.execute(
        """\
            UPDATE spot SET
            status=(?), job_duration=job_duration+(?)-last_recovered_at
            WHERE job_id=(?)""",
        (SpotStatus.RECOVERING.value, time.time(), job_id))
    _CONN.commit()


def recovered(job_id: int):
    _CURSOR.execute(
        """\
        UPDATE spot SET
        status=(?), last_recovered_at=(?), recovery_count=recovery_count+1
        WHERE job_id=(?)""", (SpotStatus.RUNNING.value, time.time(), job_id))
    _CONN.commit()
    logger.info('==== Recovered. ====')


def succeeded(job_id: int):
    _CURSOR.execute(
        """\
        UPDATE spot SET
        status=(?), end_at=(?)
        WHERE job_id=(?)""", (SpotStatus.SUCCEEDED.value, time.time(), job_id))
    _CONN.commit()
    logger.info('Job succeeded.')


def failed(job_id: int):
    _CURSOR.execute(
        """\
        UPDATE spot SET
        status=(?), end_at=(?)
        WHERE job_id=(?)""", (SpotStatus.FAILED.value, time.time(), job_id))
    _CONN.commit()
    logger.info('Job failed.')


def cancelled(job_id: int):
    _CURSOR.execute(
        """\
        UPDATE spot SET
        status=(?), end_at=MIN(end_at, (?))
        WHERE job_id=(?)""", (SpotStatus.CANCELLED.value, time.time(), job_id))
    _CONN.commit()
    logger.info('Job cancelled.')


# ======== utility functions ========


def get_nonterminal_job_ids_by_name(name: str) -> List[int]:

    rows = _CURSOR.execute(
        f"""\
        SELECT job_id FROM spot
        WHERE job_name=(?) AND
        status NOT IN
        ({", ".join(["?"] * len(SpotStatus.terminal_status()))})""",
        (name, *[status.value for status in SpotStatus.terminal_status()]))
    job_ids = [row[0] for row in rows if row[0] is not None]
    return job_ids


def get_status(job_id: int) -> Optional[SpotStatus]:
    """Get the status of a job."""
    status = _CURSOR.execute(
        """\
        SELECT status FROM spot WHERE job_id=(?)""", (job_id,)).fetchone()
    if status is None:
        return None
    return SpotStatus(status[0])


def get_spot_jobs() -> List[Dict[str, Any]]:
    """Get spot clusters' status."""
    rows = _CURSOR.execute("""\
        SELECT * FROM spot ORDER BY submitted_at DESC""")
    jobs = []
    for row in rows:
        job_dict = dict(zip(columns, row))
        job_dict['status'] = SpotStatus(job_dict['status'])
        jobs.append(job_dict)
    return jobs
