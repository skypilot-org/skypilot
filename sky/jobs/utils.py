"""User interfaces with managed jobs.

NOTE: whenever an API change is made in this file, we need to bump the
jobs.constants.MANAGED_JOBS_VERSION and handle the API change in the
ManagedJobCodeGen.
"""
import collections
import enum
import os
import pathlib
import shlex
import textwrap
import time
import traceback
import typing
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import colorama
import filelock
from typing_extensions import Literal

from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.backends import backend_utils
from sky.jobs import constants as managed_job_constants
from sky.jobs import scheduler
from sky.jobs import state as managed_job_state
from sky.skylet import constants
from sky.skylet import job_lib
from sky.skylet import log_lib
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import log_utils
from sky.utils import message_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import psutil

    import sky
    from sky import dag as dag_lib
else:
    psutil = adaptors_common.LazyImport('psutil')

logger = sky_logging.init_logger(__name__)

SIGNAL_FILE_PREFIX = '/tmp/sky_jobs_controller_signal_{}'
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
# blocking for a long time. This should be significantly longer than the
# JOB_STATUS_CHECK_GAP_SECONDS to avoid timing out before the controller can
# update the state.
_FINAL_JOB_STATUS_WAIT_TIMEOUT_SECONDS = 40


class UserSignal(enum.Enum):
    """The signal to be sent to the user."""
    CANCEL = 'CANCEL'
    # NOTE: We can have more communication signals here if needed
    # in the future.


# ====== internal functions ======
def terminate_cluster(cluster_name: str, max_retry: int = 6) -> None:
    """Terminate the cluster."""
    from sky import core  # pylint: disable=import-outside-toplevel
    retry_cnt = 0
    # In some cases, e.g. botocore.exceptions.NoCredentialsError due to AWS
    # metadata service throttling, the failed sky.down attempt can take 10-11
    # seconds. In this case, we need the backoff to significantly reduce the
    # rate of requests - that is, significantly increase the time between
    # requests. We set the initial backoff to 15 seconds, so that once it grows
    # exponentially it will quickly dominate the 10-11 seconds that we already
    # see between requests. We set the max backoff very high, since it's
    # generally much more important to eventually succeed than to fail fast.
    backoff = common_utils.Backoff(
        initial_backoff=15,
        # 1.6 ** 5 = 10.48576 < 20, so we won't hit this with default max_retry
        max_backoff_factor=20)
    while True:
        try:
            usage_lib.messages.usage.set_internal()
            core.down(cluster_name)
            return
        except exceptions.ClusterDoesNotExist:
            # The cluster is already down.
            logger.debug(f'The cluster {cluster_name} is already down.')
            return
        except Exception as e:  # pylint: disable=broad-except
            retry_cnt += 1
            if retry_cnt >= max_retry:
                raise RuntimeError(
                    f'Failed to terminate the cluster {cluster_name}.') from e
            logger.error(
                f'Failed to terminate the cluster {cluster_name}. Retrying.'
                f'Details: {common_utils.format_exception(e)}')
            with ux_utils.enable_traceback():
                logger.error(f'  Traceback: {traceback.format_exc()}')
            time.sleep(backoff.current_backoff())


def get_job_status(backend: 'backends.CloudVmRayBackend',
                   cluster_name: str) -> Optional['job_lib.JobStatus']:
    """Check the status of the job running on a managed job cluster.

    It can be None, INIT, RUNNING, SUCCEEDED, FAILED, FAILED_DRIVER,
    FAILED_SETUP or CANCELLED.
    """
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    if handle is None:
        # This can happen if the cluster was preempted and background status
        # refresh already noticed and cleaned it up.
        logger.info(f'Cluster {cluster_name} not found.')
        return None
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


def _controller_process_alive(pid: int, job_id: int) -> bool:
    """Check if the controller process is alive."""
    try:
        process = psutil.Process(pid)
        # The last two args of the command line should be --job-id <id>
        job_args = process.cmdline()[-2:]
        return process.is_running() and job_args == ['--job-id', str(job_id)]
    except psutil.NoSuchProcess:
        return False


