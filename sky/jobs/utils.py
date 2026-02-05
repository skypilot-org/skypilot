"""User interfaces with managed jobs.

NOTE: whenever an API change is made in this file, we need to bump the
jobs.constants.MANAGED_JOBS_VERSION and handle the API change in the
ManagedJobCodeGen.
"""
import asyncio
import collections
from datetime import datetime
import enum
import os
import pathlib
import re
import shlex
import textwrap
import time
import traceback
import typing
from typing import (Any, Deque, Dict, Iterable, List, Literal, Optional, Set,
                    TextIO, Tuple, Union)

import colorama
import filelock

from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.dag import DagExecution
from sky.dag import DEFAULT_EXECUTION
from sky.jobs import constants as managed_job_constants
from sky.jobs import scheduler
from sky.jobs import state as managed_job_state
from sky.schemas.api import responses
from sky.skylet import constants
from sky.skylet import job_lib
from sky.skylet import log_lib
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import infra_utils
from sky.utils import log_utils
from sky.utils import message_utils
from sky.utils import resources_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from google.protobuf import descriptor
    from google.protobuf import json_format
    import grpc
    import psutil

    import sky
    from sky import dag as dag_lib
    from sky.schemas.generated import jobsv1_pb2
    from sky.schemas.generated import managed_jobsv1_pb2
else:
    json_format = adaptors_common.LazyImport('google.protobuf.json_format')
    descriptor = adaptors_common.LazyImport('google.protobuf.descriptor')
    psutil = adaptors_common.LazyImport('psutil')
    grpc = adaptors_common.LazyImport('grpc')
    jobsv1_pb2 = adaptors_common.LazyImport('sky.schemas.generated.jobsv1_pb2')
    managed_jobsv1_pb2 = adaptors_common.LazyImport(
        'sky.schemas.generated.managed_jobsv1_pb2')

logger = sky_logging.init_logger(__name__)

# Controller checks its job's status every this many seconds.
# This is a tradeoff between the latency and the resource usage.
JOB_STATUS_CHECK_GAP_SECONDS = 15

# Controller checks if its job has started every this many seconds.
JOB_STARTED_STATUS_CHECK_GAP_SECONDS = 5

_LOG_STREAM_CHECK_CONTROLLER_GAP_SECONDS = 5

_JOB_STATUS_FETCH_TIMEOUT_SECONDS = 30
JOB_STATUS_FETCH_TOTAL_TIMEOUT_SECONDS = 60

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
_FINAL_JOB_STATUS_WAIT_TIMEOUT_SECONDS = 120

# After enabling consolidation mode, we need to restart the API server to get
# the jobs refresh deamon and correct number of executors. We use this file to
# indicate that the API server has been restarted after enabling consolidation
# mode.
_JOBS_CONSOLIDATION_RELOADED_SIGNAL_FILE = (
    '~/.sky/.jobs_controller_consolidation_reloaded_signal')

# The response fields for managed jobs that require cluster handle
_CLUSTER_HANDLE_FIELDS = [
    'cluster_resources',
    'cluster_resources_full',
    'cloud',
    'region',
    'zone',
    'infra',
    'accelerators',
    'cluster_name_on_cloud',
    'labels',
]

# The response fields for managed jobs that are not stored in the database
# These fields will be mapped to the DB fields in the `_update_fields`.
_NON_DB_FIELDS = _CLUSTER_HANDLE_FIELDS + [
    'user_yaml',
    'user_name',
    'details',
    # is_job_group is derived from execution column (execution == 'parallel')
    'is_job_group',
]


class ManagedJobQueueResultType(enum.Enum):
    """The type of the managed job queue result."""
    DICT = 'DICT'
    LIST = 'LIST'


class UserSignal(enum.Enum):
    """The signal to be sent to the user."""
    CANCEL = 'CANCEL'
    # NOTE: We can have more communication signals here if needed
    # in the future.


# ====== internal functions ======
def terminate_cluster(
    cluster_name: str,
    max_retry: int = 6,
) -> None:
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


def _validate_consolidation_mode_config(
        current_is_consolidation_mode: bool) -> None:
    """Validate the consolidation mode config."""
    # Check whether the consolidation mode config is changed.
    if current_is_consolidation_mode:
        controller_cn = (
            controller_utils.Controllers.JOBS_CONTROLLER.value.cluster_name)
        if global_user_state.cluster_with_name_exists(controller_cn):
            logger.warning(
                f'{colorama.Fore.RED}Consolidation mode for jobs is enabled, '
                f'but the controller cluster {controller_cn} is still running. '
                'Please terminate the controller cluster first.'
                f'{colorama.Style.RESET_ALL}')
    else:
        total_jobs = managed_job_state.get_managed_jobs_total()
        if total_jobs > 0:
            nonterminal_jobs = (
                managed_job_state.get_nonterminal_job_ids_by_name(
                    None, None, all_users=True))
            if nonterminal_jobs:
                logger.warning(
                    f'{colorama.Fore.YELLOW}Consolidation mode is disabled, '
                    f'but there are still {len(nonterminal_jobs)} managed jobs '
                    'running. Please terminate those jobs first.'
                    f'{colorama.Style.RESET_ALL}')
            else:
                logger.warning(
                    f'{colorama.Fore.YELLOW}Consolidation mode is disabled, '
                    f'but there are {total_jobs} jobs from previous '
                    'consolidation mode. Reset the `jobs.controller.'
                    'consolidation_mode` to `true` and run `sky jobs queue` '
                    'to see those jobs. Switching to normal mode will '
                    f'lose the job history.{colorama.Style.RESET_ALL}')


# Whether to use consolidation mode or not. When this is enabled, the managed
# jobs controller will not be running on a separate cluster, but locally on the
# API Server. Under the hood, we submit the job monitoring logic as processes
# directly in the API Server.
# Use LRU Cache so that the check is only done once.
@annotations.lru_cache(scope='request', maxsize=2)
def is_consolidation_mode(on_api_restart: bool = False) -> bool:
    if os.environ.get(constants.OVERRIDE_CONSOLIDATION_MODE) is not None:
        return True

    config_consolidation_mode = skypilot_config.get_nested(
        ('jobs', 'controller', 'consolidation_mode'), default_value=False)

    signal_file = pathlib.Path(
        _JOBS_CONSOLIDATION_RELOADED_SIGNAL_FILE).expanduser()

    if on_api_restart:
        if config_consolidation_mode:
            signal_file.touch()
    else:
        restart_signal_file_exists = signal_file.exists()
        if not restart_signal_file_exists:
            if config_consolidation_mode:
                logger.warning(f'{colorama.Fore.YELLOW}Consolidation mode for '
                               'managed jobs is enabled in the server config, '
                               'but the API server has not been restarted yet. '
                               'Please restart the API server to enable it.'
                               f'{colorama.Style.RESET_ALL}')
                return False
        elif not config_consolidation_mode:
            # Cleanup the signal file if the consolidation mode is disabled in
            # the config. This allow the user to disable the consolidation mode
            # without restarting the API server.
            signal_file.unlink()

    # We should only do this check on API server, as the controller will not
    # have related config and will always seemingly disabled for consolidation
    # mode. Check #6611 for more details.
    if os.environ.get(constants.ENV_VAR_IS_SKYPILOT_SERVER) is not None:
        _validate_consolidation_mode_config(config_consolidation_mode)
    return config_consolidation_mode


def ha_recovery_for_consolidation_mode() -> None:
    """Recovery logic for consolidation mode.

    This should only be called from the managed-job-status-refresh-daemon, due
    so that we have correct ordering recovery -> controller start -> job status
    updates. This also should ensure correct operation during a rolling update.
    """
    # No setup recovery is needed in consolidation mode, as the API server
    # already has all runtime installed. Directly start jobs recovery here.
    # Refers to sky/templates/kubernetes-ray.yml.j2 for more details.
    scheduler.maybe_start_controllers()
    with open(constants.HA_PERSISTENT_RECOVERY_LOG_PATH.format('jobs_'),
              'a',
              encoding='utf-8') as f:
        start = time.time()
        f.write(f'Starting HA recovery at {datetime.now()}\n')
        jobs, _ = managed_job_state.get_managed_jobs_with_filters(fields=[
            'job_id', 'controller_pid', 'controller_pid_started_at',
            'schedule_state', 'status'
        ])
        for job in jobs:
            job_id = job['job_id']
            controller_pid = job['controller_pid']
            controller_pid_started_at = job.get('controller_pid_started_at')

            # In consolidation mode, it is possible that only the API server
            # process is restarted, and the controller process is not. In such
            # case, we don't need to do anything and the controller process will
            # just keep running. However, in most cases, the controller process
            # will also be stopped - either by a pod restart in k8s API server,
            # or by `sky api stop`, which will stop controllers.
            # TODO(cooperc): Make sure we cannot have a controller process
            # running across API server restarts for consistency.
            if controller_pid is not None:
                try:
                    # Note: We provide the legacy job id to the
                    # controller_process_alive just in case, but we shouldn't
                    # have a running legacy job controller process at this point
                    if controller_process_alive(
                            managed_job_state.ControllerPidRecord(
                                pid=controller_pid,
                                started_at=controller_pid_started_at), job_id):
                        message = (f'Controller pid {controller_pid} for '
                                   f'job {job_id} is still running. '
                                   'Skipping recovery.\n')
                        logger.debug(message)
                        f.write(message)
                        continue
                except Exception:  # pylint: disable=broad-except
                    # _controller_process_alive may raise if psutil fails; we
                    # should not crash the recovery logic because of this.
                    message = ('Error checking controller pid '
                               f'{controller_pid} for job {job_id}\n')
                    logger.warning(message, exc_info=True)
                    f.write(message)

            # Controller process is not set or not alive.
            if job['schedule_state'] not in [
                    managed_job_state.ManagedJobScheduleState.DONE,
                    managed_job_state.ManagedJobScheduleState.WAITING,
                    # INACTIVE job may be mid-submission, don't set to WAITING.
                    managed_job_state.ManagedJobScheduleState.INACTIVE,
            ]:
                managed_job_state.reset_job_for_recovery(job_id)
                message = (f'Job {job_id} completed recovery at '
                           f'{datetime.now()}\n')
                logger.info(message)
                f.write(message)
        f.write(f'HA recovery completed at {datetime.now()}\n')
        f.write(f'Total recovery time: {time.time() - start} seconds\n')


