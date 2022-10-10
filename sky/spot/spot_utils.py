"""User interfaces with managed spot jobs."""
import collections
import enum
import json
import pathlib
import shlex
import time
from typing import Any, Dict, List, Optional, Tuple

import colorama
import filelock

from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.skylet import job_lib
from sky.utils import common_utils
from sky.utils import log_utils
from sky.spot import spot_state
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)

# Add user hash so that two users don't have the same controller VM on
# shared-account clouds such as GCP.
SPOT_CONTROLLER_NAME = f'sky-spot-controller-{common_utils.get_user_hash()}'
SIGNAL_FILE_PREFIX = '/tmp/sky_spot_controller_signal_{}'
# Controller checks its job's status every this many seconds.
JOB_STATUS_CHECK_GAP_SECONDS = 20

# Controller checks if its job has started every this many seconds.
JOB_STARTED_STATUS_CHECK_GAP_SECONDS = 5

_SPOT_STATUS_CACHE = '~/.sky/spot_status_cache.txt'

_LOG_STREAM_CHECK_CONTROLLER_GAP_SECONDS = 5


class UserSignal(enum.Enum):
    """The signal to be sent to the user."""
    CANCEL = 'CANCEL'
    # NOTE: We can have more communication signals here if needed
    # in the future.


# ====== internal functions ======
def get_job_status(backend: 'backends.CloudVmRayBackend',
                   cluster_name: str) -> Optional['job_lib.JobStatus']:
    """Check the status of the job running on the spot cluster.

    It can be None, INIT, RUNNING, SUCCEEDED, FAILED or CANCELLED.
    """
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    status = None
    try:
        logger.info('=== Checking the job status... ===')
        statuses = backend.get_job_status(handle, stream_logs=False)
        status = list(statuses.values())[0]
        if status is None:
            logger.info('No job found.')
        else:
            logger.info(f'Job status: {status}')
    except exceptions.CommandError:
        logger.info('Failed to connect to the cluster.')
    logger.info('=' * 34)
    return status


def update_spot_job_status(job_id: Optional[int] = None):
    """Update spot job status if the controller job failed abnormally.

    Check the status of the controller job. If it is not running, it must have
    exited abnormally, and we should set the job status to FAILED_CONTROLLER.
    `end_at` will be set to the current timestamp for the job when above
    happens, which could be not accurate based on the frequency this function
    is called.
    """
    if job_id is None:
        job_ids = spot_state.get_nonterminal_job_ids_by_name(None)
    else:
        job_ids = [job_id]
    for job_id_ in job_ids:
        controller_status = job_lib.get_status(job_id_)
        if controller_status.is_terminal():
            logger.error(f'Controller for job {job_id_} has exited abnormally. '
                         'Setting the job status to FAILED_CONTROLLER.')
            task_name = spot_state.get_task_name_by_job_id(job_id_)

            # Tear down the abnormal spot cluster to avoid resource leakage.
            cluster_name = generate_spot_cluster_name(task_name, job_id_)
            handle = global_user_state.get_handle_from_cluster_name(
                cluster_name)
            if handle is not None:
                backend = backend_utils.get_backend_from_handle(handle)
                max_retry = 3
                for retry_cnt in range(max_retry):
                    try:
                        backend.teardown(handle, terminate=True)
                        break
                    except RuntimeError:
                        logger.error('Failed to tear down the spot cluster '
                                     f'{cluster_name!r}. Retrying '
                                     f'[{retry_cnt}/{max_retry}].')

            # The controller job for this spot job is not running: it must
            # have exited abnormally, and we should set the job status to
            # FAILED_CONTROLLER.
            spot_state.set_failed(job_id_,
                                  spot_state.SpotStatus.FAILED_CONTROLLER)


def get_job_timestamp(backend: 'backends.CloudVmRayBackend', cluster_name: str,
                      get_end_time: bool) -> float:
    """Get the started/ended time of the job."""
    code = job_lib.JobLibCodeGen.get_job_time(job_id=None, is_end=get_end_time)
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    returncode, stdout, stderr = backend.run_on_head(handle,
                                                     code,
                                                     stream_logs=False,
                                                     require_outputs=True)
    subprocess_utils.handle_returncode(returncode, code,
                                       'Failed to get job time.',
                                       stdout + stderr)
    return float(stdout)