def update_managed_jobs_statuses(job_id: Optional[int] = None):
    """Update managed job status if the controller process failed abnormally.

    Check the status of the controller process. If it is not running, it must
    have exited abnormally, and we should set the job status to
    FAILED_CONTROLLER. `end_at` will be set to the current timestamp for the job
    when above happens, which could be not accurate based on the frequency this
    function is called.

    Note: we expect that job_id, if provided, refers to a nonterminal job or a
    job that has not completed its cleanup (schedule state not DONE).
    """

    def _cleanup_job_clusters(job_id: int) -> Optional[str]:
        """Clean up clusters for a job. Returns error message if any.

        This function should not throw any exception. If it fails, it will
        capture the error message, and log/return it.
        """
        error_msg = None
        tasks = managed_job_state.get_managed_jobs(job_id)
        for task in tasks:
            task_name = task['job_name']
            cluster_name = generate_managed_job_cluster_name(task_name, job_id)
            handle = global_user_state.get_handle_from_cluster_name(
                cluster_name)
            if handle is not None:
                try:
                    terminate_cluster(cluster_name)
                except Exception as e:  # pylint: disable=broad-except
                    error_msg = (
                        f'Failed to terminate cluster {cluster_name}: '
                        f'{common_utils.format_exception(e, use_bracket=True)}')
                    logger.exception(error_msg, exc_info=e)
        return error_msg

    # For backwards compatible jobs
    # TODO(cooperc): Remove before 0.11.0.
    def _handle_legacy_job(job_id: int):
        controller_status = job_lib.get_status(job_id)
        if controller_status is None or controller_status.is_terminal():
            logger.error(f'Controller process for legacy job {job_id} is '
                         'in an unexpected state.')

            cleanup_error = _cleanup_job_clusters(job_id)
            if cleanup_error:
                # Unconditionally set the job to failed_controller if the
                # cleanup fails.
                managed_job_state.set_failed(
                    job_id,
                    task_id=None,
                    failure_type=managed_job_state.ManagedJobStatus.
                    FAILED_CONTROLLER,
                    failure_reason=
                    'Legacy controller process has exited abnormally, and '
                    f'cleanup failed: {cleanup_error}. For more details, run: '
                    f'sky jobs logs --controller {job_id}',
                    override_terminal=True)
                return

            # It's possible for the job to have transitioned to
            # another terminal state while between when we checked its
            # state and now. In that case, set_failed won't do
            # anything, which is fine.
            managed_job_state.set_failed(
                job_id,
                task_id=None,
                failure_type=managed_job_state.ManagedJobStatus.
                FAILED_CONTROLLER,
                failure_reason=(
                    'Legacy controller process has exited abnormally. For '
                    f'more details, run: sky jobs logs --controller {job_id}'))

    # Get jobs that need checking (non-terminal or not DONE)
    job_ids = managed_job_state.get_jobs_to_check_status(job_id)
    if not job_ids:
        # job_id is already terminal, or if job_id is None, there are no jobs
        # that need to be checked.
        return

    for job_id in job_ids:
        tasks = managed_job_state.get_managed_jobs(job_id)
        # Note: controller_pid and schedule_state are in the job_info table
        # which is joined to the spot table, so all tasks with the same job_id
        # will have the same value for these columns. This is what lets us just
        # take tasks[0]['controller_pid'] and tasks[0]['schedule_state'].
        schedule_state = tasks[0]['schedule_state']

        # Backwards compatibility: this job was submitted when ray was still
        # used for managing the parallelism of job controllers, before #4485.
        # TODO(cooperc): Remove before 0.11.0.
        if (schedule_state is
                managed_job_state.ManagedJobScheduleState.INVALID):
            _handle_legacy_job(job_id)
            continue

        # Handle jobs with schedule state (non-legacy jobs):
        pid = tasks[0]['controller_pid']
        if schedule_state == managed_job_state.ManagedJobScheduleState.DONE:
            # There are two cases where we could get a job that is DONE.
            # 1. At query time (get_jobs_to_check_status), the job was not yet
            #    DONE, but since then (before get_managed_jobs is called) it has
            #    hit a terminal status, marked itself done, and exited. This is
            #    fine.
            # 2. The job is DONE, but in a non-terminal status. This is
            #    unexpected. For instance, the task status is RUNNING, but the
            #    job schedule_state is DONE.
            if all(task['status'].is_terminal() for task in tasks):
                # Turns out this job is fine, even though it got pulled by
                # get_jobs_to_check_status. Probably case #1 above.
                continue

            logger.error(f'Job {job_id} has DONE schedule state, but some '
                         f'tasks are not terminal. Task statuses: '
                         f'{", ".join(task["status"].value for task in tasks)}')
            failure_reason = ('Inconsistent internal job state. This is a bug.')
        elif pid is None:
            # Non-legacy job and controller process has not yet started.
            controller_status = job_lib.get_status(job_id)
            if controller_status == job_lib.JobStatus.FAILED_SETUP:
                # We should fail the case where the controller status is
                # FAILED_SETUP, as it is due to the failure of dependency setup
                # on the controller.
                # TODO(cooperc): We should also handle the case where controller
                # status is FAILED_DRIVER or FAILED.
                logger.error('Failed to setup the cloud dependencies for '
                             'the managed job.')
            elif (schedule_state in [
                    managed_job_state.ManagedJobScheduleState.INACTIVE,
                    managed_job_state.ManagedJobScheduleState.WAITING,
            ]):
                # It is expected that the controller hasn't been started yet.
                continue
            elif (schedule_state ==
                  managed_job_state.ManagedJobScheduleState.LAUNCHING):
                # This is unlikely but technically possible. There's a brief
                # period between marking job as scheduled (LAUNCHING) and
                # actually launching the controller process and writing the pid
                # back to the table.
                # TODO(cooperc): Find a way to detect if we get stuck in this
                # state.
                logger.info(f'Job {job_id} is in {schedule_state.value} state, '
                            'but controller process hasn\'t started yet.')
                continue

            logger.error(f'Expected to find a controller pid for state '
                         f'{schedule_state.value} but found none.')
            failure_reason = f'No controller pid set for {schedule_state.value}'
        else:
            logger.debug(f'Checking controller pid {pid}')
            if _controller_process_alive(pid, job_id):
                # The controller is still running, so this job is fine.
                continue

            # Double check job is not already DONE before marking as failed, to
            # avoid the race where the controller marked itself as DONE and
            # exited between the state check and the pid check. Since the job
            # controller process will mark itself DONE _before_ exiting, if it
            # has exited and it's still not DONE now, it is abnormal.
            if (managed_job_state.get_job_schedule_state(job_id) ==
                    managed_job_state.ManagedJobScheduleState.DONE):
                # Never mind, the job is DONE now. This is fine.
                continue

            logger.error(f'Controller process for {job_id} seems to be dead.')
            failure_reason = 'Controller process is dead'

        # At this point, either pid is None or process is dead.

        # The controller process for this managed job is not running: it must
        # have exited abnormally, and we should set the job status to
        # FAILED_CONTROLLER.
        logger.error(f'Controller process for job {job_id} has exited '
                     'abnormally. Setting the job status to FAILED_CONTROLLER.')

        # Cleanup clusters and capture any errors.
        cleanup_error = _cleanup_job_clusters(job_id)
        cleanup_error_msg = ''
        if cleanup_error:
            cleanup_error_msg = f'Also, cleanup failed: {cleanup_error}. '

        # Set all tasks to FAILED_CONTROLLER, regardless of current status.
        # This may change a job from SUCCEEDED or another terminal state to
        # FAILED_CONTROLLER. This is what we want - we are sure that this
        # controller process crashed, so we want to capture that even if the
        # underlying job succeeded.
        # Note: 2+ invocations of update_managed_jobs_statuses could be running
        # at the same time, so this could override the FAILED_CONTROLLER status
        # set by another invocation of update_managed_jobs_statuses. That should
        # be okay. The only difference could be that one process failed to clean
        # up the cluster while the other succeeds. No matter which
        # failure_reason ends up in the database, the outcome is acceptable.
        # We assume that no other code path outside the controller process will
        # update the job status.
        managed_job_state.set_failed(
            job_id,
            task_id=None,
            failure_type=managed_job_state.ManagedJobStatus.FAILED_CONTROLLER,
            failure_reason=
            f'Controller process has exited abnormally ({failure_reason}). '
            f'{cleanup_error_msg}'
            f'For more details, run: sky jobs logs --controller {job_id}',
            override_terminal=True)

        scheduler.job_done(job_id, idempotent=True)


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
    stdout = message_utils.decode_payload(stdout)
    return float(stdout)


