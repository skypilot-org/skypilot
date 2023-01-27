"""The database for spot jobs status."""
# TODO(zhwu): maybe use file based status instead of database, so
# that we can easily switch to a s3-based storage.
import enum
import pathlib
import sqlite3
import time
from typing import Any, Dict, List, Optional

import colorama

from sky import sky_logging
from sky.backends import backend_utils

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
    submitted_at FLOAT,
    status TEXT,
    run_timestamp TEXT CANDIDATE KEY,
    start_at FLOAT DEFAULT NULL,
    end_at FLOAT DEFAULT NULL,
    last_recovered_at FLOAT DEFAULT -1,
    recovery_count INTEGER DEFAULT 0,
    job_duration FLOAT DEFAULT 0)""")

# job_duration is the time a job actually runs (including the
# setup duration) before last_recover, excluding the provision
# and recovery time.
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
    """Spot job status, designed to be in serverless style.

    The SpotStatus is a higher level status than the JobStatus.
    Each spot job submitted to the spot cluster, will have a JobStatus
    on that spot cluster:
        JobStatus = [INIT, SETTING_UP, PENDING, RUNNING, ...]
    Whenever the spot cluster is preempted and recovered, the JobStatus
    will go through the statuses above again.
    That means during the lifetime of a spot job, its JobsStatus could be
    reset to INIT or SETTING_UP multiple times (depending on the preemptions).

    However, a spot job only has one SpotStatus on the spot controller.
        SpotStatus = [PENDING, SUBMITTED, STARTING, RUNNING, ...]
    Mapping from JobStatus to SpotStatus:
        INIT            ->  STARTING/RECOVERING
        SETTING_UP      ->  RUNNING
        PENDING         ->  RUNNING
        RUNNING         ->  RUNNING
        SUCCEEDED       ->  SUCCEEDED
        FAILED          ->  FAILED
        FAILED_SETUP    ->  FAILED_SETUP
    Note that the JobStatus will not be stuck in PENDING, because each spot
    cluster is dedicated to a spot job, i.e. there should always be enough
    resource to run the job and the job will be immediately transitioned to
    RUNNING.
    """
    # PENDING: Waiting for the spot controller to have a slot to run the
    # controller process.
    # The submitted_at timestamp of the spot job in the 'spot' table will be
    # set to the time when the job is firstly submitted by the user (set to
    # PENDING).
    PENDING = 'PENDING'
    # SUBMITTED: The spot controller starts the controller process.
    SUBMITTED = 'SUBMITTED'
    # STARTING: The controller process is launching the spot cluster for
    # the spot job.
    STARTING = 'STARTING'
    # RUNNING: The job is submitted to the spot cluster, and is setting up
    # or running.
    # The start_at timestamp of the spot job in the 'spot' table will be set
    # to the time when the job is firstly transitioned to RUNNING.
    RUNNING = 'RUNNING'
    # RECOVERING: The spot cluster is preempted, and the controller process
    # is recovering the spot cluster (relaunching/failover).
    RECOVERING = 'RECOVERING'
    # Terminal statuses
    # SUCCEEDED: The job is finished successfully.
    SUCCEEDED = 'SUCCEEDED'
    # FAILED: The job is finished with failure from the user's program.
    FAILED = 'FAILED'
    # FAILED_SETUP: The job is finished with failure from the user's setup
    # script.
    FAILED_SETUP = 'FAILED_SETUP'
    # FAILED_NO_RESOURCE: The job is finished with failure because there is no
    # resource available in the cloud provider(s) to launch the spot cluster.
    FAILED_NO_RESOURCE = 'FAILED_NO_RESOURCE'
    # FAILED_CONTROLLER: The job is finished with failure because of unexpected
    # error in the controller process.
    FAILED_CONTROLLER = 'FAILED_CONTROLLER'
    # CANCELLED: The job is cancelled by the user.
    CANCELLED = 'CANCELLED'

    def is_terminal(self) -> bool:
        return self in self.terminal_statuses()

    def is_failed(self) -> bool:
        return self in self.failure_statuses()

    def colored_str(self):
        color = _SPOT_STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'

    @classmethod
    def terminal_statuses(cls) -> List['SpotStatus']:
        return [
            cls.SUCCEEDED, cls.FAILED, cls.FAILED_SETUP, cls.FAILED_NO_RESOURCE,
            cls.FAILED_CONTROLLER, cls.CANCELLED
        ]

    @classmethod
    def failure_statuses(cls) -> List['SpotStatus']:
        return [
            cls.FAILED, cls.FAILED_SETUP, cls.FAILED_NO_RESOURCE,
            cls.FAILED_CONTROLLER
        ]


_SPOT_STATUS_TO_COLOR = {
    SpotStatus.PENDING: colorama.Fore.BLUE,
    SpotStatus.SUBMITTED: colorama.Fore.BLUE,
    SpotStatus.STARTING: colorama.Fore.BLUE,
    SpotStatus.RUNNING: colorama.Fore.GREEN,
    SpotStatus.RECOVERING: colorama.Fore.CYAN,
    SpotStatus.SUCCEEDED: colorama.Fore.GREEN,
    SpotStatus.FAILED: colorama.Fore.RED,
    SpotStatus.FAILED_SETUP: colorama.Fore.RED,
    SpotStatus.FAILED_NO_RESOURCE: colorama.Fore.RED,
    SpotStatus.FAILED_CONTROLLER: colorama.Fore.RED,
    SpotStatus.CANCELLED: colorama.Fore.YELLOW,
}


# === Status transition functions ===
def set_pending(job_id: int, name: str, resources_str: str):
    """Set the job to pending state."""
    _CURSOR.execute(
        """\
        INSERT INTO spot
        (job_id, job_name, resources, status) VALUES (?, ?, ?, ?)""",
        (job_id, name, resources_str, SpotStatus.PENDING.value))
    _CONN.commit()


def set_submitted(job_id: int, name: str, run_timestamp: str,
                  resources_str: str):
    """Set the job to submitted."""
    # Use the timestamp in the `run_timestamp` ('sky-2022-10...'), to make the
    # log directory and submission time align with each other, so as to make
    # it easier to find them based on one of the values.
    # Also, using the earlier timestamp should be closer to the term
    # `submit_at`, which represents the time the spot task is submitted.
    submit_time = backend_utils.get_timestamp_from_run_timestamp(run_timestamp)
    _CURSOR.execute(
        """\
        UPDATE spot SET
        job_name=(?),
        resources=(?),
        submitted_at=(?),
        status=(?),
        run_timestamp=(?)
        WHERE job_id=(?)""",
        (name, resources_str, submit_time, SpotStatus.SUBMITTED.value,
         run_timestamp, job_id))
    _CONN.commit()


def set_starting(job_id: int):
    logger.info('Launching the spot cluster...')
    _CURSOR.execute("""\
        UPDATE spot SET status=(?) WHERE job_id=(?)""",
                    (SpotStatus.STARTING.value, job_id))
    _CONN.commit()


def set_started(job_id: int, start_time: float):
    logger.info('Job started.')
    _CURSOR.execute(
        """\
        UPDATE spot SET status=(?), start_at=(?), last_recovered_at=(?)
        WHERE job_id=(?)""",
        (SpotStatus.RUNNING.value, start_time, start_time, job_id))
    _CONN.commit()


def set_recovering(job_id: int):
    logger.info('=== Recovering... ===')
    _CURSOR.execute(
        """\
            UPDATE spot SET
            status=(?), job_duration=job_duration+(?)-last_recovered_at
            WHERE job_id=(?)""",
        (SpotStatus.RECOVERING.value, time.time(), job_id))
    _CONN.commit()


def set_recovered(job_id: int, recovered_time: float):
    _CURSOR.execute(
        """\
        UPDATE spot SET
        status=(?), last_recovered_at=(?), recovery_count=recovery_count+1
        WHERE job_id=(?)""", (SpotStatus.RUNNING.value, recovered_time, job_id))
    _CONN.commit()
    logger.info('==== Recovered. ====')


def set_succeeded(job_id: int, end_time: float):
    _CURSOR.execute(
        """\
        UPDATE spot SET
        status=(?), end_at=(?)
        WHERE job_id=(?) AND end_at IS null""",
        (SpotStatus.SUCCEEDED.value, end_time, job_id))
    _CONN.commit()
    logger.info('Job succeeded.')


def set_failed(job_id: int,
               failure_type: SpotStatus,
               end_time: Optional[float] = None):
    assert failure_type.is_failed(), failure_type
    end_time = time.time() if end_time is None else end_time
    fields_to_set = {
        'end_at': end_time,
        'status': failure_type.value,
    }
    previsou_status = _CURSOR.execute(
        'SELECT status FROM spot WHERE job_id=(?)', (job_id,)).fetchone()
    previsou_status = SpotStatus(previsou_status[0])
    if previsou_status in [SpotStatus.RECOVERING]:
        # If the job is recovering, we should set the
        # last_recovered_at to the end_time, so that the
        # end_at - last_recovered_at will not be affect the job duration
        # calculation.
        fields_to_set['last_recovered_at'] = end_time
    set_str = ', '.join(f'{k}=(?)' for k in fields_to_set)
    _CURSOR.execute(
        f"""\
        UPDATE spot SET
        {set_str}
        WHERE job_id=(?) AND end_at IS null""",
        (*list(fields_to_set.values()), job_id))
    _CONN.commit()
    if failure_type in [SpotStatus.FAILED, SpotStatus.FAILED_SETUP]:
        logger.info(
            f'Job failed due to user code (status: {failure_type.value}).')
    elif failure_type == SpotStatus.FAILED_NO_RESOURCE:
        logger.info('Job failed due to failing to find available resources '
                    'after retries.')
    else:
        assert failure_type == SpotStatus.FAILED_CONTROLLER, failure_type
        logger.info('Job failed due to unexpected controller failure.')


def set_cancelled(job_id: int):
    _CURSOR.execute(
        """\
        UPDATE spot SET
        status=(?), end_at=(?)
        WHERE job_id=(?) AND end_at IS null""",
        (SpotStatus.CANCELLED.value, time.time(), job_id))
    _CONN.commit()
    logger.info('Job cancelled.')


# ======== utility functions ========


def get_nonterminal_job_ids_by_name(name: Optional[str]) -> List[int]:
    """Get non-terminal job ids by name."""
    name_filter = 'AND job_name=(?)' if name is not None else ''
    field_values = [status.value for status in SpotStatus.terminal_statuses()]
    if name is not None:
        field_values.append(name)
    statuses = ', '.join(['?'] * len(SpotStatus.terminal_statuses()))
    rows = _CURSOR.execute(
        f"""\
        SELECT job_id FROM spot
        WHERE status NOT IN
        ({statuses})
        {name_filter}""", field_values)
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
        SELECT * FROM spot ORDER BY job_id DESC""")
    jobs = []
    for row in rows:
        job_dict = dict(zip(columns, row))
        job_dict['status'] = SpotStatus(job_dict['status'])
        jobs.append(job_dict)
    return jobs


def get_task_name_by_job_id(job_id: int) -> str:
    """Get the task name of a job."""
    task_name = _CURSOR.execute(
        """\
        SELECT job_name FROM spot WHERE job_id=(?)""", (job_id,)).fetchone()
    return task_name[0]


def get_latest_job_id() -> Optional[int]:
    """Get the latest job id."""
    rows = _CURSOR.execute("""\
        SELECT job_id FROM spot ORDER BY submitted_at DESC LIMIT 1""")
    for (job_id,) in rows:
        return job_id
    return None