# ======== user functions ========


def generate_spot_cluster_name(task_name: str, job_id: int) -> str:
    """Generate spot cluster name."""
    return f'{task_name}-{job_id}'


def cancel_jobs_by_id(job_ids: Optional[List[int]]) -> str:
    """Cancel jobs by id.

    If job_ids is None, cancel all jobs.
    """
    if job_ids is None:
        job_ids = spot_state.get_nonterminal_job_ids_by_name(None)
    if len(job_ids) == 0:
        return 'No job to cancel.'
    job_id_str = ', '.join(map(str, job_ids))
    logger.info(f'Cancelling jobs {job_id_str}.')
    cancelled_job_ids = []
    for job_id in job_ids:
        # Check the status of the managed spot job status. If it is in
        # terminal state, we can safely skip it.
        job_status = spot_state.get_status(job_id)
        if job_status.is_terminal():
            logger.info(f'Job {job_id} is already in terminal state '
                        f'{job_status.value}. Skipped.')
            continue

        update_spot_job_status(job_id)

        # Send the signal to the spot job controller.
        signal_file = pathlib.Path(SIGNAL_FILE_PREFIX.format(job_id))
        # Filelock is needed to prevent race condition between signal
        # check/removal and signal writing.
        # TODO(mraheja): remove pylint disabling when filelock version updated
        # pylint: disable=abstract-class-instantiated
        with filelock.FileLock(str(signal_file) + '.lock'):
            with signal_file.open('w') as f:
                f.write(UserSignal.CANCEL.value)
                f.flush()
        cancelled_job_ids.append(job_id)

    if len(cancelled_job_ids) == 0:
        return 'No job to cancel.'
    identity_str = f'Job with ID {cancelled_job_ids[0]} is'
    if len(cancelled_job_ids) > 1:
        cancelled_job_ids_str = ', '.join(map(str, cancelled_job_ids))
        identity_str = f'Jobs with IDs {cancelled_job_ids_str} are'

    return (f'{identity_str} scheduled to be cancelled within '
            f'{JOB_STATUS_CHECK_GAP_SECONDS} seconds.')


def cancel_job_by_name(job_name: str) -> str:
    """Cancel a job by name."""
    job_ids = spot_state.get_nonterminal_job_ids_by_name(job_name)
    if len(job_ids) == 0:
        return f'No running job found with name {job_name!r}.'
    if len(job_ids) > 1:
        return (f'{colorama.Fore.RED}Multiple running jobs found '
                f'with name {job_name!r}.\n'
                f'Job IDs: {job_ids}{colorama.Style.RESET_ALL}')
    cancel_jobs_by_id(job_ids)
    return (f'Job {job_name!r} is scheduled to be cancelled within '
            f'{JOB_STATUS_CHECK_GAP_SECONDS} seconds.')