def try_to_get_job_end_time(backend: 'backends.CloudVmRayBackend',
                            cluster_name: str) -> float:
    """Try to get the end time of the job.

    If the job is preempted or we can't connect to the instance for whatever
    reason, fall back to the current time.
    """
    try:
        return get_job_timestamp(backend, cluster_name, get_end_time=True)
    except exceptions.CommandError as e:
        if e.returncode == 255:
            # Failed to connect - probably the instance was preempted since the
            # job completed. We shouldn't crash here, so just log and use the
            # current time.
            logger.info(f'Failed to connect to the instance {cluster_name} '
                        'since the job completed. Assuming the instance '
                        'was preempted.')
            return time.time()
        else:
            raise


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


def cancel_jobs_by_id(job_ids: Optional[List[int]],
                      all_users: bool = False) -> str:
    """Cancel jobs by id.

    If job_ids is None, cancel all jobs.
    """
    if job_ids is None:
        job_ids = managed_job_state.get_nonterminal_job_ids_by_name(
            None, all_users)
    job_ids = list(set(job_ids))
    if not job_ids:
        return 'No job to cancel.'
    job_id_str = ', '.join(map(str, job_ids))
    logger.info(f'Cancelling jobs {job_id_str}.')
    cancelled_job_ids: List[int] = []
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

        update_managed_jobs_statuses(job_id)

        # Send the signal to the jobs controller.
        signal_file = pathlib.Path(SIGNAL_FILE_PREFIX.format(job_id))
        # Filelock is needed to prevent race condition between signal
        # check/removal and signal writing.
        with filelock.FileLock(str(signal_file) + '.lock'):
            with signal_file.open('w', encoding='utf-8') as f:
                f.write(UserSignal.CANCEL.value)
                f.flush()
        cancelled_job_ids.append(job_id)

    if not cancelled_job_ids:
        return 'No job to cancel.'
    identity_str = f'Job with ID {cancelled_job_ids[0]} is'
    if len(cancelled_job_ids) > 1:
        cancelled_job_ids_str = ', '.join(map(str, cancelled_job_ids))
        identity_str = f'Jobs with IDs {cancelled_job_ids_str} are'

    return f'{identity_str} scheduled to be cancelled.'


