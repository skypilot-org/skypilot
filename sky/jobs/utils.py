"""User interfaces with managed jobs.

NOTE: whenever an API change is made in this file, we need to bump the
jobs.constants.MANAGED_JOBS_VERSION and handle the API change in the
ManagedJobCodeGen.
"""
import collections
import enum
import inspect
import os
import pathlib
import shlex
import shutil
import textwrap
import time
import typing
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import colorama
import filelock
from typing_extensions import Literal

from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.jobs import constants as managed_job_constants
from sky.jobs import state as managed_job_state
from sky.skylet import constants
from sky.skylet import job_lib
from sky.skylet import log_lib
from sky.utils import common_utils
from sky.utils import log_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import sky
    from sky import dag as dag_lib

logger = sky_logging.init_logger(__name__)

# Add user hash so that two users don't have the same controller VM on
# shared-account clouds such as GCP.
JOB_CONTROLLER_NAME: str = (
    f'sky-jobs-controller-{common_utils.get_user_hash()}')
LEGACY_JOB_CONTROLLER_NAME: str = (
    f'sky-spot-controller-{common_utils.get_user_hash()}')
SIGNAL_FILE_PREFIX = '/tmp/sky_jobs_controller_signal_{}'
LEGACY_SIGNAL_FILE_PREFIX = '/tmp/sky_spot_controller_signal_{}'
# Controller checks its job's status every this many seconds.
JOB_STATUS_CHECK_GAP_SECONDS = 20

# Controller checks if its job has started every this many seconds.
JOB_STARTED_STATUS_CHECK_GAP_SECONDS = 5

_LOG_STREAM_CHECK_CONTROLLER_GAP_SECONDS = 5

_JOB_WAITING_STATUS_MESSAGE = ux_utils.spinner_message(
    'Waiting for task to start[/]'
    '{status_str}. It may take a few minutes.\n'
    '  [dim]View controller logs: sky jobs logs --controller {job_id}')
_JOB_CANCELLED_MESSAGE = (
    ux_utils.spinner_message('Waiting for task status to be updated.') +
    ' It may take a minute.')

# The maximum time to wait for the managed job status to transition to terminal
# state, after the job finished. This is a safeguard to avoid the case where
# the managed job status fails to be updated and keep the `sky jobs logs`
# blocking for a long time.
_FINAL_JOB_STATUS_WAIT_TIMEOUT_SECONDS = 25


class UserSignal(enum.Enum):
    """The signal to be sent to the user."""
    CANCEL = 'CANCEL'
    # NOTE: We can have more communication signals here if needed
    # in the future.


