"""User interfaces with managed spot jobs."""
import collections
import enum
import json
import os
import pathlib
import shlex
import time
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

import colorama
import filelock
from typing_extensions import Literal

from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.skylet import constants
from sky.skylet import job_lib
from sky.skylet.log_lib import run_bash_command_with_log
from sky.spot import constants as spot_constants
from sky.spot import spot_state
from sky.utils import common_utils
from sky.utils import log_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils

if typing.TYPE_CHECKING:
    import sky
    from sky import dag as dag_lib

logger = sky_logging.init_logger(__name__)

# Add user hash so that two users don't have the same controller VM on
# shared-account clouds such as GCP.
SPOT_CONTROLLER_NAME: str = (
    f'sky-spot-controller-{common_utils.get_user_hash()}')
SIGNAL_FILE_PREFIX = '/tmp/sky_spot_controller_signal_{}'
# Controller checks its job's status every this many seconds.
JOB_STATUS_CHECK_GAP_SECONDS = 20

# Controller checks if its job has started every this many seconds.
JOB_STARTED_STATUS_CHECK_GAP_SECONDS = 5

_SPOT_STATUS_CACHE = '~/.sky/spot_status_cache.txt'

_LOG_STREAM_CHECK_CONTROLLER_GAP_SECONDS = 5

_JOB_WAITING_STATUS_MESSAGE = ('[bold cyan]Waiting for the task to start'
                               '{status_str}.[/] It may take a few minutes.')
_JOB_CANCELLED_MESSAGE = (
    '[bold cyan]Waiting for the task status to be updated.'
    '[/] It may take a minute.')

# The maximum time to wait for the spot job status to transition to terminal
# state, after the job finished. This is a safeguard to avoid the case where
# the spot job status fails to be updated and keep the `sky spot logs` blocking
# for a long time.
_FINAL_SPOT_STATUS_WAIT_TIMEOUT_SECONDS = 20


class UserSignal(enum.Enum):
    """The signal to be sent to the user."""
    CANCEL = 'CANCEL'
    # NOTE: We can have more communication signals here if needed
    # in the future.


# ====== internal functions ======
def get_job_status(backend: 'backends.CloudVmRayBackend',
                   cluster_name: str) -> Optional['job_lib.JobStatus']:
    """Check the status of the job running on the spot cluster.

    It can be None, INIT, RUNNING, SUCCEEDED, FAILED, FAILED_SETUP or CANCELLED.
    """
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    assert isinstance(handle, backends.CloudVmRayResourceHandle), handle
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
        if controller_status is None or controller_status.is_terminal():
            logger.error(f'Controller for job {job_id_} has exited abnormally. '
                         'Setting the job status to FAILED_CONTROLLER.')
            tasks = spot_state.get_spot_jobs(job_id_)
            for task in tasks:
                task_name = task['job_name']
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
            # The `set_failed` will only update the task's status if the
            # status is non-terminal.
            spot_state.set_failed(
                job_id_,
                task_id=None,
                failure_type=spot_state.SpotStatus.FAILED_CONTROLLER,
                failure_reason=
                'Controller process has exited abnormally. For more details,'
                f' run: sky spot logs --controller {job_id_}')


def get_job_timestamp(backend: 'backends.CloudVmRayBackend', cluster_name: str,
                      get_end_time: bool) -> float:
    """Get the submitted/ended time of the job."""
    code = job_lib.JobLibCodeGen.get_job_submitted_or_ended_timestamp_payload(
        job_id=None, get_ended_time=get_end_time)
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    returncode, stdout, stderr = backend.run_on_head(handle,
                                                     code,
                                                     stream_logs=False,
                                                     require_outputs=True)
    subprocess_utils.handle_returncode(returncode, code,
                                       'Failed to get job time.',
                                       stdout + stderr)
    stdout = common_utils.decode_payload(stdout)
    return float(stdout)