def cancel_job_by_name(job_name: str) -> str:
    """Cancel a job by name."""
    job_ids = managed_job_state.get_nonterminal_job_ids_by_name(job_name)
    if not job_ids:
        return f'No running job found with name {job_name!r}.'
    if len(job_ids) > 1:
        return (f'{colorama.Fore.RED}Multiple running jobs found '
                f'with name {job_name!r}.\n'
                f'Job IDs: {job_ids}{colorama.Style.RESET_ALL}')
    cancel_jobs_by_id(job_ids)
    return f'Job {job_name!r} is scheduled to be cancelled.'


def stream_logs_by_id(job_id: int, follow: bool = True) -> Tuple[str, int]:
    """Stream logs by job id.

    Returns:
        A tuple containing the log message and an exit code based on success or
        failure of the job. 0 if success, 100 if the job failed.
        See exceptions.JobExitCode for possible exit codes.
    """

    def should_keep_logging(status: managed_job_state.ManagedJobStatus) -> bool:
        # If we see CANCELLING, just exit - we could miss some job logs but the
        # job will be terminated momentarily anyway so we don't really care.
        return (not status.is_terminal() and
                status != managed_job_state.ManagedJobStatus.CANCELLING)

    msg = _JOB_WAITING_STATUS_MESSAGE.format(status_str='', job_id=job_id)
    status_display = rich_utils.safe_status(msg)
    num_tasks = managed_job_state.get_num_tasks(job_id)

    with status_display:
        prev_msg = msg
        while (managed_job_status :=
               managed_job_state.get_status(job_id)) is None:
            time.sleep(1)

        if not should_keep_logging(managed_job_status):
            job_msg = ''
            if managed_job_status.is_failed():
                job_msg = ('\nFailure reason: '
                           f'{managed_job_state.get_failure_reason(job_id)}')
            log_file = managed_job_state.get_local_log_file(job_id, None)
            if log_file is not None:
                with open(os.path.expanduser(log_file), 'r',
                          encoding='utf-8') as f:
                    # Stream the logs to the console without reading the whole
                    # file into memory.
                    start_streaming = False
                    for line in f:
                        if log_lib.LOG_FILE_START_STREAMING_AT in line:
                            start_streaming = True
                        if start_streaming:
                            print(line, end='', flush=True)
                return '', exceptions.JobExitCode.from_managed_job_status(
                    managed_job_status)
            return (f'{colorama.Fore.YELLOW}'
                    f'Job {job_id} is already in terminal state '
                    f'{managed_job_status.value}. For more details, run: '
                    f'sky jobs logs --controller {job_id}'
                    f'{colorama.Style.RESET_ALL}'
                    f'{job_msg}',
                    exceptions.JobExitCode.from_managed_job_status(
                        managed_job_status))
        backend = backends.CloudVmRayBackend()
        task_id, managed_job_status = (
            managed_job_state.get_latest_task_id_status(job_id))

        # We wait for managed_job_status to be not None above. Once we see that
        # it's not None, we don't expect it to every become None again.
        assert managed_job_status is not None, (job_id, task_id,
                                                managed_job_status)

        while should_keep_logging(managed_job_status):
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
                assert managed_job_status is not None, (job_id, task_id,
                                                        managed_job_status)
                continue
            assert (managed_job_status ==
                    managed_job_state.ManagedJobStatus.RUNNING)
            assert isinstance(handle, backends.CloudVmRayResourceHandle), handle
            status_display.stop()
            returncode = backend.tail_logs(handle,
                                           job_id=None,
                                           managed_job_id=job_id,
                                           follow=follow)
            if returncode in [rc.value for rc in exceptions.JobExitCode]:
                # If the log tailing exits with a known exit code we can safely
                # break the loop because it indicates the tailing process
                # succeeded (even though the real job can be SUCCEEDED or
                # FAILED). We use the status in job queue to show the
                # information, as the ManagedJobStatus is not updated yet.
                job_statuses = backend.get_job_status(handle, stream_logs=False)
                job_status = list(job_statuses.values())[0]
                assert job_status is not None, 'No job found.'
                assert task_id is not None, job_id

                if job_status != job_lib.JobStatus.CANCELLED:
                    if not follow:
                        break

                    # Logs for retrying failed tasks.
                    if (job_status
                            in job_lib.JobStatus.user_code_failure_states()):
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

                        def is_managed_job_status_updated(
                            status: Optional[managed_job_state.ManagedJobStatus]
                        ) -> bool:
                            """Check if local managed job status reflects remote
                            job failure.

                            Ensures synchronization between remote cluster
                            failure detection (JobStatus.FAILED) and controller
                            retry logic.
                            """
                            return (status !=
                                    managed_job_state.ManagedJobStatus.RUNNING)

                        while not is_managed_job_status_updated(
                                managed_job_status :=
                                managed_job_state.get_status(job_id)):
                            time.sleep(JOB_STATUS_CHECK_GAP_SECONDS)
                        assert managed_job_status is not None, (
                            job_id, managed_job_status)
                        continue

                    if task_id == num_tasks - 1:
                        break

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
                            managed_job_state.get_latest_task_id_status(job_id))
                        if original_task_id != task_id:
                            break
                        time.sleep(JOB_STATUS_CHECK_GAP_SECONDS)
                    assert managed_job_status is not None, (job_id, task_id,
                                                            managed_job_status)
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
            if not should_keep_logging(managed_job_status):
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
            assert managed_job_status is not None, (job_id, managed_job_status)

    # The managed_job_status may not be in terminal status yet, since the
    # controller has not updated the managed job state yet. We wait for a while,
    # until the managed job state is updated.
    wait_seconds = 0
    managed_job_status = managed_job_state.get_status(job_id)
    assert managed_job_status is not None, job_id
    while (should_keep_logging(managed_job_status) and follow and
           wait_seconds < _FINAL_JOB_STATUS_WAIT_TIMEOUT_SECONDS):
        time.sleep(1)
        wait_seconds += 1
        managed_job_status = managed_job_state.get_status(job_id)
        assert managed_job_status is not None, job_id

    if not follow and not managed_job_status.is_terminal():
        # The job is not in terminal state and we are not following,
        # just return.
        return '', exceptions.JobExitCode.SUCCEEDED
    logger.info(
        ux_utils.finishing_message(f'Managed job finished: {job_id} '
                                   f'(status: {managed_job_status.value}).'))
    return '', exceptions.JobExitCode.from_managed_job_status(
        managed_job_status)


