import enum
import pathlib
import sqlite3
from typing import Any, Dict, List

_DB_PATH = pathlib.Path('~/.sky/job.db')
_DB_PATH.expanduser()
_DB_PATH.parents[0].mkdir(parents=True, exist_ok=True)

_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS spot (
    job_name TEXT PRIMARY KEY,
    submitted_at INTEGER,
    status TEXT,
    run_timestamp TEXT CANDIDATE KEY,
    start_at INTEGER,
    end_at INTERGER,
    last_recovered_at INTEGER DEFAULT -1,
    recovery_count INTEGER,
    job_duration INTEGER,)""")
# job_duration is the time a job actually runs, excluding the provision and recovery time.
# If the job is not finished:
# total_job_duration = now() - last_recovered_at + job_duration


class SpotStatus(enum.Enum):
    INIT = 'INIT'
    LAUNCHING = 'LAUNCHING'
    RUNNING = 'RUNNING'
    RECOVERING = 'RECOVERING'
    PENDING = 'PENDING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'


def insert_job() -> bool:
    """Insert a new spot job, returns the success."""
    raise NotImplemented


def get_spot_jobs() -> List[Dict[str, Any]]:
    """Get spot clusters' status."""
    raise NotImplemented