def event_callback_func(job_id: int, task_id: int, task: 'sky.Task'):
    """Run event callback for the task."""

    def callback_func(state: str):
        event_callback = task.event_callback if task else None
        if event_callback is None or task is None:
            return
        event_callback = event_callback.strip()
        cluster_name = generate_spot_cluster_name(task.name,
                                                  job_id) if task.name else None
        logger.info(f'=== START: event callback for {state!r} ===')
        log_path = os.path.join(constants.SKY_LOGS_DIRECTORY, 'spot_event',
                                f'spot-callback-{job_id}-{task_id}.log')
        result = run_bash_command_with_log(
            bash_command=event_callback,
            log_path=log_path,
            env_vars=dict(
                SKYPILOT_TASK_ID=str(
                    task.envs.get(constants.TASK_ID_ENV_VAR, 'N.A.')),
                SKYPILOT_TASK_IDS=str(
                    task.envs.get(constants.TASK_ID_LIST_ENV_VAR, 'N.A.')),
                TASK_ID=str(task_id),
                JOB_ID=str(job_id),
                JOB_STATUS=state,
                CLUSTER_NAME=cluster_name or '',
                TASK_NAME=task.name or '',
                # TODO(MaoZiming): Future event type Job or Spot.
                EVENT_TYPE='Spot'))
        logger.info(
            f'Bash:{event_callback},log_path:{log_path},result:{result}')
        logger.info(f'=== END: event callback for {state!r} ===')

    return callback_func


# ======== user functions ========


def generate_spot_cluster_name(task_name: str, job_id: int) -> str:
    """Generate spot cluster name."""
    # Truncate the task name to 30 chars to avoid the cluster name being too
    # long after appending the job id, which will cause another truncation in
    # the underlying sky.launch, hiding the `job_id` in the cluster name.
    cluster_name = common_utils.make_cluster_name_on_cloud(
        task_name,
        spot_constants.SPOT_CLUSTER_NAME_PREFIX_LENGTH,
        add_user_hash=False)
    return f'{cluster_name}-{job_id}'


def cancel_jobs_by_id(job_ids: Optional[List[int]]) -> str:
    """Cancel jobs by id.

    If job_ids is None, cancel all jobs.
    """
    if job_ids is None:
        job_ids = spot_state.get_nonterminal_job_ids_by_name(None)
    job_ids = list(set(job_ids))
    if len(job_ids) == 0:
        return 'No job to cancel.'
    job_id_str = ', '.join(map(str, job_ids))
    logger.info(f'Cancelling jobs {job_id_str}.')
    cancelled_job_ids = []
    for job_id in job_ids:
        # Check the status of the managed spot job status. If it is in
        # terminal state, we can safely skip it.
        job_status = spot_state.get_status(job_id)
        if job_status is None:
            logger.info(f'Job {job_id} not found. Skipped.')
            continue
        elif job_status.is_terminal():
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
            with signal_file.open('w', encoding='utf-8') as f:
                f.write(UserSignal.CANCEL.value)
                f.flush()
        cancelled_job_ids.append(job_id)

    if len(cancelled_job_ids) == 0:
        return 'No job to cancel.'
    identity_str = f'Job with ID {cancelled_job_ids[0]} is'
    if len(cancelled_job_ids) > 1:
        cancelled_job_ids_str = ', '.join(map(str, cancelled_job_ids))
        identity_str = f'Jobs with IDs {cancelled_job_ids_str} are'

    return f'{identity_str} scheduled to be cancelled.'


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
    return f'Job {job_name!r} is scheduled to be cancelled.'