# ====== internal functions ======
def get_job_status(backend: 'backends.CloudVmRayBackend',
                   cluster_name: str) -> Optional['job_lib.JobStatus']:
    """Check the status of the job running on a managed job cluster.

    It can be None, INIT, RUNNING, SUCCEEDED, FAILED, FAILED_DRIVER,
    FAILED_SETUP or CANCELLED.
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


def update_managed_job_status(job_id: Optional[int] = None):
    """Update managed job status if the controller job failed abnormally.

    Check the status of the controller job. If it is not running, it must have
    exited abnormally, and we should set the job status to FAILED_CONTROLLER.
    `end_at` will be set to the current timestamp for the job when above
    happens, which could be not accurate based on the frequency this function
    is called.
    """
    if job_id is None:
        job_ids = managed_job_state.get_nonterminal_job_ids_by_name(None)
    else:
        job_ids = [job_id]
    for job_id_ in job_ids:
        controller_status = job_lib.get_status(job_id_)
        if controller_status is None or controller_status.is_terminal():
            logger.error(f'Controller for job {job_id_} has exited abnormally. '
                         'Setting the job status to FAILED_CONTROLLER.')
            tasks = managed_job_state.get_managed_jobs(job_id_)
            for task in tasks:
                task_name = task['job_name']
                # Tear down the abnormal cluster to avoid resource leakage.
                cluster_name = generate_managed_job_cluster_name(
                    task_name, job_id_)
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
                            logger.error('Failed to tear down the cluster '
                                         f'{cluster_name!r}. Retrying '
                                         f'[{retry_cnt}/{max_retry}].')

            # The controller job for this managed job is not running: it must
            # have exited abnormally, and we should set the job status to
            # FAILED_CONTROLLER.
            # The `set_failed` will only update the task's status if the
            # status is non-terminal.
            managed_job_state.set_failed(
                job_id_,
                task_id=None,
                failure_type=managed_job_state.ManagedJobStatus.
                FAILED_CONTROLLER,
                failure_reason=
                'Controller process has exited abnormally. For more details,'
                f' run: sky jobs logs --controller {job_id_}')


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

    def callback_func(status: str):
        event_callback = task.event_callback if task else None
        if event_callback is None or task is None:
            return
        event_callback = event_callback.strip()
        cluster_name = generate_managed_job_cluster_name(
            task.name, job_id) if task.name else None
        logger.info(f'=== START: event callback for {status!r} ===')
        log_path = os.path.join(constants.SKY_LOGS_DIRECTORY,
                                'managed_job_event',
                                f'jobs-callback-{job_id}-{task_id}.log')
        result = log_lib.run_bash_command_with_log(
            bash_command=event_callback,
            log_path=log_path,
            env_vars=dict(
                SKYPILOT_TASK_ID=str(
                    task.envs.get(constants.TASK_ID_ENV_VAR, 'N.A.')),
                SKYPILOT_TASK_IDS=str(
                    task.envs.get(constants.TASK_ID_LIST_ENV_VAR, 'N.A.')),
                TASK_ID=str(task_id),
                JOB_ID=str(job_id),
                JOB_STATUS=status,
                CLUSTER_NAME=cluster_name or '',
                TASK_NAME=task.name or '',
                # TODO(MaoZiming): Future event type Job or Spot.
                EVENT_TYPE='Spot'))
        logger.info(
            f'Bash:{event_callback},log_path:{log_path},result:{result}')
        logger.info(f'=== END: event callback for {status!r} ===')

    return callback_func


# ======== user functions ========


def generate_managed_job_cluster_name(task_name: str, job_id: int) -> str:
    """Generate managed job cluster name."""
    # Truncate the task name to 30 chars to avoid the cluster name being too
    # long after appending the job id, which will cause another truncation in
    # the underlying sky.launch, hiding the `job_id` in the cluster name.
    cluster_name = common_utils.make_cluster_name_on_cloud(
        task_name,
        managed_job_constants.JOBS_CLUSTER_NAME_PREFIX_LENGTH,
        add_user_hash=False)
    return f'{cluster_name}-{job_id}'


def cancel_jobs_by_id(job_ids: Optional[List[int]]) -> str:
    """Cancel jobs by id.

    If job_ids is None, cancel all jobs.
    """
    if job_ids is None:
        job_ids = managed_job_state.get_nonterminal_job_ids_by_name(None)
    job_ids = list(set(job_ids))
    if len(job_ids) == 0:
        return 'No job to cancel.'
    job_id_str = ', '.join(map(str, job_ids))
    logger.info(f'Cancelling jobs {job_id_str}.')
    cancelled_job_ids = []
    for job_id in job_ids:
        # Check the status of the managed job status. If it is in
        # terminal state, we can safely skip it.
        job_status = managed_job_state.get_status(job_id)
        if job_status is None:
            logger.info(f'Job {job_id} not found. Skipped.')
            continue
        elif job_status.is_terminal():
            logger.info(f'Job {job_id} is already in terminal state '
                        f'{job_status.value}. Skipped.')
            continue

        update_managed_job_status(job_id)

        # Send the signal to the jobs controller.
        signal_file = pathlib.Path(SIGNAL_FILE_PREFIX.format(job_id))
        legacy_signal_file = pathlib.Path(
            LEGACY_SIGNAL_FILE_PREFIX.format(job_id))
        # Filelock is needed to prevent race condition between signal
        # check/removal and signal writing.
        with filelock.FileLock(str(signal_file) + '.lock'):
            with signal_file.open('w', encoding='utf-8') as f:
                f.write(UserSignal.CANCEL.value)
                f.flush()
            # Backward compatibility for managed jobs launched before #3419. It
            # can be removed in the future 0.8.0 release.
            shutil.copy(str(signal_file), str(legacy_signal_file))
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
    job_ids = managed_job_state.get_nonterminal_job_ids_by_name(job_name)
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
    status_msg = ux_utils.spinner_message(
        'Waiting for controller process to be RUNNING') + '{status_str}'
    status_display = rich_utils.safe_status(status_msg.format(status_str=''))
    num_tasks = managed_job_state.get_num_tasks(job_id)

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

        msg = _JOB_WAITING_STATUS_MESSAGE.format(status_str='', job_id=job_id)
        status_display.update(msg)
        prev_msg = msg
        managed_job_status = managed_job_state.get_status(job_id)
        while managed_job_status is None:
            time.sleep(1)
            managed_job_status = managed_job_state.get_status(job_id)

        if managed_job_status.is_terminal():
            job_msg = ''
            if managed_job_status.is_failed():
                job_msg = ('\nFailure reason: '
                           f'{managed_job_state.get_failure_reason(job_id)}')
            return (f'{colorama.Fore.YELLOW}'
                    f'Job {job_id} is already in terminal state '
                    f'{managed_job_status.value}. Logs will not be shown.'
                    f'{colorama.Style.RESET_ALL}{job_msg}')
        backend = backends.CloudVmRayBackend()
        task_id, managed_job_status = (
            managed_job_state.get_latest_task_id_status(job_id))

        # task_id and managed_job_status can be None if the controller process
        # just started and the managed job status has not set to PENDING yet.
        while (managed_job_status is None or
               not managed_job_status.is_terminal()):
            handle = None
            if task_id is not None:
                task_name = managed_job_state.get_task_name(job_id, task_id)
                cluster_name = generate_managed_job_cluster_name(
                    task_name, job_id)
                handle = global_user_state.get_handle_from_cluster_name(
                    cluster_name)

            # Check the handle: The cluster can be preempted and removed from
            # the table before the managed job state is updated by the
            # controller. In this case, we should skip the logging, and wait for
            # the next round of status check.
            if (handle is None or managed_job_status !=
                    managed_job_state.ManagedJobStatus.RUNNING):
                status_str = ''
                if (managed_job_status is not None and managed_job_status !=
                        managed_job_state.ManagedJobStatus.RUNNING):
                    status_str = f' (status: {managed_job_status.value})'
                logger.debug(
                    f'INFO: The log is not ready yet{status_str}. '
                    f'Waiting for {JOB_STATUS_CHECK_GAP_SECONDS} seconds.')
                msg = _JOB_WAITING_STATUS_MESSAGE.format(status_str=status_str,
                                                         job_id=job_id)
                if msg != prev_msg:
                    status_display.update(msg)
                    prev_msg = msg
                time.sleep(JOB_STATUS_CHECK_GAP_SECONDS)
                task_id, managed_job_status = (
                    managed_job_state.get_latest_task_id_status(job_id))
                continue
            assert managed_job_status is not None
            assert isinstance(handle, backends.CloudVmRayResourceHandle), handle
            status_display.stop()
            returncode = backend.tail_logs(handle,
                                           job_id=None,
                                           managed_job_id=job_id,
                                           follow=follow)
            if returncode == 0:
                # If the log tailing exit successfully (the real job can be
                # SUCCEEDED or FAILED), we can safely break the loop. We use the
                # status in job queue to show the information, as the
                # ManagedJobStatus is not updated yet.
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
                        # Add a newline to avoid the status display below
                        # removing the last line of the task output.
                        print()
                        status_display.update(
                            ux_utils.spinner_message(
                                f'Waiting for the next task: {task_id + 1}'))
                        status_display.start()
                        original_task_id = task_id
                        while True:
                            task_id, managed_job_status = (
                                managed_job_state.get_latest_task_id_status(
                                    job_id))
                            if original_task_id != task_id:
                                break
                            time.sleep(JOB_STATUS_CHECK_GAP_SECONDS)
                        continue
                    else:
                        task_specs = managed_job_state.get_task_specs(
                            job_id, task_id)
                        if task_specs.get('max_restarts_on_errors', 0) == 0:
                            # We don't need to wait for the managed job status
                            # update, as the job is guaranteed to be in terminal
                            # state afterwards.
                            break
                        print()
                        status_display.update(
                            ux_utils.spinner_message(
                                'Waiting for next restart for the failed task'))
                        status_display.start()
                        while True:
                            _, managed_job_status = (
                                managed_job_state.get_latest_task_id_status(
                                    job_id))
                            if (managed_job_status !=
                                    managed_job_state.ManagedJobStatus.RUNNING):
                                break
                            time.sleep(JOB_STATUS_CHECK_GAP_SECONDS)
                        continue
                # The job can be cancelled by the user or the controller (when
                # the cluster is partially preempted).
                logger.debug(
                    'INFO: Job is cancelled. Waiting for the status update in '
                    f'{JOB_STATUS_CHECK_GAP_SECONDS} seconds.')
            else:
                logger.debug(
                    f'INFO: (Log streaming) Got return code {returncode}. '
                    f'Retrying in {JOB_STATUS_CHECK_GAP_SECONDS} seconds.')
            # Finish early if the managed job status is already in terminal
            # state.
            managed_job_status = managed_job_state.get_status(job_id)
            assert managed_job_status is not None, job_id
            if managed_job_status.is_terminal():
                break
            logger.info(f'{colorama.Fore.YELLOW}The job cluster is preempted '
                        f'or failed.{colorama.Style.RESET_ALL}')
            msg = _JOB_CANCELLED_MESSAGE
            status_display.update(msg)
            prev_msg = msg
            status_display.start()
            # If the tailing fails, it is likely that the cluster fails, so we
            # wait a while to make sure the managed job state is updated by the
            # controller, and check the managed job queue again.
            # Wait a bit longer than the controller, so as to make sure the
            # managed job state is updated.
            time.sleep(3 * JOB_STATUS_CHECK_GAP_SECONDS)
            managed_job_status = managed_job_state.get_status(job_id)

    # The managed_job_status may not be in terminal status yet, since the
    # controller has not updated the managed job state yet. We wait for a while,
    # until the managed job state is updated.
    wait_seconds = 0
    managed_job_status = managed_job_state.get_status(job_id)
    assert managed_job_status is not None, job_id
    while (not managed_job_status.is_terminal() and follow and
           wait_seconds < _FINAL_JOB_STATUS_WAIT_TIMEOUT_SECONDS):
        time.sleep(1)
        wait_seconds += 1
        managed_job_status = managed_job_state.get_status(job_id)
        assert managed_job_status is not None, job_id

    logger.info(
        ux_utils.finishing_message(f'Managed job finished: {job_id} '
                                   f'(status: {managed_job_status.value}).'))
    return ''


def stream_logs(job_id: Optional[int],
                job_name: Optional[str],
                controller: bool = False,
                follow: bool = True) -> str:
    """Stream logs by job id or job name."""
    if job_id is None and job_name is None:
        job_id = managed_job_state.get_latest_job_id()
        if job_id is None:
            return 'No managed job found.'

    if controller:
        if job_id is None:
            assert job_name is not None
            managed_jobs = managed_job_state.get_managed_jobs()
            # We manually filter the jobs by name, instead of using
            # get_nonterminal_job_ids_by_name, as with `controller=True`, we
            # should be able to show the logs for jobs in terminal states.
            managed_job_ids: Set[int] = {
                job['job_id']
                for job in managed_jobs
                if job['job_name'] == job_name
            }
            if len(managed_job_ids) == 0:
                return f'No managed job found with name {job_name!r}.'
            if len(managed_job_ids) > 1:
                job_ids_str = ', '.join(
                    str(job_id) for job_id in managed_job_ids)
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Multiple managed jobs found with name {job_name!r} '
                        f'(Job IDs: {job_ids_str}). Please specify the job_id '
                        'instead.')
            job_id = managed_job_ids.pop()
        assert job_id is not None, (job_id, job_name)
        # TODO: keep the following code sync with
        # job_lib.JobLibCodeGen.tail_logs, we do not directly call that function
        # as the following code need to be run in the current machine, instead
        # of running remotely.
        run_timestamp = job_lib.get_run_timestamp(job_id)
        if run_timestamp is None:
            return f'No managed job contrller log found with job_id {job_id}.'
        log_dir = os.path.join(constants.SKY_LOGS_DIRECTORY, run_timestamp)
        log_lib.tail_logs(job_id=job_id, log_dir=log_dir, follow=follow)
        return ''

    if job_id is None:
        assert job_name is not None
        job_ids = managed_job_state.get_nonterminal_job_ids_by_name(job_name)
        if len(job_ids) == 0:
            return f'No running managed job found with name {job_name!r}.'
        if len(job_ids) > 1:
            raise ValueError(
                f'Multiple running jobs found with name {job_name!r}.')
        job_id = job_ids[0]

    return stream_logs_by_id(job_id, follow)


def dump_managed_job_queue() -> str:
    jobs = managed_job_state.get_managed_jobs()

    for job in jobs:
        end_at = job['end_at']
        if end_at is None:
            end_at = time.time()

        job_submitted_at = job['last_recovered_at'] - job['job_duration']
        if job['status'] == managed_job_state.ManagedJobStatus.RECOVERING:
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

        cluster_name = generate_managed_job_cluster_name(
            job['task_name'], job['job_id'])
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


def load_managed_job_queue(payload: str) -> List[Dict[str, Any]]:
    """Load job queue from json string."""
    jobs = common_utils.decode_payload(payload)
    for job in jobs:
        job['status'] = managed_job_state.ManagedJobStatus(job['status'])
    return jobs


def _get_job_status_from_tasks(
    job_tasks: List[Dict[str, Any]]
) -> Tuple[managed_job_state.ManagedJobStatus, int]:
    """Get the current task status and the current task id for a job."""
    managed_task_status = managed_job_state.ManagedJobStatus.SUCCEEDED
    current_task_id = 0
    for task in job_tasks:
        managed_task_status = task['status']
        current_task_id = task['task_id']

        # Use the first non-succeeded status.
        if managed_task_status != managed_job_state.ManagedJobStatus.SUCCEEDED:
            # TODO(zhwu): we should not blindly use the first non-
            # succeeded as the status could be changed to SUBMITTED
            # when going from one task to the next one, which can be
            # confusing.
            break
    return managed_task_status, current_task_id


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
    """Returns managed jobs as a formatted string.

    Args:
        jobs: A list of managed jobs.
        show_all: Whether to show all columns.
        max_jobs: The maximum number of jobs to show in the table.
        return_rows: If True, return the rows as a list of strings instead of
          all rows concatenated into a single string.

    Returns: A formatted string of managed jobs, if not `return_rows`; otherwise
      a list of "rows" (each of which is a list of str).
    """
    jobs = collections.defaultdict(list)
    # Check if the tasks have user information.
    tasks_have_user = any([task.get('user') for task in tasks])
    if max_jobs and tasks_have_user:
        raise ValueError('max_jobs is not supported when tasks have user info.')

    def get_hash(task):
        if tasks_have_user:
            return (task['user'], task['job_id'])
        return task['job_id']

    for task in tasks:
        # The tasks within the same job_id are already sorted
        # by the task_id.
        jobs[get_hash(task)].append(task)

    status_counts: Dict[str, int] = collections.defaultdict(int)
    for job_tasks in jobs.values():
        managed_job_status = _get_job_status_from_tasks(job_tasks)[0]
        if not managed_job_status.is_terminal():
            status_counts[managed_job_status.value] += 1

    columns = [
        'ID', 'TASK', 'NAME', 'RESOURCES', 'SUBMITTED', 'TOT. DURATION',
        'JOB DURATION', '#RECOVERIES', 'STATUS'
    ]
    if show_all:
        columns += ['STARTED', 'CLUSTER', 'REGION', 'FAILURE']
    if tasks_have_user:
        columns.insert(0, 'USER')
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
        jobs[get_hash(task)].append(task)

    for job_hash, job_tasks in jobs.items():
        if len(job_tasks) > 1:
            # Aggregate the tasks into a new row in the table.
            job_name = job_tasks[0]['job_name']
            job_duration = 0
            submitted_at = None
            end_at: Optional[int] = 0
            recovery_cnt = 0
            managed_job_status, current_task_id = _get_job_status_from_tasks(
                job_tasks)
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

            failure_reason = job_tasks[current_task_id]['failure_reason']
            job_duration = log_utils.readable_time_duration(0,
                                                            job_duration,
                                                            absolute=True)
            submitted = log_utils.readable_time_duration(submitted_at)
            total_duration = log_utils.readable_time_duration(submitted_at,
                                                              end_at,
                                                              absolute=True)

            status_str = managed_job_status.colored_str()
            if not managed_job_status.is_terminal():
                status_str += f' (task: {current_task_id})'

            job_id = job_hash[1] if tasks_have_user else job_hash
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
            if tasks_have_user:
                job_values.insert(0, job_tasks[0].get('user', '-'))
            job_table.add_row(job_values)

        for task in job_tasks:
            # The job['job_duration'] is already calculated in
            # dump_managed_job_queue().
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
            if tasks_have_user:
                values.insert(0, task.get('user', '-'))
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
        status_str = 'No in-progress managed jobs.'
    output = status_str
    if str(job_table):
        output += f'\n{job_table}'
    if return_rows:
        return job_table.rows
    return output


class ManagedJobCodeGen:
    """Code generator for managed job utility functions.

    Usage:

      >> codegen = ManagedJobCodeGen.show_jobs(...)
    """
    # TODO: the try..except.. block is for backward compatibility. Remove it in
    # v0.8.0.
    _PREFIX = textwrap.dedent("""\
        managed_job_version = 0
        try:
            from sky.jobs import utils
            from sky.jobs import constants as managed_job_constants
            from sky.jobs import state as managed_job_state

            managed_job_version = managed_job_constants.MANAGED_JOBS_VERSION
        except ImportError:
            from sky.spot import spot_state as managed_job_state
            from sky.spot import spot_utils as utils
        """)

    @classmethod
    def get_job_table(cls) -> str:
        code = textwrap.dedent("""\
        if managed_job_version < 1:
            job_table = utils.dump_spot_job_queue()
        else:
            job_table = utils.dump_managed_job_queue()
        print(job_table, flush=True)
        """)
        return cls._build(code)

    @classmethod
    def cancel_jobs_by_id(cls, job_ids: Optional[List[int]]) -> str:
        code = textwrap.dedent(f"""\
        msg = utils.cancel_jobs_by_id({job_ids})
        print(msg, end="", flush=True)
        """)
        return cls._build(code)

    @classmethod
    def cancel_job_by_name(cls, job_name: str) -> str:
        code = textwrap.dedent(f"""\
        msg = utils.cancel_job_by_name({job_name!r})
        print(msg, end="", flush=True)
        """)
        return cls._build(code)

    @classmethod
    def stream_logs(cls,
                    job_name: Optional[str],
                    job_id: Optional[int],
                    follow: bool = True,
                    controller: bool = False) -> str:
        # We inspect the source code of the function here for backward
        # compatibility.
        # TODO: change to utils.stream_logs(job_id, job_name, follow) in v0.8.0.
        # Import libraries required by `stream_logs`. The try...except... block
        # should be removed in v0.8.0.
        code = textwrap.dedent("""\
        import os

        from sky.skylet import job_lib, log_lib
        from sky.skylet import constants
        from sky.utils import ux_utils
        try:
            from sky.jobs.utils import stream_logs_by_id
        except ImportError:
            from sky.spot.spot_utils import stream_logs_by_id
        from typing import Optional
        """)
        code += inspect.getsource(stream_logs)
        code += textwrap.dedent(f"""\

        msg = stream_logs({job_id!r}, {job_name!r},
                           follow={follow}, controller={controller})
        print(msg, flush=True)
        """)
        return cls._build(code)

    @classmethod
    def set_pending(cls, job_id: int, managed_job_dag: 'dag_lib.Dag') -> str:
        dag_name = managed_job_dag.name
        # Add the managed job to queue table.
        code = textwrap.dedent(f"""\
            managed_job_state.set_job_name({job_id}, {dag_name!r})
            """)
        for task_id, task in enumerate(managed_job_dag.tasks):
            resources_str = backend_utils.get_task_resources_str(
                task, is_managed_job=True)
            code += textwrap.dedent(f"""\
                managed_job_state.set_pending({job_id}, {task_id},
                                  {task.name!r}, {resources_str!r})
                """)
        return cls._build(code)

    @classmethod
    def _build(cls, code: str) -> str:
        generated_code = cls._PREFIX + '\n' + code

        return f'{constants.SKY_PYTHON_CMD} -u -c {shlex.quote(generated_code)}'