async def get_job_status(
    backend: 'backends.CloudVmRayBackend', cluster_name: str,
    job_id: Optional[int]
) -> Tuple[Optional['job_lib.JobStatus'], Optional[str]]:
    """Check the status of the job running on a managed job cluster.

    It can be None, INIT, RUNNING, SUCCEEDED, FAILED, FAILED_DRIVER,
    FAILED_SETUP or CANCELLED.

    Returns:
        job_status: The status of the job.
        transient_error_reason: None if successful or fatal error; otherwise,
            the detailed reason for the transient error.
    """
    # TODO(zhwu, cooperc): Make this get job status aware of cluster status, so
    # that it can exit retry early if the cluster is down.
    # TODO(luca) make this async
    handle = await asyncio.to_thread(
        global_user_state.get_handle_from_cluster_name, cluster_name)
    if handle is None:
        # This can happen if the cluster was preempted and background status
        # refresh already noticed and cleaned it up.
        logger.info(f'Cluster {cluster_name} not found.')
        return None, None
    assert isinstance(handle, backends.CloudVmRayResourceHandle), handle
    job_ids = None if job_id is None else [job_id]
    try:
        logger.info('=== Checking the job status... ===')
        statuses = await asyncio.wait_for(
            asyncio.to_thread(backend.get_job_status,
                              handle,
                              job_ids=job_ids,
                              stream_logs=False),
            timeout=_JOB_STATUS_FETCH_TIMEOUT_SECONDS)
        status = list(statuses.values())[0]
        if status is None:
            logger.info('No job found.')
        else:
            logger.info(f'Job status: {status}')
        logger.info('=' * 34)
        return status, None
    except (exceptions.CommandError, grpc.RpcError, grpc.FutureTimeoutError,
            ValueError, TypeError, asyncio.TimeoutError) as e:
        # Note: Each of these exceptions has some additional conditions to
        # limit how we handle it and whether or not we catch it.
        potential_transient_error_reason = None
        if isinstance(e, exceptions.CommandError):
            returncode = e.returncode
            potential_transient_error_reason = (f'Returncode: {returncode}. '
                                                f'{e.detailed_reason}')
        elif isinstance(e, grpc.RpcError):
            potential_transient_error_reason = e.details()
        elif isinstance(e, grpc.FutureTimeoutError):
            potential_transient_error_reason = 'grpc timeout'
        elif isinstance(e, asyncio.TimeoutError):
            potential_transient_error_reason = (
                'Job status check timed out after '
                f'{_JOB_STATUS_FETCH_TIMEOUT_SECONDS}s')
        # TODO(cooperc): Gracefully handle these exceptions in the backend.
        elif isinstance(e, ValueError):
            # If the cluster yaml is deleted in the middle of getting the
            # SSH credentials, we could see this. See
            # sky/global_user_state.py get_cluster_yaml_dict.
            if re.search(r'Cluster yaml .* not found', str(e)):
                potential_transient_error_reason = 'Cluster yaml was deleted'
            else:
                raise
        elif isinstance(e, TypeError):
            # We will grab the SSH credentials from the cluster yaml, but if
            # handle.cluster_yaml is None, we will just return an empty dict
            # for the credentials. See
            # backend_utils.ssh_credential_from_yaml. Then, the credentials
            # are passed as kwargs to SSHCommandRunner.__init__ - see
            # cloud_vm_ray_backend.get_command_runners. So we can hit this
            # TypeError if the cluster yaml is removed from the handle right
            # when we pull it before the cluster is fully deleted.
            error_msg_to_check = (
                'SSHCommandRunner.__init__() missing 2 required positional '
                'arguments: \'ssh_user\' and \'ssh_private_key\'')
            if str(e) == error_msg_to_check:
                potential_transient_error_reason = ('SSH credentials were '
                                                    'already cleaned up')
            else:
                raise
        return None, potential_transient_error_reason