def stream_logs_by_id(job_id: int, follow: bool = True) -> str:
    """Stream logs by job id."""
    controller_status = job_lib.get_status(job_id)
    status_msg = ('[bold cyan]Waiting for controller process to be RUNNING'
                  '{status_str}[/].')
    status_display = rich_utils.safe_status(status_msg.format(status_str=''))
    num_tasks = spot_state.get_num_tasks(job_id)

    with status_display:
        prev_msg = None
        while (controller_status != job_lib.JobStatus.RUNNING and
               (controller_status is None or
                not controller_status.is_terminal())):
            status_str = 'None'
            if controller_status is not None:
                status_str = controller_status.value
            msg = status_msg.format(status_str=f' (status: {status_str})')
            if msg != prev_msg:
                status_display.update(msg)
                prev_msg = msg
            time.sleep(_LOG_STREAM_CHECK_CONTROLLER_GAP_SECONDS)
            controller_status = job_lib.get_status(job_id)

        msg = _JOB_WAITING_STATUS_MESSAGE.format(status_str='')
        status_display.update(msg)
        prev_msg = msg
        spot_job_status = spot_state.get_status(job_id)
        while spot_job_status is None:
            time.sleep(1)
            spot_job_status = spot_state.get_status(job_id)

        if spot_job_status.is_terminal():
            job_msg = ''
            if spot_job_status.is_failed():
                job_msg = (
                    f'\nFailure reason: {spot_state.get_failure_reason(job_id)}'
                )
            return (f'{colorama.Fore.YELLOW}'
                    f'Job {job_id} is already in terminal state '
                    f'{spot_job_status.value}. Logs will not be shown.'
                    f'{colorama.Style.RESET_ALL}{job_msg}')
        backend = backends.CloudVmRayBackend()
        task_id, spot_status = spot_state.get_latest_task_id_status(job_id)

        # task_id and spot_status can be None if the controller process just
        # started and the spot status has not set to PENDING yet.
        while spot_status is None or not spot_status.is_terminal():
            handle = None
            if task_id is not None:
                task_name = spot_state.get_task_name(job_id, task_id)
                cluster_name = generate_spot_cluster_name(task_name, job_id)
                handle = global_user_state.get_handle_from_cluster_name(
                    cluster_name)

            # Check the handle: The cluster can be preempted and removed from
            # the table before the spot state is updated by the controller. In
            # this case, we should skip the logging, and wait for the next
            # round of status check.
            if handle is None or spot_status != spot_state.SpotStatus.RUNNING:
                status_str = ''
                if (spot_status is not None and
                        spot_status != spot_state.SpotStatus.RUNNING):
                    status_str = f' (status: {spot_status.value})'
                logger.debug(
                    f'INFO: The log is not ready yet{status_str}. '
                    f'Waiting for {JOB_STATUS_CHECK_GAP_SECONDS} seconds.')
                msg = _JOB_WAITING_STATUS_MESSAGE.format(status_str=status_str)
                if msg != prev_msg:
                    status_display.update(msg)
                    prev_msg = msg
                time.sleep(JOB_STATUS_CHECK_GAP_SECONDS)
                task_id, spot_status = (
                    spot_state.get_latest_task_id_status(job_id))
                continue
            assert spot_status is not None
            assert isinstance(handle, backends.CloudVmRayResourceHandle), handle
            status_display.stop()
            returncode = backend.tail_logs(handle,
                                           job_id=None,
                                           spot_job_id=job_id,
                                           follow=follow)
            if returncode == 0:
                # If the log tailing exit successfully (the real job can be
                # SUCCEEDED or FAILED), we can safely break the loop. We use the
                # status in job queue to show the information, as the spot_state
                # is not updated yet.
                job_statuses = backend.get_job_status(handle, stream_logs=False)
                job_status = list(job_statuses.values())[0]
                assert job_status is not None, 'No job found.'
                if job_status != job_lib.JobStatus.CANCELLED:
                    assert task_id is not None, job_id
                    if task_id < num_tasks - 1 and follow:
                        # The log for the current job is finished. We need to
                        # wait until next job to be started.
                        logger.debug(
                            f'INFO: Log for the current task ({task_id}) '
                            'is finished. Waiting for the next task\'s log '
                            'to be started.')
                        status_display.update('Waiting for the next task: '
                                              f'{task_id + 1}.')
                        status_display.start()
                        original_task_id = task_id
                        while True:
                            task_id, spot_status = (
                                spot_state.get_latest_task_id_status(job_id))
                            if original_task_id != task_id:
                                break
                            time.sleep(JOB_STATUS_CHECK_GAP_SECONDS)
                        continue
                    else:
                        break
                # The job can be cancelled by the user or the controller (when
                # the cluster is partially preempted).
                logger.debug(
                    'INFO: Job is cancelled. Waiting for the status update in '
                    f'{JOB_STATUS_CHECK_GAP_SECONDS} seconds.')
            else:
                logger.debug(
                    f'INFO: (Log streaming) Got return code {returncode}. '
                    f'Retrying in {JOB_STATUS_CHECK_GAP_SECONDS} seconds.')
            # Finish early if the spot status is already in terminal state.
            spot_status = spot_state.get_status(job_id)
            assert spot_status is not None, job_id
            if spot_status.is_terminal():
                break
            logger.info(f'{colorama.Fore.YELLOW}The job is preempted.'
                        f'{colorama.Style.RESET_ALL}')
            msg = _JOB_CANCELLED_MESSAGE
            status_display.update(msg)
            prev_msg = msg
            status_display.start()
            # If the tailing fails, it is likely that the cluster fails, so we
            # wait a while to make sure the spot state is updated by the
            # controller, and check the spot queue again.
            # Wait a bit longer than the controller, so as to make sure the
            # spot state is updated.
            time.sleep(3 * JOB_STATUS_CHECK_GAP_SECONDS)
            spot_status = spot_state.get_status(job_id)

    # The spot_status may not be in terminal status yet, since the controllerhas
    # not updated the spot state yet. We wait for a while, until the spot state
    # is updated.
    wait_seconds = 0
    spot_status = spot_state.get_status(job_id)
    assert spot_status is not None, job_id
    while (not spot_status.is_terminal() and follow and
           wait_seconds < _FINAL_SPOT_STATUS_WAIT_TIMEOUT_SECONDS):
        time.sleep(1)
        wait_seconds += 1
        spot_status = spot_state.get_status(job_id)
        assert spot_status is not None, job_id

    logger.info(f'Logs finished for job {job_id} '
                f'(status: {spot_status.value}).')
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

        job_submitted_at = job['last_recovered_at'] - job['job_duration']
        if job['status'] == spot_state.SpotStatus.RECOVERING:
            # When job is recovering, the duration is exact job['job_duration']
            job_duration = job['job_duration']
        elif job_submitted_at > 0:
            job_duration = end_at - job_submitted_at
        else:
            # When job_start_at <= 0, that means the last_recovered_at is not
            # set yet, i.e. the job is not started.
            job_duration = 0
        job['job_duration'] = job_duration
        job['status'] = job['status'].value

        cluster_name = generate_spot_cluster_name(job['task_name'],
                                                  job['job_id'])
        handle = global_user_state.get_handle_from_cluster_name(cluster_name)
        if handle is not None:
            assert isinstance(handle, backends.CloudVmRayResourceHandle)
            job['cluster_resources'] = (
                f'{handle.launched_nodes}x {handle.launched_resources}')
            job['region'] = handle.launched_resources.region
        else:
            # FIXME(zongheng): display the last cached values for these.
            job['cluster_resources'] = '-'
            job['region'] = '-'

    return common_utils.encode_payload(jobs)


