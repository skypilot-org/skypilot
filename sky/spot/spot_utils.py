"""User interfaces with managed spot jobs."""

import enum
import json
import pathlib
import shlex
import time
from typing import List, Tuple

import colorama
import filelock

from sky.skylet.utils import log_utils
from sky.spot import spot_state

SIGNAL_FILE_PREFIX = '/tmp/sky_spot_controller_signal_{}'
JOB_STATUS_CHECK_GAP_SECONDS = 60

_SPOT_STATUS_CACHE = '~/.sky/spot_status_cache.txt'


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
        # Filelock is needed to prevent race condition between signal
        # check/removal and signal writing.
        # TODO(mraheja): remove pylint disabling when filelock version updated
        # pylint: disable=abstract-class-instantiated
        with filelock.FileLock(str(signal_file) + '.lock'):
            with signal_file.open('w') as f:
                f.write(UserSignal.CANCEL.value)
                f.flush()

    identity_str = f'job ID {job_ids[0]} is'
    if len(job_ids) > 1:
        identity_str = f'job IDs {job_ids} are'

    return (f'Jobs with {identity_str} scheduled to be cancelled within '
            f'{JOB_STATUS_CHECK_GAP_SECONDS} seconds.')


def cancel_job_by_name(job_name: str) -> str:
    """Cancel a job by name."""
    job_ids = spot_state.get_nonterminal_job_ids_by_name(job_name)
    if len(job_ids) == 0:
        return (f'{colorama.Fore.RED}No job found with name {job_name!r}.'
                f'{colorama.Style.RESET_ALL}')
    if len(job_ids) > 1:
        return (f'{colorama.Fore.RED}Multiple running jobs found '
                f'with name {job_name!r}.\n'
                f'Job IDs: {job_ids}{colorama.Style.RESET_ALL}')
    cancel_jobs_by_id(job_ids)
    return (f'Job {job_name!r} is scheduled to be cancelled within '
            f'{JOB_STATUS_CHECK_GAP_SECONDS} seconds.')


def show_jobs() -> str:
    """Show all spot jobs."""
    jobs = spot_state.get_spot_jobs()

    job_table = log_utils.create_table([
        'ID', 'NAME', 'RESOURCES', 'SUBMITTED', 'STARTED', 'TOT. DURATION',
        'JOB DURATION', '#RECOVERIES', 'STATUS'
    ])
    for job in jobs:
        job_duration = log_utils.readable_time_duration(
            job['last_recovered_at'] - job['job_duration'],
            job['end_at'],
            absolute=True)
        if job['status'] == spot_state.SpotStatus.RECOVERING:
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
    _PREFIX = ['from sky.spot import spot_utils']

    def __init__(self):
        self._code = []

    def show_jobs(self) -> str:
        self._code += [
            'job_table = spot_utils.show_jobs()',
            'print(job_table)',
        ]
        return self._build()

    def cancel_jobs_by_id(self, job_ids: List[int]) -> str:
        self._code += [
            f'result = spot_utils.cancel_jobs_by_id({job_ids})',
            'print(result, end="", flush=True)',
        ]
        return self._build()

    def cancel_job_by_name(self, job_name: str) -> str:
        self._code += [
            f'result = spot_utils.cancel_job_by_name({job_name!r})',
            'print(result, end="", flush=True)',
        ]
        return self._build()

    def _build(self):
        code = self._PREFIX + self._code
        code = '; '.join(code)
        return f'python3 -u -c {shlex.quote(code)}'


def dump_job_table_cache(job_table):
    """Dump job table cache to file."""
    cache_file = pathlib.Path(_SPOT_STATUS_CACHE).expanduser()
    with cache_file.open('w') as f:
        json.dump((time.time(), job_table), f)


def load_job_table_cache() -> Tuple[str, str]:
    """Load job table cache from file."""
    cache_file = pathlib.Path(_SPOT_STATUS_CACHE).expanduser()
    if not cache_file.exists():
        return None
    with cache_file.open('r') as f:
        return json.load(f)