def stream_logs_by_id(job_id: int, follow: bool = True) -> str:
    """Stream logs by job id."""
    controller_status = job_lib.get_status(job_id)
    while (controller_status != job_lib.JobStatus.RUNNING and
           (controller_status is None or not controller_status.is_terminal())):
        status_str = 'None'
        if controller_status is not None:
            status_str = controller_status.value
        logger.info(
            'Waiting for the spot controller process to be RUNNING (status: '
            f'{status_str}).')
        time.sleep(_LOG_STREAM_CHECK_CONTROLLER_GAP_SECONDS)
        controller_status = job_lib.get_status(job_id)

    job_status = spot_state.get_status(job_id)
    while job_status is None:
        logger.info('Waiting for the spot job to be started.')
        time.sleep(1)
        job_status = spot_state.get_status(job_id)

    if job_status.is_terminal():
        job_msg = ''
        if job_status.is_failed():
            job_msg = ('\nFor detailed error message, please check: '
                       f'{colorama.Style.BRIGHT}sky logs '
                       f'{SPOT_CONTROLLER_NAME} {job_id}'
                       f'{colorama.Style.RESET_ALL}')
        return (
            f'Job {job_id} is already in terminal state {job_status.value}. '
            f'Logs will not be shown.{job_msg}')
    task_name = spot_state.get_task_name_by_job_id(job_id)
    cluster_name = generate_spot_cluster_name(task_name, job_id)
    backend = backends.CloudVmRayBackend()
    spot_status = spot_state.get_status(job_id)
    while not spot_status.is_terminal():
        if spot_status != spot_state.SpotStatus.RUNNING:
            logger.info(f'INFO: The log is not ready yet, as the spot job '
                        f'is {spot_status.value}. '
                        f'Waiting for {JOB_STATUS_CHECK_GAP_SECONDS} seconds.')
            time.sleep(JOB_STATUS_CHECK_GAP_SECONDS)
            spot_status = spot_state.get_status(job_id)
            continue
        handle = global_user_state.get_handle_from_cluster_name(cluster_name)
        returncode = backend.tail_logs(handle,
                                       job_id=None,
                                       spot_job_id=job_id,
                                       follow=follow)
        if returncode == 0:
            # If the log tailing exit successfully (the real job can be
            # SUCCEEDED or FAILED), we can safely break the loop. We use the
            # status in job queue to show the information, as the spot_state is
            # not updated yet.
            job_statuses = backend.get_job_status(handle, stream_logs=False)
            job_status = list(job_statuses.values())[0]
            assert job_status is not None, 'No job found.'
            logger.info(f'Logs finished for job {job_id} '
                        f'(status: {job_status.value}).')
            break
        logger.info(
            f'INFO: (Log streaming) Got return code {returncode}. Retrying '
            f'in {JOB_STATUS_CHECK_GAP_SECONDS} seconds.')
        # If the tailing fails, it is likely that the cluster fails, so we wait
        # a while to make sure the spot state is updated by the controller, and
        # check the spot status again.
        time.sleep(JOB_STATUS_CHECK_GAP_SECONDS)
        spot_status = spot_state.get_status(job_id)
    else:
        # The spot_status is in terminal state.
        logger.info(f'Logs finished for job {job_id} '
                    f'(status: {spot_state.get_status(job_id).value}).')
    return ''


def stream_logs_by_name(job_name: str, follow: bool = True) -> str:
    """Stream logs by name."""
    job_ids = spot_state.get_nonterminal_job_ids_by_name(job_name)
    if len(job_ids) == 0:
        return (f'{colorama.Fore.RED}No job found with name {job_name!r}.'
                f'{colorama.Style.RESET_ALL}')
    if len(job_ids) > 1:
        return (f'{colorama.Fore.RED}Multiple running jobs found '
                f'with name {job_name!r}.\n'
                f'Job IDs: {job_ids}{colorama.Style.RESET_ALL}')
    stream_logs_by_id(job_ids[0], follow)
    return ''


def dump_spot_job_queue() -> str:
    jobs = spot_state.get_spot_jobs()

    for job in jobs:
        end_at = job['end_at']
        if end_at is None:
            end_at = time.time()

        job_start_at = job['last_recovered_at'] - job['job_duration']
        if job['status'] == spot_state.SpotStatus.RECOVERING:
            # When job is recovering, the duration is exact job['job_duration']
            job_duration = job['job_duration']
        elif job_start_at > 0:
            job_duration = end_at - job_start_at
        else:
            # When job_start_at <= 0, that means the last_recovered_at is not
            # set yet, i.e. the job is not started.
            job_duration = 0
        job['job_duration'] = job_duration
        job['status'] = job['status'].value

        cluster_name = generate_spot_cluster_name(job['job_name'],
                                                  job['job_id'])
        handle = global_user_state.get_handle_from_cluster_name(cluster_name)
        if handle is not None:
            job['cluster_resources'] = (
                f'{handle.launched_nodes}x {handle.launched_resources}')
            job['region'] = handle.launched_resources.region
        else:
            job['cluster_resources'] = '-'
            job['region'] = '-'

    return common_utils.encode_payload(jobs)