def stream_logs(job_id: Optional[int],
                job_name: Optional[str],
                controller: bool = False,
                follow: bool = True) -> Tuple[str, int]:
    """Stream logs by job id or job name.

    Returns:
        A tuple containing the log message and the exit code based on success
        or failure of the job. 0 if success, 100 if the job failed.
        See exceptions.JobExitCode for possible exit codes.
    """
    if job_id is None and job_name is None:
        job_id = managed_job_state.get_latest_job_id()
        if job_id is None:
            return 'No managed job found.', exceptions.JobExitCode.NOT_FOUND

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
            if not managed_job_ids:
                return (f'No managed job found with name {job_name!r}.',
                        exceptions.JobExitCode.NOT_FOUND)
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

        controller_log_path = os.path.join(
            os.path.expanduser(managed_job_constants.JOBS_CONTROLLER_LOGS_DIR),
            f'{job_id}.log')
        job_status = None

        # Wait for the log file to be written
        while not os.path.exists(controller_log_path):
            if not follow:
                # Assume that the log file hasn't been written yet. Since we
                # aren't following, just return.
                return '', exceptions.JobExitCode.SUCCEEDED

            job_status = managed_job_state.get_status(job_id)
            if job_status is None:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(f'Job {job_id} not found.')
            if job_status.is_terminal():
                # Don't keep waiting. If the log file is not created by this
                # point, it never will be. This job may have been submitted
                # using an old version that did not create the log file, so this
                # is not considered an exceptional case.
                return '', exceptions.JobExitCode.from_managed_job_status(
                    job_status)

            time.sleep(log_lib.SKY_LOG_WAITING_GAP_SECONDS)

        # This code is based on log_lib.tail_logs. We can't use that code
        # exactly because state works differently between managed jobs and
        # normal jobs.
        with open(controller_log_path, 'r', newline='', encoding='utf-8') as f:
            # Note: we do not need to care about start_stream_at here, since
            # that should be in the job log printed above.
            for line in f:
                print(line, end='')
            # Flush.
            print(end='', flush=True)

            if follow:
                while True:
                    # Print all new lines, if there are any.
                    line = f.readline()
                    while line is not None and line != '':
                        print(line, end='')
                        line = f.readline()

                    # Flush.
                    print(end='', flush=True)

                    # Check if the job if finished.
                    # TODO(cooperc): The controller can still be
                    # cleaning up if job is in a terminal status
                    # (e.g. SUCCEEDED). We want to follow those logs
                    # too. Use DONE instead?
                    job_status = managed_job_state.get_status(job_id)
                    assert job_status is not None, (job_id, job_name)
                    if job_status.is_terminal():
                        break

                    time.sleep(log_lib.SKY_LOG_TAILING_GAP_SECONDS)

                # Wait for final logs to be written.
                time.sleep(1 + log_lib.SKY_LOG_TAILING_GAP_SECONDS)

            # Print any remaining logs including incomplete line.
            print(f.read(), end='', flush=True)

        if follow:
            return ux_utils.finishing_message(
                f'Job finished (status: {job_status}).'
            ), exceptions.JobExitCode.from_managed_job_status(job_status)

        return '', exceptions.JobExitCode.SUCCEEDED

    if job_id is None:
        assert job_name is not None
        job_ids = managed_job_state.get_nonterminal_job_ids_by_name(job_name)
        if not job_ids:
            return (f'No running managed job found with name {job_name!r}.',
                    exceptions.JobExitCode.NOT_FOUND)
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
        job['schedule_state'] = job['schedule_state'].value

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

    return message_utils.encode_payload(jobs)


