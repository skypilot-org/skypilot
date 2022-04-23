"""User interfaces with managed spot jobs."""

import enum
import pathlib
import shlex
from typing import List

from sky.spot import spot_status
from sky.skylet.utils import log_utils

SIGNAL_FILE_PREFIX = '/tmp/sky_spot_controller_singal_{}'
JOB_STATUS_CHECK_GAP_SECONDS = 60


class UserSignal(enum.Enum):
    """The signal to be sent to the user."""
    CANCEL = 'CANCEL'
    # TODO(zhwu): We can have more communication signals here if needed
    # in the future.


# ======== user functions ========


def cancel_jobs_by_id(job_ids: List[int]) -> str:
    """Cancel jobs by id."""
    if len(job_ids) == 0:
        return 'No job to cancel.'
    for job_id in job_ids:
        signal_file = pathlib.Path(SIGNAL_FILE_PREFIX.format(job_id))
        with signal_file.open('w') as f:
            f.write(UserSignal.CANCEL.value)
            f.flush()

    identity_str = f'job ID {job_ids[0]} is'
    if len(job_ids) > 0:
        identity_str = f'job IDs {job_ids} are'

    return (f'Jobs with {identity_str} scheduled to be cancelled within '
            f'{JOB_STATUS_CHECK_GAP_SECONDS} seconds.')


def cancel_job_by_name(job_name: str) -> str:
    """Cancel a job by name."""
    job_ids = spot_status.get_job_ids_by_name(job_name)
    if len(job_ids) == 0:
        return f'No job found with name {job_name!r}'
    if len(job_ids) > 1:
        return (f'Multiple jobs found with name {job_name!r}.\n'
                f'Job IDs: {job_ids}')
    job_id = job_ids[0]
    cancel_jobs_by_id([job_id])
    return (f'Job {job_name!r} is scheduled to be cancelled within '
            f'{JOB_STATUS_CHECK_GAP_SECONDS} seconds.')


def show_jobs() -> str:
    """Show all spot jobs."""
    jobs = spot_status.get_spot_jobs()

    job_table = log_utils.create_table([
        'ID', 'NAME', 'RESOURCES', 'SUBMITTED', 'STARTED', 'TOT. DURATION',
        'JOB DURATION', '#RECOVERS', 'STATUS'
    ])
    for job in jobs:
        job_duration = log_utils.readable_time_duration(
            job['last_recovered_at'] - job['job_duration'],
            job['end_at'],
            absolute=True)
        if job['status'] == spot_status.SpotStatus.RECOVERING:
            # When job is recovering, the duration is exact job['job_duration']
            job_duration = log_utils.readable_time_duration(0,
                                                            job['job_duration'],
                                                            absolute=True)

        job_table.add_row([
            job['job_id'],
            job['job_name'],
            job['resources'],
            log_utils.readable_time_duration(job['submitted_at']),
            log_utils.readable_time_duration(job['start_at']),
            log_utils.readable_time_duration(job['submitted_at'],
                                             job['end_at'],
                                             absolute=True),
            job_duration,
            job['recovery_count'],
            job['status'].value,
        ])
    return str(job_table)


class SpotCodeGen:
    """Code generator for managed spot job utility functions.

    Usage:

      >> codegen = SpotCodegen().show_jobs(...)
    """
    _PREFIX = ['from sky.spot import spot_status']

    def __init__(self):
        self._code = []

    def show_jobs(self) -> str:
        self._code += [
            'job_table = spot_status.show_jobs()', 'print(job_table)'
        ]
        return self._build()

    def cancel_jobs_by_id(self, job_ids: List[int]) -> str:
        self._code += [
            f'result = cancel_jobs_by_id({job_ids})'
            'print(result)'
        ]
        return self._build()

    def cancel_job_by_name(self, job_name: str) -> str:
        self._code += [
            f'result = cancel_job_by_name({job_name!r})', 'print(result)'
        ]
        return self._build()

    def _build(self):
        code = self._PREFIX + self._code
        code = '; '.join(code)
        return f'python3 -u -c {shlex.quote(code)}'