def load_spot_job_queue(payload: str) -> List[Dict[str, Any]]:
    """Load job queue from json string."""
    jobs = common_utils.decode_payload(payload)
    for job in jobs:
        job['status'] = spot_state.SpotStatus(job['status'])
    return jobs


@typing.overload
def format_job_table(tasks: List[Dict[str, Any]],
                     show_all: bool,
                     return_rows: Literal[False] = False,
                     max_jobs: Optional[int] = None) -> str:
    ...


@typing.overload
def format_job_table(tasks: List[Dict[str, Any]],
                     show_all: bool,
                     return_rows: Literal[True],
                     max_jobs: Optional[int] = None) -> List[List[str]]:
    ...


def format_job_table(
        tasks: List[Dict[str, Any]],
        show_all: bool,
        return_rows: bool = False,
        max_jobs: Optional[int] = None) -> Union[str, List[List[str]]]:
    """Returns spot jobs as a formatted string.

    Args:
        jobs: A list of spot jobs.
        show_all: Whether to show all columns.
        max_jobs: The maximum number of jobs to show in the table.
        return_rows: If True, return the rows as a list of strings instead of
          all rows concatenated into a single string.

    Returns: A formatted string of spot jobs, if not `return_rows`; otherwise a
      list of "rows" (each of which is a list of str).
    """
    columns = [
        'ID', 'TASK', 'NAME', 'RESOURCES', 'SUBMITTED', 'TOT. DURATION',
        'JOB DURATION', '#RECOVERIES', 'STATUS'
    ]
    if show_all:
        columns += ['STARTED', 'CLUSTER', 'REGION', 'FAILURE']
    job_table = log_utils.create_table(columns)

    status_counts: Dict[str, int] = collections.defaultdict(int)
    for task in tasks:
        if not task['status'].is_terminal():
            status_counts[task['status'].value] += 1

    all_tasks = tasks
    if max_jobs is not None:
        all_tasks = tasks[:max_jobs]
    jobs = collections.defaultdict(list)
    for task in all_tasks:
        # The tasks within the same job_id are already sorted
        # by the task_id.
        jobs[task['job_id']].append(task)

    for job_id, job_tasks in jobs.items():
        if len(job_tasks) > 1:
            # Aggregate the tasks into a new row in the table.
            job_name = job_tasks[0]['job_name']
            job_duration = 0
            submitted_at = None
            end_at: Optional[int] = 0
            recovery_cnt = 0
            spot_status = spot_state.SpotStatus.SUCCEEDED
            failure_reason = None
            current_task_id = len(job_tasks) - 1
            for task in job_tasks:
                job_duration += task['job_duration']
                if task['submitted_at'] is not None:
                    if (submitted_at is None or
                            submitted_at > task['submitted_at']):
                        submitted_at = task['submitted_at']
                if task['end_at'] is not None:
                    if end_at is not None and end_at < task['end_at']:
                        end_at = task['end_at']
                else:
                    end_at = None
                recovery_cnt += task['recovery_count']
                if spot_status == spot_state.SpotStatus.SUCCEEDED:
                    # Use the first non-succeeded status.
                    # TODO(zhwu): we should not blindly use the first non-
                    # succeeded as the status could be changed to SUBMITTED
                    # when going from one task to the next one, which can be
                    # confusing.
                    spot_status = task['status']
                    current_task_id = task['task_id']

                if (failure_reason is None and
                        task['status'] > spot_state.SpotStatus.SUCCEEDED):
                    failure_reason = task['failure_reason']

            job_duration = log_utils.readable_time_duration(0,
                                                            job_duration,
                                                            absolute=True)
            submitted = log_utils.readable_time_duration(submitted_at)
            total_duration = log_utils.readable_time_duration(submitted_at,
                                                              end_at,
                                                              absolute=True)

            status_str = spot_status.colored_str()
            if (spot_status < spot_state.SpotStatus.RUNNING and
                    current_task_id > 0):
                status_str += f' (task: {current_task_id})'

            job_values = [
                job_id,
                '',
                job_name,
                '-',
                submitted,
                total_duration,
                job_duration,
                recovery_cnt,
                status_str,
            ]
            if show_all:
                job_values.extend([
                    '-',
                    '-',
                    '-',
                    failure_reason if failure_reason is not None else '-',
                ])
            job_table.add_row(job_values)

        for task in job_tasks:
            # The job['job_duration'] is already calculated in
            # dump_spot_job_queue().
            job_duration = log_utils.readable_time_duration(
                0, task['job_duration'], absolute=True)
            submitted = log_utils.readable_time_duration(task['submitted_at'])
            values = [
                task['job_id'] if len(job_tasks) == 1 else ' \u21B3',
                task['task_id'] if len(job_tasks) > 1 else '-',
                task['task_name'],
                task['resources'],
                # SUBMITTED
                submitted if submitted != '-' else submitted,
                # TOT. DURATION
                log_utils.readable_time_duration(task['submitted_at'],
                                                 task['end_at'],
                                                 absolute=True),
                job_duration,
                task['recovery_count'],
                task['status'].colored_str(),
            ]
            if show_all:
                values.extend([
                    # STARTED
                    log_utils.readable_time_duration(task['start_at']),
                    task['cluster_resources'],
                    task['region'],
                    task['failure_reason']
                    if task['failure_reason'] is not None else '-',
                ])
            job_table.add_row(values)

        if len(job_tasks) > 1:
            # Add a row to separate the aggregated job from the next job.
            job_table.add_row([''] * len(columns))
    status_str = ', '.join([
        f'{count} {status}' for status, count in sorted(status_counts.items())
    ])
    if status_str:
        status_str = f'In progress tasks: {status_str}'
    else:
        status_str = 'No in-progress spot jobs.'
    output = status_str
    if str(job_table):
        output += f'\n{job_table}'
    if return_rows:
        return job_table.rows
    return output


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
    def set_pending(cls, job_id: int, spot_dag: 'dag_lib.Dag') -> str:
        dag_name = spot_dag.name
        # Add the spot job to spot queue table.
        code = [
            f'spot_state.set_job_name('
            f'{job_id}, {dag_name!r})',
        ]
        for task_id, task in enumerate(spot_dag.tasks):
            resources_str = backend_utils.get_task_resources_str(task)
            code += [
                f'spot_state.set_pending('
                f'{job_id}, {task_id}, {task.name!r}, '
                f'{resources_str!r})',
            ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        generated_code = '; '.join(code)
        return f'python3 -u -c {shlex.quote(generated_code)}'


def dump_job_table_cache(job_table: str):
    """Dump job table cache to file."""
    cache_file = pathlib.Path(_SPOT_STATUS_CACHE).expanduser()
    with cache_file.open('w', encoding='utf-8') as f:
        json.dump((time.time(), job_table), f)


def load_job_table_cache() -> Optional[Tuple[float, str]]:
    """Load job table cache from file.

    Returns:
        A tuple of (timestamp, job_table), where the timestamp is
        the time when the job table is dumped and the job_table is
        the dumped job table in string.
        None if the cache file does not exist.
    """
    cache_file = pathlib.Path(_SPOT_STATUS_CACHE).expanduser()
    if not cache_file.exists():
        return None
    with cache_file.open('r', encoding='utf-8') as f:
        return json.load(f)