def controller_process_alive(record: managed_job_state.ControllerPidRecord,
                             legacy_job_id: Optional[int] = None,
                             quiet: bool = True) -> bool:
    """Check if the controller process is alive.

    If legacy_job_id is provided, this will also return True for a legacy
    single-job controller process with that job id, based on the cmdline. This
    is how the old check worked before #7051.
    """
    try:
        process = psutil.Process(record.pid)

        if record.started_at is not None:
            if process.create_time() != record.started_at:
                if not quiet:
                    logger.debug(f'Controller process {record.pid} has started '
                                 f'at {record.started_at} but process has '
                                 f'started at {process.create_time()}')
                return False
        else:
            # If we can't check the create_time try to check the cmdline instead
            cmd_str = ' '.join(process.cmdline())
            # pylint: disable=line-too-long
            # Pre-#7051 cmdline: /path/to/python -u -m sky.jobs.controller <dag.yaml_path> --job-id <job_id>
            # Post-#7051 cmdline: /path/to/python -u -msky.jobs.controller
            # pylint: enable=line-too-long
            if ('-m sky.jobs.controller' not in cmd_str and
                    '-msky.jobs.controller' not in cmd_str):
                if not quiet:
                    logger.debug(f'Process {record.pid} is not a controller '
                                 'process - missing "-m sky.jobs.controller" '
                                 f'from cmdline: {cmd_str}')
                return False
            if (legacy_job_id is not None and '--job-id' in cmd_str and
                    f'--job-id {legacy_job_id}' not in cmd_str):
                if not quiet:
                    logger.debug(f'Controller process {record.pid} has the '
                                 f'wrong --job-id (expected {legacy_job_id}) '
                                 f'in cmdline: {cmd_str}')
                return False

            # On linux, psutil.Process(pid) will return a valid process object
            # even if the pid is actually a thread ID within the process. This
            # hugely inflates the number of valid-looking pids, increasing the
            # chance that we will falsely believe a controller is alive. The pid
            # file should never contain thread IDs, just process IDs. We can
            # check this with psutil.pid_exists(pid), which is false for TIDs.
            # See pid_exists in psutil/_pslinux.py
            if not psutil.pid_exists(record.pid):
                if not quiet:
                    logger.debug(
                        f'Controller process {record.pid} is not a valid '
                        'process id.')
                return False

        return process.is_running()

    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess,
            OSError) as e:
        if not quiet:
            logger.debug(f'Controller process {record.pid} is not running: {e}')
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
    # This signal file suggests that the controller is recovering from a
    # failure. See sky/templates/kubernetes-ray.yml.j2 for more details.
    # When restarting the controller processes, we don't want this event to
    # set the job status to FAILED_CONTROLLER.
    # TODO(tian): Change this to restart the controller process. For now we
    # disabled it when recovering because we want to avoid caveats of infinite
    # restart of last controller process that fully occupied the controller VM.
    if os.path.exists(
            os.path.expanduser(
                constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE)):
        return

    def _cleanup_job_clusters(job_id: int) -> Optional[str]:
        """Clean up clusters for a job. Returns error message if any.

        This function should not throw any exception. If it fails, it will
        capture the error message, and log/return it.
        """
        error_msg = None
        tasks = managed_job_state.get_managed_job_tasks(job_id)
        for task in tasks:
            pool = task.get('pool', None)
            if pool is None:
                task_name = task['job_name']
                cluster_name = generate_managed_job_cluster_name(
                    task_name, job_id)
            else:
                cluster_name, _ = (
                    managed_job_state.get_pool_submit_info(job_id))
            handle = global_user_state.get_handle_from_cluster_name(
                cluster_name)
            if handle is not None:
                try:
                    if pool is None:
                        terminate_cluster(cluster_name)
                except Exception as e:  # pylint: disable=broad-except
                    error_msg = (
                        f'Failed to terminate cluster {cluster_name}: '
                        f'{common_utils.format_exception(e, use_bracket=True)}')
                    logger.exception(error_msg, exc_info=e)
        return error_msg

    # Get jobs that need checking (non-terminal or not DONE)
    job_ids = managed_job_state.get_jobs_to_check_status(job_id)
    if not job_ids:
        # job_id is already terminal, or if job_id is None, there are no jobs
        # that need to be checked.
        return

    for job_id in job_ids:
        assert job_id is not None
        tasks = managed_job_state.get_managed_job_tasks(job_id)
        # Note: controller_pid and schedule_state are in the job_info table
        # which is joined to the spot table, so all tasks with the same job_id
        # will have the same value for these columns. This is what lets us just
        # take tasks[0]['controller_pid'] and tasks[0]['schedule_state'].
        schedule_state = tasks[0]['schedule_state']

        # Handle jobs with schedule state (non-legacy jobs):
        pid = tasks[0]['controller_pid']
        pid_started_at = tasks[0].get('controller_pid_started_at')
        if schedule_state == managed_job_state.ManagedJobScheduleState.DONE:
            # There are two cases where we could get a job that is DONE.
            # 1. At query time (get_jobs_to_check_status), the job was not yet
            #    DONE, but since then (before get_managed_job_tasks is called)
            #    it has hit a terminal status, marked itself done, and exited.
            #    This is fine.
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
            if controller_process_alive(
                    managed_job_state.ControllerPidRecord(
                        pid=pid, started_at=pid_started_at), job_id):
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
                      job_id: Optional[int], get_end_time: bool) -> float:
    """Get the submitted/ended time of the job."""
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    assert handle is not None, (
        f'handle for cluster {cluster_name!r} should not be None')
    if handle.is_grpc_enabled_with_flag:
        try:
            if get_end_time:
                end_ts_request = jobsv1_pb2.GetJobEndedTimestampRequest(
                    job_id=job_id)
                end_ts_response = backend_utils.invoke_skylet_with_retries(
                    lambda: cloud_vm_ray_backend.SkyletClient(
                        handle.get_grpc_channel()).get_job_ended_timestamp(
                            end_ts_request))
                return end_ts_response.timestamp
            else:
                submit_ts_request = jobsv1_pb2.GetJobSubmittedTimestampRequest(
                    job_id=job_id)
                submit_ts_response = backend_utils.invoke_skylet_with_retries(
                    lambda: cloud_vm_ray_backend.SkyletClient(
                        handle.get_grpc_channel()).get_job_submitted_timestamp(
                            submit_ts_request))
                return submit_ts_response.timestamp
        except exceptions.SkyletMethodNotImplementedError:
            pass

    code = (job_lib.JobLibCodeGen.get_job_submitted_or_ended_timestamp_payload(
        job_id=job_id, get_ended_time=get_end_time))
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
                            cluster_name: str, job_id: Optional[int]) -> float:
    """Try to get the end time of the job.

    If the job is preempted or we can't connect to the instance for whatever
    reason, fall back to the current time.
    """
    try:
        return get_job_timestamp(backend,
                                 cluster_name,
                                 job_id=job_id,
                                 get_end_time=True)
    except (exceptions.CommandError, grpc.RpcError,
            grpc.FutureTimeoutError) as e:
        if isinstance(e, exceptions.CommandError) and e.returncode == 255 or \
                (isinstance(e, grpc.RpcError) and e.code() in [
                    grpc.StatusCode.UNAVAILABLE,
                    grpc.StatusCode.DEADLINE_EXCEEDED,
                ]) or isinstance(e, grpc.FutureTimeoutError):
            # Failed to connect - probably the instance was preempted since the
            # job completed. We shouldn't crash here, so just log and use the
            # current time.
            logger.info(f'Failed to connect to the instance {cluster_name} '
                        'since the job completed. Assuming the instance '
                        'was preempted.')
            return time.time()
        else:
            raise


def event_callback_func(
        job_id: int, task_id: Optional[int],
        task: Optional['sky.Task']) -> managed_job_state.AsyncCallbackType:
    """Run event callback for the task."""

    def callback_func(status: str):
        event_callback = task.event_callback if task else None
        if event_callback is None or task is None:
            return
        event_callback = event_callback.strip()
        pool = managed_job_state.get_pool_from_job_id(job_id)
        if pool is not None:
            cluster_name, _ = (managed_job_state.get_pool_submit_info(job_id))
        else:
            cluster_name = generate_managed_job_cluster_name(
                task.name, job_id) if task.name else None
        logger.info(f'=== START: event callback for {status!r} ===')
        log_path = os.path.join(constants.SKY_LOGS_DIRECTORY,
                                'managed_job_event',
                                f'jobs-callback-{job_id}-{task_id}.log')
        env_vars = task.envs.copy() if task.envs else {}
        env_vars.update(
            dict(
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
        result = log_lib.run_bash_command_with_log(bash_command=event_callback,
                                                   log_path=log_path,
                                                   env_vars=env_vars)
        logger.info(
            f'Bash:{event_callback},log_path:{log_path},result:{result}')
        logger.info(f'=== END: event callback for {status!r} ===')

    async def async_callback_func(status: str):
        return await asyncio.to_thread(callback_func, status)

    return async_callback_func


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
                      all_users: bool = False,
                      current_workspace: Optional[str] = None,
                      user_hash: Optional[str] = None) -> str:
    """Cancel jobs by id.

    If job_ids is None, cancel all jobs.
    """
    if job_ids is None:
        job_ids = managed_job_state.get_nonterminal_job_ids_by_name(
            None, user_hash, all_users)
    job_ids = list(set(job_ids))
    if not job_ids:
        return 'No job to cancel.'
    if current_workspace is None:
        current_workspace = constants.SKYPILOT_DEFAULT_WORKSPACE

    cancelled_job_ids: List[int] = []
    wrong_workspace_job_ids: List[int] = []
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
        elif job_status == managed_job_state.ManagedJobStatus.PENDING:
            # the "if PENDING" is a short circuit, this will be atomic.
            cancelled = managed_job_state.set_pending_cancelled(job_id)
            if cancelled:
                cancelled_job_ids.append(job_id)
                continue

        update_managed_jobs_statuses(job_id)

        job_workspace = managed_job_state.get_workspace(job_id)
        if current_workspace is not None and job_workspace != current_workspace:
            wrong_workspace_job_ids.append(job_id)
            continue

        if managed_job_state.is_legacy_controller_process(job_id):
            # The job is running on a legacy single-job controller process.
            # TODO(cooperc): Remove this handling for 0.13.0

            # Send the signal to the jobs controller.
            signal_file = (pathlib.Path(
                managed_job_constants.SIGNAL_FILE_PREFIX.format(job_id)))
            # Filelock is needed to prevent race condition between signal
            # check/removal and signal writing.
            with filelock.FileLock(str(signal_file) + '.lock'):
                with signal_file.open('w', encoding='utf-8') as f:
                    f.write(UserSignal.CANCEL.value)
                    f.flush()
        else:
            # New controller process.
            try:
                signal_file = pathlib.Path(
                    managed_job_constants.CONSOLIDATED_SIGNAL_PATH, f'{job_id}')
                signal_file.touch()
            except OSError as e:
                logger.error(f'Failed to cancel job {job_id}: {e}')
                # Don't add it to the to be cancelled job ids
                continue

        cancelled_job_ids.append(job_id)

    wrong_workspace_job_str = ''
    if wrong_workspace_job_ids:
        plural = 's' if len(wrong_workspace_job_ids) > 1 else ''
        plural_verb = 'are' if len(wrong_workspace_job_ids) > 1 else 'is'
        wrong_workspace_job_str = (
            f' Job{plural} with ID{plural}'
            f' {", ".join(map(str, wrong_workspace_job_ids))} '
            f'{plural_verb} skipped as they are not in the active workspace '
            f'{current_workspace!r}. Check the workspace of the job with: '
            f'sky jobs queue')

    if not cancelled_job_ids:
        return f'No job to cancel.{wrong_workspace_job_str}'
    identity_str = f'Job with ID {cancelled_job_ids[0]} is'
    if len(cancelled_job_ids) > 1:
        cancelled_job_ids_str = ', '.join(map(str, cancelled_job_ids))
        identity_str = f'Jobs with IDs {cancelled_job_ids_str} are'

    msg = f'{identity_str} scheduled to be cancelled.{wrong_workspace_job_str}'
    return msg


def cancel_job_by_name(job_name: str,
                       current_workspace: Optional[str] = None) -> str:
    """Cancel a job by name."""
    job_ids = managed_job_state.get_nonterminal_job_ids_by_name(job_name)
    if not job_ids:
        return f'No running job found with name {job_name!r}.'
    if len(job_ids) > 1:
        return (f'{colorama.Fore.RED}Multiple running jobs found '
                f'with name {job_name!r}.\n'
                f'Job IDs: {job_ids}{colorama.Style.RESET_ALL}')
    msg = cancel_jobs_by_id(job_ids, current_workspace=current_workspace)
    return f'{job_name!r} {msg}'


def cancel_jobs_by_pool(pool_name: str,
                        current_workspace: Optional[str] = None) -> str:
    """Cancel all jobs in a pool."""
    job_ids = managed_job_state.get_nonterminal_job_ids_by_pool(pool_name)
    if not job_ids:
        return f'No running job found in pool {pool_name!r}.'
    return cancel_jobs_by_id(job_ids, current_workspace=current_workspace)


def controller_log_file_for_job(job_id: int,
                                create_if_not_exists: bool = False) -> str:
    log_dir = os.path.expanduser(managed_job_constants.JOBS_CONTROLLER_LOGS_DIR)
    if create_if_not_exists:
        os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f'{job_id}.log')