def load_managed_job_queue(payload: str) -> List[Dict[str, Any]]:
    """Load job queue from json string."""
    jobs = message_utils.decode_payload(payload)
    for job in jobs:
        job['status'] = managed_job_state.ManagedJobStatus(job['status'])
        if 'user_hash' in job and job['user_hash'] is not None:
            # Skip jobs that do not have user_hash info.
            # TODO(cooperc): Remove check before 0.12.0.
            job['user_name'] = global_user_state.get_user(job['user_hash']).name
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
                     show_user: bool,
                     return_rows: Literal[False] = False,
                     max_jobs: Optional[int] = None) -> str:
    ...


@typing.overload
def format_job_table(tasks: List[Dict[str, Any]],
                     show_all: bool,
                     show_user: bool,
                     return_rows: Literal[True],
                     max_jobs: Optional[int] = None) -> List[List[str]]:
    ...


def format_job_table(
        tasks: List[Dict[str, Any]],
        show_all: bool,
        show_user: bool,
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
    # Check if the tasks have user information from kubernetes.
    # This is only used for sky status --kubernetes.
    tasks_have_k8s_user = any([task.get('user') for task in tasks])
    if max_jobs and tasks_have_k8s_user:
        raise ValueError('max_jobs is not supported when tasks have user info.')

    def get_hash(task):
        if tasks_have_k8s_user:
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

    user_cols: List[str] = []
    if show_user:
        user_cols = ['USER']
        if show_all:
            user_cols.append('USER_ID')

    columns = [
        'ID',
        'TASK',
        'NAME',
        *user_cols,
        'RESOURCES',
        'SUBMITTED',
        'TOT. DURATION',
        'JOB DURATION',
        '#RECOVERIES',
        'STATUS',
    ]
    if show_all:
        # TODO: move SCHED. STATE to a separate flag (e.g. --debug)
        columns += ['STARTED', 'CLUSTER', 'REGION', 'SCHED. STATE', 'DETAILS']
    if tasks_have_k8s_user:
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

    def generate_details(failure_reason: Optional[str]) -> str:
        if failure_reason is not None:
            return f'Failure: {failure_reason}'
        return '-'

    def get_user_column_values(task: Dict[str, Any]) -> List[str]:
        user_values: List[str] = []
        if show_user:
            user_name = '-'  # default value

            task_user_name = task.get('user_name', None)
            task_user_hash = task.get('user_hash', None)
            if task_user_name is not None:
                user_name = task_user_name
            elif task_user_hash is not None:
                # Fallback to the user hash if we are somehow missing the name.
                user_name = task_user_hash

            user_values = [user_name]

            if show_all:
                user_values.append(
                    task_user_hash if task_user_hash is not None else '-')

        return user_values

    for job_hash, job_tasks in jobs.items():
        if show_all:
            schedule_state = job_tasks[0]['schedule_state']

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

            user_values = get_user_column_values(job_tasks[0])

            job_id = job_hash[1] if tasks_have_k8s_user else job_hash
            job_values = [
                job_id,
                '',
                job_name,
                *user_values,
                '-',
                submitted,
                total_duration,
                job_duration,
                recovery_cnt,
                status_str,
            ]
            if show_all:
                failure_reason = job_tasks[current_task_id]['failure_reason']
                job_values.extend([
                    '-',
                    '-',
                    '-',
                    job_tasks[0]['schedule_state'],
                    generate_details(failure_reason),
                ])
            if tasks_have_k8s_user:
                job_values.insert(0, job_tasks[0].get('user', '-'))
            job_table.add_row(job_values)

        for task in job_tasks:
            # The job['job_duration'] is already calculated in
            # dump_managed_job_queue().
            job_duration = log_utils.readable_time_duration(
                0, task['job_duration'], absolute=True)
            submitted = log_utils.readable_time_duration(task['submitted_at'])
            user_values = get_user_column_values(task)
            values = [
                task['job_id'] if len(job_tasks) == 1 else ' \u21B3',
                task['task_id'] if len(job_tasks) > 1 else '-',
                task['task_name'],
                *user_values,
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
                # schedule_state is only set at the job level, so if we have
                # more than one task, only display on the aggregated row.
                schedule_state = (task['schedule_state']
                                  if len(job_tasks) == 1 else '-')
                values.extend([
                    # STARTED
                    log_utils.readable_time_duration(task['start_at']),
                    task['cluster_resources'],
                    task['region'],
                    schedule_state,
                    generate_details(task['failure_reason']),
                ])
            if tasks_have_k8s_user:
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
    _PREFIX = textwrap.dedent("""\
        import sys
        from sky.jobs import utils
        from sky.jobs import state as managed_job_state
        from sky.jobs import constants as managed_job_constants

        managed_job_version = managed_job_constants.MANAGED_JOBS_VERSION
        """)

    @classmethod
    def get_job_table(cls) -> str:
        code = textwrap.dedent("""\
        job_table = utils.dump_managed_job_queue()
        print(job_table, flush=True)
        """)
        return cls._build(code)

    @classmethod
    def cancel_jobs_by_id(cls,
                          job_ids: Optional[List[int]],
                          all_users: bool = False) -> str:
        code = textwrap.dedent(f"""\
        if managed_job_version < 2:
            # For backward compatibility, since all_users is not supported
            # before #4787. Assume th
            # TODO(cooperc): Remove compatibility before 0.12.0
            msg = utils.cancel_jobs_by_id({job_ids})
        else:
            msg = utils.cancel_jobs_by_id({job_ids}, all_users={all_users})
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
    def get_all_job_ids_by_name(cls, job_name: Optional[str]) -> str:
        code = textwrap.dedent(f"""\
        from sky.utils import message_utils
        job_id = managed_job_state.get_all_job_ids_by_name({job_name!r})
        print(message_utils.encode_payload(job_id), end="", flush=True)
        """)
        return cls._build(code)

    @classmethod
    def stream_logs(cls,
                    job_name: Optional[str],
                    job_id: Optional[int],
                    follow: bool = True,
                    controller: bool = False) -> str:
        code = textwrap.dedent(f"""\
        result = utils.stream_logs(job_id={job_id!r}, job_name={job_name!r},
                                follow={follow}, controller={controller})
        if managed_job_version < 3:
            # Versions 2 and older did not return a retcode, so we just print
            # the result.
            # TODO: Remove compatibility before 0.12.0
            print(result, flush=True)
        else:
            msg, retcode = result
            print(msg, flush=True)
            sys.exit(retcode)
        """)
        return cls._build(code)

    @classmethod
    def set_pending(cls, job_id: int, managed_job_dag: 'dag_lib.Dag') -> str:
        dag_name = managed_job_dag.name
        # Add the managed job to queue table.
        code = textwrap.dedent(f"""\
            managed_job_state.set_job_info({job_id}, {dag_name!r})
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
        # Use the local user id to make sure the operation goes to the correct
        # user.
        return (
            f'export {constants.USER_ID_ENV_VAR}='
            f'"{common_utils.get_user_hash()}"; '
            f'{constants.SKY_PYTHON_CMD} -u -c {shlex.quote(generated_code)}')