def load_spot_job_queue(payload: str) -> List[Dict[str, Any]]:
    """Load job queue from json string."""
    jobs = common_utils.decode_payload(payload)
    for job in jobs:
        job['status'] = spot_state.SpotStatus(job['status'])
    return jobs


def format_job_table(jobs: List[Dict[str, Any]], show_all: bool) -> str:
    """Show all spot jobs."""
    columns = [
        'ID', 'NAME', 'RESOURCES', 'SUBMITTED', 'TOT. DURATION', 'JOB DURATION',
        '#RECOVERIES', 'STATUS'
    ]
    if show_all:
        columns += ['STARTED', 'CLUSTER', 'REGION']
    job_table = log_utils.create_table(columns)

    status_counts = collections.defaultdict(int)
    for job in jobs:
        # The job['job_duration'] is already calculated in
        # dump_spot_job_queue().
        job_duration = log_utils.readable_time_duration(0,
                                                        job['job_duration'],
                                                        absolute=True)
        ago_suffix = ' ago' if show_all else ''
        submitted = log_utils.readable_time_duration(job['submitted_at'],
                                                     absolute=show_all)
        values = [
            job['job_id'],
            job['job_name'],
            job['resources'],
            # SUBMITTED
            submitted + ago_suffix if submitted != '-' else submitted,
            # TOT. DURATION
            log_utils.readable_time_duration(job['submitted_at'],
                                             job['end_at'],
                                             absolute=True),
            job_duration,
            job['recovery_count'],
            job['status'].value,
        ]
        if not job['status'].is_terminal():
            status_counts[job['status'].value] += 1
        if show_all:
            # STARTED
            started = log_utils.readable_time_duration(job['start_at'],
                                                       absolute=True)
            if started != '-':
                started += ago_suffix
            values.append(started)
            values.extend([
                job['cluster_resources'],
                job['region'],
            ])
        job_table.add_row(values)
    status_str = ', '.join([
        f'{count} {status}' for status, count in sorted(status_counts.items())
    ])
    if status_str:
        status_str = f'In progress jobs: {status_str}\n\n'
    return status_str + str(job_table)


class SpotCodeGen:
    """Code generator for managed spot job utility functions.

    Usage:

      >> codegen = SpotCodegen.show_jobs(...)
    """
    _PREFIX = [
        'from sky.spot import spot_state',
        'from sky.spot import spot_utils',
    ]

    @classmethod
    def get_job_table(cls) -> str:
        code = [
            'job_table = spot_utils.dump_spot_job_queue()',
            'print(job_table, flush=True)',
        ]
        return cls._build(code)

    @classmethod
    def cancel_jobs_by_id(cls, job_ids: Optional[List[int]]) -> str:
        code = [
            f'msg = spot_utils.cancel_jobs_by_id({job_ids})',
            'print(msg, end="", flush=True)',
        ]
        return cls._build(code)

    @classmethod
    def cancel_job_by_name(cls, job_name: str) -> str:
        code = [
            f'msg = spot_utils.cancel_job_by_name({job_name!r})',
            'print(msg, end="", flush=True)',
        ]
        return cls._build(code)

    @classmethod
    def stream_logs_by_name(cls, job_name: str, follow: bool = True) -> str:
        code = [
            f'msg = spot_utils.stream_logs_by_name({job_name!r}, '
            f'follow={follow})',
            'print(msg, flush=True)',
        ]
        return cls._build(code)

    @classmethod
    def stream_logs_by_id(cls,
                          job_id: Optional[int],
                          follow: bool = True) -> str:
        code = [
            f'job_id = {job_id} if {job_id} is not None '
            'else spot_state.get_latest_job_id()',
            f'msg = spot_utils.stream_logs_by_id(job_id, follow={follow})',
            'print(msg, flush=True)',
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str] = None) -> str:
        code = cls._PREFIX + code
        code = '; '.join(code)
        return f'python3 -u -c {shlex.quote(code)}'


def dump_job_table_cache(job_table: str):
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