def stream_logs_by_id(
        job_id: int,
        follow: bool = True,
        tail: Optional[int] = None,
        task: Optional[Union[str, int]] = None) -> Tuple[str, int]:
    """Stream logs by job id.

    Args:
        job_id: The job ID to stream logs for.
        follow: Whether to follow the logs.
        tail: Number of lines to tail from the end of the log file.
        task: Task identifier to view logs for a specific task in a JobGroup.
            If an int, it is treated as a task ID. If a str, it is treated as
            a task name. If None, logs for all tasks are shown.

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

    def matches_task_filter(task_id: int, task_name: str,
                            task_filter: Optional[Union[str, int]]) -> bool:
        """Check if a task matches the task filter.

        If task_filter is an int, it is matched against task_id.
        If task_filter is a str, it is matched against task_name.
        """
        if task_filter is None:
            return True
        if isinstance(task_filter, int):
            return task_id == task_filter
        # task_filter is a str, match by task name
        return task_name == task_filter

    msg = _JOB_WAITING_STATUS_MESSAGE.format(status_str='', job_id=job_id)
    status_display = rich_utils.safe_status(msg)
    num_tasks = managed_job_state.get_num_tasks(job_id)

    # Check if job exists - if num_tasks is 0, the job doesn't exist
    if num_tasks == 0:
        return (f'Job {job_id} not found.', exceptions.JobExitCode.NOT_FOUND)

    # Resolve task filter to a specific task_id if provided
    # This is used for running jobs to stream logs from the correct task
    filtered_task_id: Optional[int] = None
    if task is not None:
        task_info = managed_job_state.get_all_task_ids_names_statuses_logs(
            job_id)
        for t_id, t_name, _, _, _ in task_info:
            if matches_task_filter(t_id, t_name, task):
                filtered_task_id = t_id
                break
        if filtered_task_id is None:
            valid_range = f'0-{num_tasks - 1}' if num_tasks > 1 else '0'
            return (f'No task found matching {task!r} in job {job_id}. '
                    f'Valid task IDs are {valid_range}.',
                    exceptions.JobExitCode.NOT_FOUND)

    with status_display:
        prev_msg = msg
        while (managed_job_status :=
               managed_job_state.get_status(job_id)) is None:
            time.sleep(1)

        # Show hint about per-task filtering when there are multiple tasks
        if num_tasks > 1 and task is None:
            print(f'{colorama.Fore.CYAN}Hint: This job has {num_tasks} tasks. '
                  f'Use \'sky jobs logs {job_id} TASK\' to view logs for a '
                  f'specific task (TASK can be task ID or name).'
                  f'{colorama.Style.RESET_ALL}')

        if not should_keep_logging(managed_job_status):
            job_msg = ''
            if managed_job_status.is_failed():
                job_msg = ('\nFailure reason: '
                           f'{managed_job_state.get_failure_reason(job_id)}')
            log_file_ever_existed = False
            task_info = managed_job_state.get_all_task_ids_names_statuses_logs(
                job_id)
            total_tasks = len(task_info)
            # Filter tasks if task filter is specified
            if task is not None:
                task_info = [
                    t for t in task_info
                    if matches_task_filter(t[0], t[1], task)
                ]
                if not task_info:
                    valid_range = (f'0-{total_tasks - 1}'
                                   if total_tasks > 1 else '0')
                    return (f'No task found matching {task!r} in job {job_id}. '
                            f'Valid task IDs are {valid_range}.',
                            exceptions.JobExitCode.NOT_FOUND)
            num_tasks = len(task_info)
            for (task_id, task_name, task_status, log_file,
                 logs_cleaned_at) in task_info:
                if log_file:
                    log_file_ever_existed = True
                    if logs_cleaned_at is not None:
                        ts_str = datetime.fromtimestamp(
                            logs_cleaned_at).strftime('%Y-%m-%d %H:%M:%S')
                        print(f'Task {task_name}({task_id}) log has been '
                              f'cleaned at {ts_str}.')
                        continue
                    task_str = (f'Task {task_name}({task_id})'
                                if task_name else f'Task {task_id}')
                    # Show task header when multiple tasks OR when filtering
                    if num_tasks > 1 or task is not None:
                        print(f'=== {task_str} ===')
                    with open(os.path.expanduser(log_file),
                              'r',
                              encoding='utf-8') as f:
                        # Stream the logs to the console without reading the
                        # whole file into memory.
                        start_streaming = False
                        read_from: Union[TextIO, Deque[str]] = f
                        if tail is not None:
                            assert tail > 0
                            # Read only the last 'tail' lines using deque
                            read_from = collections.deque(f, maxlen=tail)
                            # We set start_streaming to True here in case
                            # truncating the log file removes the line that
                            # contains LOG_FILE_START_STREAMING_AT. This does
                            # not cause issues for log files shorter than tail
                            # because tail_logs in sky/skylet/log_lib.py also
                            # handles LOG_FILE_START_STREAMING_AT.
                            start_streaming = True
                        for line in read_from:
                            if log_lib.LOG_FILE_START_STREAMING_AT in line:
                                start_streaming = True
                            if start_streaming:
                                print(line, end='', flush=True)
                    # Show task finished message for multi-task or filtering
                    if num_tasks > 1 or task is not None:
                        # Add the "Task finished" message for terminal states
                        if task_status.is_terminal():
                            print(ux_utils.finishing_message(
                                f'{task_str} finished '
                                f'(status: {task_status.value}).'),
                                  flush=True)
            if log_file_ever_existed:
                # Add the "Job finished" message for terminal states
                if managed_job_status.is_terminal():
                    print(ux_utils.finishing_message(
                        f'Job finished (status: {managed_job_status.value}).'),
                          flush=True)
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

        # If a task filter was specified, use the filtered task_id instead of
        # the latest task_id. This allows viewing logs for a specific task in
        # a JobGroup with parallel execution.
        if filtered_task_id is not None:
            task_id = filtered_task_id

        # We wait for managed_job_status to be not None above. Once we see that
        # it's not None, we don't expect it to every become None again.
        assert managed_job_status is not None, (job_id, task_id,
                                                managed_job_status)

        while should_keep_logging(managed_job_status):
            handle = None
            job_id_to_tail = None
            if task_id is not None:
                pool = managed_job_state.get_pool_from_job_id(job_id)
                if pool is not None:
                    cluster_name, job_id_to_tail = (
                        managed_job_state.get_pool_submit_info(job_id))
                else:
                    task_name = managed_job_state.get_task_name(job_id, task_id)
                    cluster_name = generate_managed_job_cluster_name(
                        task_name, job_id)
                if cluster_name is not None:
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
                # Preserve filtered task_id if specified
                if filtered_task_id is not None:
                    task_id = filtered_task_id
                assert managed_job_status is not None, (job_id, task_id,
                                                        managed_job_status)
                continue
            assert (managed_job_status ==
                    managed_job_state.ManagedJobStatus.RUNNING)
            assert isinstance(handle, backends.CloudVmRayResourceHandle), handle
            status_display.stop()
            tail_param = tail if tail is not None else 0
            returncode = backend.tail_logs(handle,
                                           job_id=job_id_to_tail,
                                           managed_job_id=job_id,
                                           follow=follow,
                                           tail=tail_param)
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

                    # If a task filter was specified, we're done with the
                    # specific task - don't wait for other tasks.
                    if filtered_task_id is not None:
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
                follow: bool = True,
                tail: Optional[int] = None,
                task: Optional[Union[str, int]] = None) -> Tuple[str, int]:
    """Stream logs by job id or job name.

    Args:
        job_id: The job ID to stream logs for.
        job_name: The job name to stream logs for.
        controller: Whether to stream controller logs.
        follow: Whether to follow the logs.
        tail: Number of lines to tail from the end of the log file.
        task: Task identifier to view logs for a specific task in a JobGroup.
            If an int, it is treated as a task ID. If a str, it is treated as
            a task name. If None, logs for all tasks are shown.

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
            managed_jobs, _ = managed_job_state.get_managed_jobs_with_filters(
                name_match=job_name, fields=['job_id', 'job_name', 'status'])
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

        controller_log_path = controller_log_file_for_job(job_id)
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
            read_from: Union[TextIO, Deque[str]] = f
            if tail is not None:
                assert tail > 0
                # Read only the last 'tail' lines efficiently using deque
                read_from = collections.deque(f, maxlen=tail)
            for line in read_from:
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

    return stream_logs_by_id(job_id, follow, tail, task)


def dump_managed_job_queue(
    skip_finished: bool = False,
    accessible_workspaces: Optional[List[str]] = None,
    job_ids: Optional[List[int]] = None,
    workspace_match: Optional[str] = None,
    name_match: Optional[str] = None,
    pool_match: Optional[str] = None,
    page: Optional[int] = None,
    limit: Optional[int] = None,
    user_hashes: Optional[List[Optional[str]]] = None,
    statuses: Optional[List[str]] = None,
    fields: Optional[List[str]] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
) -> str:
    return message_utils.encode_payload(
        get_managed_job_queue(skip_finished, accessible_workspaces, job_ids,
                              workspace_match, name_match, pool_match, page,
                              limit, user_hashes, statuses, fields, sort_by,
                              sort_order))


def _update_fields(fields: List[str],) -> Tuple[List[str], bool]:
    """Update the fields list to include the necessary fields.

    Args:
        fields: The fields to update.

    It will:
    - Add the necessary dependent fields to the list.
    - Remove the fields that are not in the DB.
    - Determine if cluster handle is required.

    Returns:
        A tuple containing the updated fields and a boolean indicating if
        cluster handle is required.
    """
    cluster_handle_required = True
    if _cluster_handle_not_required(fields):
        cluster_handle_required = False
    # Copy the list to avoid modifying the original list
    new_fields = fields.copy()
    # status and job_id are always included
    if 'status' not in new_fields:
        new_fields.append('status')
    if 'job_id' not in new_fields:
        new_fields.append('job_id')
    # user_hash is required if user_name is present
    if 'user_name' in new_fields and 'user_hash' not in new_fields:
        new_fields.append('user_hash')
    if 'job_duration' in new_fields:
        if 'last_recovered_at' not in new_fields:
            new_fields.append('last_recovered_at')
        if 'end_at' not in new_fields:
            new_fields.append('end_at')
    if 'job_name' in new_fields and 'task_name' not in new_fields:
        new_fields.append('task_name')
    if 'details' in new_fields:
        if 'schedule_state' not in new_fields:
            new_fields.append('schedule_state')
        if 'priority' not in new_fields:
            new_fields.append('priority')
        if 'failure_reason' not in new_fields:
            new_fields.append('failure_reason')
    if 'user_yaml' in new_fields:
        if 'original_user_yaml_path' not in new_fields:
            new_fields.append('original_user_yaml_path')
        if 'original_user_yaml_content' not in new_fields:
            new_fields.append('original_user_yaml_content')
    # is_job_group is derived from execution column
    if 'is_job_group' in fields:
        if 'execution' not in new_fields:
            new_fields.append('execution')
    if cluster_handle_required:
        if 'task_name' not in new_fields:
            new_fields.append('task_name')
        if 'current_cluster_name' not in new_fields:
            new_fields.append('current_cluster_name')
    # Remove _NON_DB_FIELDS
    # These fields have been mapped to the DB fields in the above code, so we
    # don't need to include them in the updated fields.
    for field in _NON_DB_FIELDS:
        if field in new_fields:
            new_fields.remove(field)
    return new_fields, cluster_handle_required


def _cluster_handle_not_required(fields: List[str]) -> bool:
    """Determine if cluster handle is not required.

    Args:
        fields: The fields to check if they contain any of the cluster handle
        fields.

    Returns:
        True if the fields do not contain any of the cluster handle fields,
        False otherwise.
    """
    return not any(field in fields for field in _CLUSTER_HANDLE_FIELDS)


def _format_job_details(*, job: Dict[str, Any],
                        highest_blocking_priority: int) -> None:
    """Add details about schedule state / backoff."""
    state_details = None
    if job['schedule_state'] == 'ALIVE_BACKOFF':
        state_details = 'In backoff, waiting for resources'
    elif job['schedule_state'] in ('WAITING', 'ALIVE_WAITING'):
        priority = job.get('priority')
        if (priority is not None and priority < highest_blocking_priority):
            # Job is lower priority than some other blocking job.
            state_details = 'Waiting for higher priority jobs to launch'
        else:
            state_details = 'Waiting for other jobs to launch'

    if state_details and job['failure_reason']:
        job['details'] = f'{state_details} - {job["failure_reason"]}'
    elif state_details:
        job['details'] = state_details
    elif job['failure_reason']:
        job['details'] = f'Failure: {job["failure_reason"]}'
    else:
        job['details'] = None


def _populate_job_records_from_handles(
        jobs_with_handle: List[Dict[str, Any]]) -> None:
    """Populate the job records from the handles."""
    for job_with_handle in jobs_with_handle:
        _populate_job_record_from_handle(
            job=job_with_handle['job'],
            cluster_name=job_with_handle['cluster_name'],
            handle=job_with_handle['handle'])


def _populate_job_record_from_handle(
        *, job: Dict[str, Any], cluster_name: str,
        handle: 'backends.CloudVmRayResourceHandle') -> None:
    """Populate the job record from the handle."""
    del cluster_name
    resources_str_simple, resources_str_full = (
        resources_utils.get_readable_resources_repr(handle,
                                                    simplified_only=False))
    assert resources_str_full is not None
    job['cluster_resources'] = resources_str_simple
    job['cluster_resources_full'] = resources_str_full
    job['cloud'] = str(handle.launched_resources.cloud)
    job['region'] = handle.launched_resources.region
    job['zone'] = handle.launched_resources.zone
    job['infra'] = infra_utils.InfraInfo(
        str(handle.launched_resources.cloud), handle.launched_resources.region,
        handle.launched_resources.zone).formatted_str()
    job['accelerators'] = handle.launched_resources.accelerators
    job['labels'] = handle.launched_resources.labels
    job['cluster_name_on_cloud'] = handle.cluster_name_on_cloud


def get_managed_job_queue(
    skip_finished: bool = False,
    accessible_workspaces: Optional[List[str]] = None,
    job_ids: Optional[List[int]] = None,
    workspace_match: Optional[str] = None,
    name_match: Optional[str] = None,
    pool_match: Optional[str] = None,
    page: Optional[int] = None,
    limit: Optional[int] = None,
    user_hashes: Optional[List[Optional[str]]] = None,
    statuses: Optional[List[str]] = None,
    fields: Optional[List[str]] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
) -> Dict[str, Any]:
    """Get the managed job queue.

    Args:
        skip_finished: Whether to skip finished jobs.
        accessible_workspaces: The accessible workspaces.
        job_ids: The job ids.
        workspace_match: The workspace name to match.
        name_match: The job name to match.
        pool_match: The pool name to match.
        page: The page number.
        limit: The limit number.
        user_hashes: The user hashes.
        statuses: The statuses.
        fields: The fields to include in the response.
        sort_by: The field to sort by.
        sort_order: The sort order ('asc' or 'desc').

    Returns:
        A dictionary containing the managed job queue.
    """
    cluster_handle_required = True
    updated_fields = None
    # The caller only need to specify the fields in the
    # `class ManagedJobRecord` in `response.py`, and the `_update_fields`
    # function will add the necessary dependent fields to the list, for
    # example, if the caller specifies `['user_name']`, the `_update_fields`
    # function will add `['user_hash']` to the list.
    if fields:
        updated_fields, cluster_handle_required = _update_fields(fields)

    total_no_filter = managed_job_state.get_managed_jobs_total()

    status_counts = managed_job_state.get_status_count_with_filters(
        fields=fields,
        job_ids=job_ids,
        accessible_workspaces=accessible_workspaces,
        workspace_match=workspace_match,
        name_match=name_match,
        pool_match=pool_match,
        user_hashes=user_hashes,
        skip_finished=skip_finished,
    )

    jobs, total = managed_job_state.get_managed_jobs_with_filters(
        fields=updated_fields,
        job_ids=job_ids,
        accessible_workspaces=accessible_workspaces,
        workspace_match=workspace_match,
        name_match=name_match,
        pool_match=pool_match,
        user_hashes=user_hashes,
        statuses=statuses,
        skip_finished=skip_finished,
        page=page,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    if cluster_handle_required:
        # Fetch the cluster name to handle map for managed clusters only.
        cluster_name_to_handle = (
            global_user_state.get_cluster_name_to_handle_map(is_managed=True))

    highest_blocking_priority = constants.MIN_PRIORITY
    if not fields or 'details' in fields:
        # Figure out what the highest priority blocking job is. We need to know
        # in order to determine if other jobs are blocked by a higher priority
        # job, or just by the limited controller resources.
        highest_blocking_priority = (
            managed_job_state.get_managed_jobs_highest_priority())

    jobs_with_handle = []
    for job in jobs:
        if not fields or 'job_duration' in fields:
            end_at = job['end_at']
            if end_at is None:
                end_at = time.time()

            job_submitted_at = job['last_recovered_at'] - job['job_duration']
            if job['status'] == managed_job_state.ManagedJobStatus.RECOVERING:
                # When job is recovering, the duration is exact
                # job['job_duration']
                job_duration = job['job_duration']
            elif job_submitted_at > 0:
                job_duration = end_at - job_submitted_at
            else:
                # When job_start_at <= 0, that means the last_recovered_at
                # is not set yet, i.e. the job is not started.
                job_duration = 0
            job['job_duration'] = job_duration
        job['status'] = job['status'].value
        if not fields or 'schedule_state' in fields:
            job['schedule_state'] = job['schedule_state'].value
        else:
            job['schedule_state'] = None

        if cluster_handle_required:
            cluster_name = job.get('current_cluster_name', None)
            if cluster_name is None:
                cluster_name = generate_managed_job_cluster_name(
                    job['task_name'], job['job_id'])
            handle = cluster_name_to_handle.get(
                cluster_name, None) if cluster_name is not None else None
            if isinstance(handle, backends.CloudVmRayResourceHandle):
                jobs_with_handle.append({
                    'job': job,
                    'handle': handle,
                    'cluster_name': cluster_name,
                })
            else:
                # FIXME(zongheng): display the last cached values for these.
                job['cluster_resources'] = '-'
                job['cluster_resources_full'] = '-'
                job['cloud'] = '-'
                job['region'] = '-'
                job['zone'] = '-'
                job['infra'] = '-'
                job['labels'] = None
                job['cluster_name_on_cloud'] = None

    _populate_job_records_from_handles(jobs_with_handle)

    for job in jobs:
        if not fields or 'details' in fields:
            _format_job_details(
                job=job, highest_blocking_priority=highest_blocking_priority)

        # Derive is_job_group from execution column
        job['is_job_group'] = (
            job.get('execution') == DagExecution.PARALLEL.value)

    return {
        'jobs': jobs,
        'total': total,
        'total_no_filter': total_no_filter,
        'status_counts': status_counts
    }


def filter_jobs(
    jobs: List[Dict[str, Any]],
    workspace_match: Optional[str],
    name_match: Optional[str],
    pool_match: Optional[str],
    page: Optional[int],
    limit: Optional[int],
    user_match: Optional[str] = None,
    enable_user_match: bool = False,
    statuses: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], int, Dict[str, int]]:
    """Filter jobs based on the given criteria.

    Args:
        jobs: List of jobs to filter.
        workspace_match: Workspace name to filter.
        name_match: Job name to filter.
        pool_match: Pool name to filter.
        page: Page to filter.
        limit: Limit to filter.
        user_match: User name to filter.
        enable_user_match: Whether to enable user match.
        statuses: Statuses to filter.

    Returns:
        List of filtered jobs
        Total number of jobs
        Dictionary of status counts
    """

    # TODO(hailong): refactor the whole function including the
    # `dump_managed_job_queue()` to use DB filtering.

    def _pattern_matches(job: Dict[str, Any], key: str,
                         pattern: Optional[str]) -> bool:
        if pattern is None:
            return True
        if key not in job:
            return False
        value = job[key]
        if not value:
            return False
        return pattern in str(value)

    def _handle_page_and_limit(
        result: List[Dict[str, Any]],
        page: Optional[int],
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        if page is None and limit is None:
            return result
        assert page is not None and limit is not None, (page, limit)
        # page starts from 1
        start = (page - 1) * limit
        end = min(start + limit, len(result))
        return result[start:end]

    status_counts: Dict[str, int] = collections.defaultdict(int)
    result = []
    checks = [
        ('workspace', workspace_match),
        ('job_name', name_match),
        ('pool', pool_match),
    ]
    if enable_user_match:
        checks.append(('user_name', user_match))

    for job in jobs:
        if not all(
                _pattern_matches(job, key, pattern) for key, pattern in checks):
            continue
        status_counts[job['status'].value] += 1
        if statuses:
            if job['status'].value not in statuses:
                continue
        result.append(job)

    total = len(result)

    return _handle_page_and_limit(result, page, limit), total, status_counts


def load_managed_job_queue(
    payload: str
) -> Tuple[List[Dict[str, Any]], int, ManagedJobQueueResultType, int, Dict[
        str, int]]:
    """Load job queue from json string."""
    result = message_utils.decode_payload(payload)
    result_type = ManagedJobQueueResultType.DICT
    status_counts: Dict[str, int] = {}
    if isinstance(result, dict):
        jobs: List[Dict[str, Any]] = result['jobs']
        total: int = result['total']
        status_counts = result.get('status_counts', {})
        total_no_filter: int = result.get('total_no_filter', total)
    else:
        jobs = result
        total = len(jobs)
        total_no_filter = total
        result_type = ManagedJobQueueResultType.LIST

    all_users = global_user_state.get_all_users()
    all_users_map = {user.id: user.name for user in all_users}
    for job in jobs:
        job['status'] = managed_job_state.ManagedJobStatus(job['status'])
        if 'user_hash' in job and job['user_hash'] is not None:
            # Skip jobs that do not have user_hash info.
            # TODO(cooperc): Remove check before 0.12.0.
            job['user_name'] = all_users_map.get(job['user_hash'])
    return jobs, total, result_type, total_no_filter, status_counts


def _get_job_status_from_tasks(
    job_tasks: Union[List[responses.ManagedJobRecord], List[Dict[str, Any]]]
) -> Tuple[managed_job_state.ManagedJobStatus, int]:
    """Get the current task status and the current task id for a job.

    For job groups with primary/auxiliary tasks, the job status is determined
    only by the primary tasks. If all primary tasks succeed, the job is
    considered successful even if auxiliary tasks were cancelled.
    """
    # Filter to only primary tasks for status determination.
    # is_primary_in_job_group: True/False for job groups, None for non-groups.
    # For non-job-groups (None), all tasks count for status.
    # For job groups, only tasks with is_primary_in_job_group=True count.
    primary_job_tasks = [
        t for t in job_tasks
        if t.get('is_primary_in_job_group') is None or  # Non-job-group
        t.get('is_primary_in_job_group') is True  # Primary task in job group
    ]
    # Use primary tasks for status; fall back to all tasks if none match
    job_tasks_for_status: Union[List[responses.ManagedJobRecord],
                                List[Dict[str, Any]]] = (primary_job_tasks
                                                         if primary_job_tasks
                                                         else job_tasks)

    managed_task_status = managed_job_state.ManagedJobStatus.SUCCEEDED
    current_task_id = 0
    for task in job_tasks_for_status:
        task_status = task['status']
        # Handle both enum and string status values
        if isinstance(task_status, str):
            task_status = managed_job_state.ManagedJobStatus(task_status)
        managed_task_status = task_status
        current_task_id = task['task_id']

        # Use the first non-succeeded status.
        if managed_task_status != managed_job_state.ManagedJobStatus.SUCCEEDED:
            # TODO(zhwu): we should not blindly use the first non-
            # succeeded as the status could be changed to PENDING
            # when going from one task to the next one, which can be
            # confusing.
            break
    return managed_task_status, current_task_id


@typing.overload
def format_job_table(
    tasks: List[Dict[str, Any]],
    show_all: bool,
    show_user: bool,
    return_rows: Literal[False] = False,
    pool_status: Optional[List[Dict[str, Any]]] = None,
    max_jobs: Optional[int] = None,
    job_status_counts: Optional[Dict[str, int]] = None,
) -> str:
    ...


@typing.overload
def format_job_table(
    tasks: List[Dict[str, Any]],
    show_all: bool,
    show_user: bool,
    return_rows: Literal[True],
    pool_status: Optional[List[Dict[str, Any]]] = None,
    max_jobs: Optional[int] = None,
    job_status_counts: Optional[Dict[str, int]] = None,
) -> List[List[str]]:
    ...


def format_job_table(
    tasks: List[Dict[str, Any]],
    show_all: bool,
    show_user: bool,
    return_rows: bool = False,
    pool_status: Optional[List[Dict[str, Any]]] = None,
    max_jobs: Optional[int] = None,
    job_status_counts: Optional[Dict[str, int]] = None,
) -> Union[str, List[List[str]]]:
    """Returns managed jobs as a formatted string.

    Args:
        jobs: A list of managed jobs.
        show_all: Whether to show all columns.
        max_jobs: The maximum number of jobs to show in the table.
        return_rows: If True, return the rows as a list of strings instead of
          all rows concatenated into a single string.
        pool_status: List of pool status dictionaries with replica_info.
        job_status_counts: The counts of each job status.

    Returns: A formatted string of managed jobs, if not `return_rows`; otherwise
      a list of "rows" (each of which is a list of str).
    """
    jobs = collections.defaultdict(list)
    # Check if the tasks have user information from kubernetes.
    # This is only used for sky status-kubernetes.
    tasks_have_k8s_user = any([task.get('user') for task in tasks])
    if max_jobs and tasks_have_k8s_user:
        raise ValueError('max_jobs is not supported when tasks have user info.')

    def get_hash(task):
        if tasks_have_k8s_user:
            return (task['user'], task['job_id'])
        return task['job_id']

    def _get_job_id_to_worker_map(
            pool_status: Optional[List[Dict[str, Any]]]) -> Dict[int, int]:
        """Create a mapping from job_id to worker replica_id.

        Args:
            pool_status: List of pool status dictionaries with replica_info.

        Returns:
            Dictionary mapping job_id to replica_id (worker ID).
        """
        job_to_worker: Dict[int, int] = {}
        if pool_status is None:
            return job_to_worker
        for pool in pool_status:
            replica_info = pool.get('replica_info', [])
            for replica in replica_info:
                used_by = replica.get('used_by')
                if used_by is not None:
                    for job_id in used_by:
                        job_to_worker[job_id] = replica.get('replica_id')
        return job_to_worker

    # Create mapping from job_id to worker replica_id
    job_to_worker = _get_job_id_to_worker_map(pool_status)

    for task in tasks:
        # The tasks within the same job_id are already sorted
        # by the task_id.
        jobs[get_hash(task)].append(task)

    workspaces = set()
    for job_tasks in jobs.values():
        workspaces.add(job_tasks[0].get('workspace',
                                        constants.SKYPILOT_DEFAULT_WORKSPACE))

    show_workspace = len(workspaces) > 1 or show_all

    user_cols: List[str] = []
    if show_user:
        user_cols = ['USER']
        if show_all:
            user_cols.append('USER_ID')

    columns = [
        'ID',
        'TASK',
        *(['WORKSPACE'] if show_workspace else []),
        'NAME',
        *user_cols,
        'REQUESTED',
        'SUBMITTED',
        'TOT. DURATION',
        'JOB DURATION',
        '#RECOVERIES',
        'STATUS',
        'POOL',
    ]
    if show_all:
        # TODO: move SCHED. STATE to a separate flag (e.g. --debug)
        columns += [
            'WORKER_CLUSTER',
            'WORKER_JOB_ID',
            'STARTED',
            'INFRA',
            'RESOURCES',
            'SCHED. STATE',
            'DETAILS',
            'GIT_COMMIT',
        ]
    if tasks_have_k8s_user:
        columns.insert(0, 'USER')
    job_table = log_utils.create_table(columns)

    status_counts: Dict[str, int] = collections.defaultdict(int)
    if job_status_counts:
        for status_value, count in job_status_counts.items():
            status = managed_job_state.ManagedJobStatus(status_value)
            if not status.is_terminal():
                status_counts[status_value] = count
    else:
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

    def generate_details(details: Optional[str],
                         failure_reason: Optional[str]) -> str:
        if details is not None:
            return details
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
        workspace = job_tasks[0].get('workspace',
                                     constants.SKYPILOT_DEFAULT_WORKSPACE)

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

            pool = job_tasks[0].get('pool')
            if pool is None:
                pool = '-'

            # Add worker information if job is assigned to a worker
            job_id = job_hash[1] if tasks_have_k8s_user else job_hash
            # job_id is now always an integer, use it to look up worker
            if job_id in job_to_worker and pool != '-':
                pool = f'{pool} (worker={job_to_worker[job_id]})'

            job_values = [
                job_id,
                '',
                *([''] if show_workspace else []),
                job_name,
                *user_values,
                '-',
                submitted,
                total_duration,
                job_duration,
                recovery_cnt,
                status_str,
                pool,
            ]
            if show_all:
                details = job_tasks[current_task_id].get('details')
                failure_reason = job_tasks[current_task_id]['failure_reason']
                job_values.extend([
                    '-',
                    '-',
                    '-',
                    '-',
                    '-',
                    job_tasks[0]['schedule_state'],
                    generate_details(details, failure_reason),
                    job_tasks[0].get('metadata', {}).get('git_commit', '-'),
                ])
            if tasks_have_k8s_user:
                job_values.insert(0, job_tasks[0].get('user', '-'))
            job_table.add_row(job_values)

        # Check if this is a job group with auxiliary tasks.
        # is_primary_in_job_group: True/False for job groups, None otherwise.
        # We show [P] markers only for job groups that have auxiliary tasks.
        has_auxiliary_tasks = any(
            t.get('is_primary_in_job_group') is False for t in job_tasks)

        for task in job_tasks:
            # The job['job_duration'] is already calculated in
            # dump_managed_job_queue().
            job_duration = log_utils.readable_time_duration(
                0, task['job_duration'], absolute=True)
            submitted = log_utils.readable_time_duration(task['submitted_at'])
            user_values = get_user_column_values(task)
            task_workspace = '-' if len(job_tasks) > 1 else workspace
            pool = task.get('pool')
            if pool is None:
                pool = '-'

            # Add worker information if task is assigned to a worker
            task_job_id = task['job_id']
            if task_job_id in job_to_worker and pool != '-':
                pool = f'{pool} (worker={job_to_worker[task_job_id]})'

            # Add [P] marker for primary tasks in job groups with auxiliaries
            task_name = task['task_name']
            if has_auxiliary_tasks and task.get('is_primary_in_job_group'):
                task_name = f'{task_name} [P]'

            values = [
                task['job_id'] if len(job_tasks) == 1 else ' \u21B3',
                task['task_id'] if len(job_tasks) > 1 else '-',
                *([task_workspace] if show_workspace else []),
                task_name,
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
                pool,
            ]
            if show_all:
                # schedule_state is only set at the job level, so if we have
                # more than one task, only display on the aggregated row.
                schedule_state = (task['schedule_state']
                                  if len(job_tasks) == 1 else '-')
                infra_str = task.get('infra')
                if infra_str is None:
                    cloud = task.get('cloud')
                    if cloud is None:
                        # Backward compatibility for old jobs controller without
                        # cloud info returned, we parse it from the cluster
                        # resources
                        # TODO(zhwu): remove this after 0.12.0
                        cloud = task['cluster_resources'].split('(')[0].split(
                            'x')[-1]
                        task['cluster_resources'] = task[
                            'cluster_resources'].replace(f'{cloud}(',
                                                         '(').replace(
                                                             'x ', 'x')
                    region = task['region']
                    zone = task.get('zone')
                    if cloud == '-':
                        cloud = None
                    if region == '-':
                        region = None
                    if zone == '-':
                        zone = None
                    infra_str = infra_utils.InfraInfo(cloud, region,
                                                      zone).formatted_str()
                values.extend([
                    task.get('current_cluster_name', '-'),
                    task.get('job_id_on_pool_cluster', '-'),
                    # STARTED
                    log_utils.readable_time_duration(task['start_at']),
                    infra_str,
                    task['cluster_resources'],
                    schedule_state,
                    generate_details(task.get('details'),
                                     task['failure_reason']),
                ])

                values.append(task.get('metadata', {}).get('git_commit', '-'))
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


def decode_managed_job_protos(
    job_protos: Iterable['managed_jobsv1_pb2.ManagedJobInfo']
) -> List[Dict[str, Any]]:
    """Decode job protos to dicts. Similar to load_managed_job_queue."""
    user_hash_to_user = global_user_state.get_users(
        set(job.user_hash for job in job_protos if job.user_hash))

    jobs = []
    for job_proto in job_protos:
        job_dict = _job_proto_to_dict(job_proto)
        user_hash = job_dict.get('user_hash', None)
        if user_hash is not None:
            # Skip jobs that do not have user_hash info.
            # TODO(cooperc): Remove check before 0.12.0.
            user = user_hash_to_user.get(user_hash, None)
            job_dict['user_name'] = user.name if user is not None else None
        jobs.append(job_dict)
    return jobs


def _job_proto_to_dict(
        job_proto: 'managed_jobsv1_pb2.ManagedJobInfo') -> Dict[str, Any]:
    job_dict = json_format.MessageToDict(
        job_proto,
        always_print_fields_with_no_presence=True,
        # Our API returns fields in snake_case.
        preserving_proto_field_name=True,
        use_integers_for_enums=True)
    for field in job_proto.DESCRIPTOR.fields:
        # Ensure optional fields are present with None values for
        # backwards compatibility with older clients.
        if field.has_presence and field.name not in job_dict:
            job_dict[field.name] = None
        # json_format.MessageToDict is meant for encoding to JSON,
        # and Protobuf encodes int64 as decimal strings in JSON,
        # so we need to convert them back to ints.
        # https://protobuf.dev/programming-guides/json/#field-representation
        if (field.type == descriptor.FieldDescriptor.TYPE_INT64 and
                job_dict.get(field.name) is not None):
            job_dict[field.name] = int(job_dict[field.name])
    job_dict['status'] = managed_job_state.ManagedJobStatus.from_protobuf(
        job_dict['status'])
    # For backwards compatibility, convert schedule_state to a string,
    # as we don't have the logic to handle it in our request
    # encoder/decoder, unlike status.
    schedule_state_enum = (
        managed_job_state.ManagedJobScheduleState.from_protobuf(
            job_dict['schedule_state']))
    job_dict['schedule_state'] = (schedule_state_enum.value
                                  if schedule_state_enum is not None else None)
    return job_dict


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

        # Plugins are only loaded for managed jobs version 13 and above.
        if managed_job_version >= 13:
            from sky.server import plugins
            plugins.load_plugins(plugins.ExtensionContext())
        """)

    @classmethod
    def get_job_table(
        cls,
        skip_finished: bool = False,
        accessible_workspaces: Optional[List[str]] = None,
        job_ids: Optional[List[int]] = None,
        workspace_match: Optional[str] = None,
        name_match: Optional[str] = None,
        pool_match: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
        user_hashes: Optional[List[Optional[str]]] = None,
        statuses: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
    ) -> str:
        code = textwrap.dedent(f"""\
        # Filter out is_primary_in_job_group for older controllers (< 15)
        _fields = {fields!r}
        if managed_job_version < 15 and _fields is not None:
            _fields = [f for f in _fields if f != 'is_primary_in_job_group']
        if managed_job_version < 9:
            # For backward compatibility, since filtering is not supported
            # before #6652.
            # TODO(hailong): Remove compatibility before 0.12.0
            job_table = utils.dump_managed_job_queue()
        elif managed_job_version < 10:
            job_table = utils.dump_managed_job_queue(
                                skip_finished={skip_finished},
                                accessible_workspaces={accessible_workspaces!r},
                                job_ids={job_ids!r},
                                workspace_match={workspace_match!r},
                                name_match={name_match!r},
                                pool_match={pool_match!r},
                                page={page!r},
                                limit={limit!r},
                                user_hashes={user_hashes!r})
        elif managed_job_version < 12:
            job_table = utils.dump_managed_job_queue(
                                skip_finished={skip_finished},
                                accessible_workspaces={accessible_workspaces!r},
                                job_ids={job_ids!r},
                                workspace_match={workspace_match!r},
                                name_match={name_match!r},
                                pool_match={pool_match!r},
                                page={page!r},
                                limit={limit!r},
                                user_hashes={user_hashes!r},
                                statuses={statuses!r})
        elif managed_job_version < 14:
            job_table = utils.dump_managed_job_queue(
                                skip_finished={skip_finished},
                                accessible_workspaces={accessible_workspaces!r},
                                job_ids={job_ids!r},
                                workspace_match={workspace_match!r},
                                name_match={name_match!r},
                                pool_match={pool_match!r},
                                page={page!r},
                                limit={limit!r},
                                user_hashes={user_hashes!r},
                                statuses={statuses!r},
                                fields=_fields)
        else:
            job_table = utils.dump_managed_job_queue(
                                skip_finished={skip_finished},
                                accessible_workspaces={accessible_workspaces!r},
                                job_ids={job_ids!r},
                                workspace_match={workspace_match!r},
                                name_match={name_match!r},
                                pool_match={pool_match!r},
                                page={page!r},
                                limit={limit!r},
                                user_hashes={user_hashes!r},
                                statuses={statuses!r},
                                fields=_fields,
                                sort_by={sort_by!r},
                                sort_order={sort_order!r})
        print(job_table, flush=True)
        """)
        return cls._build(code)

    @classmethod
    def cancel_jobs_by_id(cls,
                          job_ids: Optional[List[int]],
                          all_users: bool = False) -> str:
        active_workspace = skypilot_config.get_active_workspace()
        code = textwrap.dedent(f"""\
        if managed_job_version < 2:
            # For backward compatibility, since all_users is not supported
            # before #4787.
            # TODO(cooperc): Remove compatibility before 0.12.0
            msg = utils.cancel_jobs_by_id({job_ids})
        elif managed_job_version < 4:
            # For backward compatibility, since current_workspace is not
            # supported before #5660. Don't check the workspace.
            # TODO(zhwu): Remove compatibility before 0.12.0
            msg = utils.cancel_jobs_by_id({job_ids}, all_users={all_users})
        else:
            msg = utils.cancel_jobs_by_id({job_ids}, all_users={all_users},
                            current_workspace={active_workspace!r})
        print(msg, end="", flush=True)
        """)
        return cls._build(code)

    @classmethod
    def cancel_job_by_name(cls, job_name: str) -> str:
        active_workspace = skypilot_config.get_active_workspace()
        code = textwrap.dedent(f"""\
        if managed_job_version < 4:
            # For backward compatibility, since current_workspace is not
            # supported before #5660. Don't check the workspace.
            # TODO(zhwu): Remove compatibility before 0.12.0
            msg = utils.cancel_job_by_name({job_name!r})
        else:
            msg = utils.cancel_job_by_name({job_name!r}, {active_workspace!r})
        print(msg, end="", flush=True)
        """)
        return cls._build(code)

    @classmethod
    def cancel_jobs_by_pool(cls, pool_name: str) -> str:
        active_workspace = skypilot_config.get_active_workspace()
        code = textwrap.dedent(f"""\
            msg = utils.cancel_jobs_by_pool({pool_name!r}, {active_workspace!r})
            print(msg, end="", flush=True)
        """)
        return cls._build(code)

    @classmethod
    def get_version_and_job_table(cls) -> str:
        """Generate code to get controller version and raw job table."""
        code = textwrap.dedent("""\
        from sky.skylet import constants as controller_constants

        # Get controller version
        controller_version = controller_constants.SKYLET_VERSION
        print(f"controller_version:{controller_version}", flush=True)

        # Get and print raw job table (load_managed_job_queue can parse this directly)
        job_table = utils.dump_managed_job_queue()
        print(job_table, flush=True)
        """)
        return cls._build(code)

    @classmethod
    def get_version(cls) -> str:
        """Generate code to get controller version."""
        code = textwrap.dedent("""\
        from sky.skylet import constants as controller_constants

        # Get controller version
        controller_version = controller_constants.SKYLET_VERSION
        print(f"controller_version:{controller_version}", flush=True)
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
                    controller: bool = False,
                    tail: Optional[int] = None,
                    task: Optional[Union[str, int]] = None) -> str:
        code = textwrap.dedent(f"""\
        if managed_job_version < 6:
            # Versions before 6 did not support tail parameter
            result = utils.stream_logs(job_id={job_id!r}, job_name={job_name!r},
                                    follow={follow}, controller={controller})
        elif managed_job_version < 15:
            # Versions before 15 did not support task parameter
            result = utils.stream_logs(job_id={job_id!r}, job_name={job_name!r},
                                    follow={follow}, controller={controller}, tail={tail!r})
        else:
            result = utils.stream_logs(job_id={job_id!r}, job_name={job_name!r},
                                    follow={follow}, controller={controller}, tail={tail!r},
                                    task={task!r})
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
    def set_pending(cls,
                    job_id: int,
                    managed_job_dag: 'dag_lib.Dag',
                    workspace: str,
                    entrypoint: str,
                    user_hash: Optional[str] = None) -> str:
        dag_name = managed_job_dag.name
        pool = managed_job_dag.pool
        # Execution mode: 'parallel' for job groups, 'serial' for pipelines and
        # single jobs
        execution = (managed_job_dag.execution.value
                     if managed_job_dag.execution else DEFAULT_EXECUTION.value)
        # Add the managed job to queue table.
        code = textwrap.dedent(f"""\
            set_job_info_kwargs = {{'workspace': {workspace!r}}}
            if managed_job_version < 4:
                set_job_info_kwargs = {{}}
            if managed_job_version >= 5:
                set_job_info_kwargs['entrypoint'] = {entrypoint!r}
            if managed_job_version >= 8:
                from sky.serve import serve_state
                pool_hash = None
                if {pool!r} != None:
                    pool_hash = serve_state.get_service_hash({pool!r})
                set_job_info_kwargs['pool'] = {pool!r}
                set_job_info_kwargs['pool_hash'] = pool_hash
            if managed_job_version >= 11:
                set_job_info_kwargs['user_hash'] = {user_hash!r}
            if managed_job_version >= 15:
                set_job_info_kwargs['execution'] = {execution!r}
            managed_job_state.set_job_info(
                {job_id}, {dag_name!r}, **set_job_info_kwargs)
            """)
        for task_id, task in enumerate(managed_job_dag.tasks):
            resources_str = backend_utils.get_task_resources_str(
                task, is_managed_job=True)
            # For job groups, determine which tasks are primary vs auxiliary.
            # For non-job-groups, is_primary_in_job_group=None for all tasks.
            is_primary_in_job_group: Optional[bool] = None
            if managed_job_dag.is_job_group():
                is_primary_in_job_group = (
                    managed_job_dag.primary_tasks is None or
                    task.name in managed_job_dag.primary_tasks)
            code += textwrap.dedent(f"""\
                if managed_job_version < 7:
                    managed_job_state.set_pending({job_id}, {task_id},
                                    {task.name!r}, {resources_str!r})
                elif managed_job_version < 15:
                    managed_job_state.set_pending({job_id}, {task_id},
                                    {task.name!r}, {resources_str!r},
                                    {task.metadata_json!r})
                else:
                    managed_job_state.set_pending({job_id}, {task_id},
                                    {task.name!r}, {resources_str!r},
                                    {task.metadata_json!r},
                                    {is_primary_in_job_group!r})
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
