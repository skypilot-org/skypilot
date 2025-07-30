"""Controller: handles the life cycle of a managed job.

TODO(cooperc): Document lifecycle, and multiprocess layout.
"""
import asyncio
import logging
import os
import resource
import shutil
import sys
import time
import traceback
import typing
from typing import Dict, Optional, Set, Tuple

import dotenv
import psutil

# This import ensures backward compatibility. Controller processes may not have
# imported this module initially, but will attempt to import it during job
# termination on the fly. If a job was launched with an old SkyPilot runtime
# and a new job is launched with a newer runtime, the old job's termination
# will try to import code from a different SkyPilot runtime, causing exceptions.
# pylint: disable=unused-import
from sky import core
from sky import exceptions
from sky import sky_logging
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.data import data_utils
from sky.jobs import constants as jobs_constants
from sky.jobs import recovery_strategy
from sky.jobs import scheduler
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants
from sky.skylet import job_lib
from sky.usage import usage_lib
from sky.utils import common
from sky.utils import common_utils
from sky.utils import context
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import sky

logger = sky_logging.init_logger('sky.jobs.controller')

_background_tasks: Set[asyncio.Task] = set()
_background_tasks_lock: asyncio.Lock = asyncio.Lock()


async def create_background_task(coro: typing.Coroutine) -> None:
    """Create a background task and add it to the set of background tasks.

    Main reason we do this is since tasks are only held as a weak reference in
    the executor, we need to keep a strong reference to the task to avoid it
    being garbage collected.

    Args:
        coro: The coroutine to create a task for.
    """
    async with _background_tasks_lock:
        task = asyncio.create_task(coro)
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


def _get_dag_and_name(dag_yaml: str) -> Tuple['sky.Dag', str]:
    dag = dag_utils.load_chain_dag_from_yaml(dag_yaml)
    dag_name = dag.name
    assert dag_name is not None, dag
    return dag, dag_name


class JobsController:
    """Each jobs controller manages the life cycle of one managed job."""

    def __init__(
        self,
        job_id: int,
        dag_yaml: str,
        job_logger: logging.Logger,
        starting: Set[int],
        job_tasks_lock: asyncio.Lock,
    ) -> None:
        """Initialize a JobsController.

        Args:
            job_id: The ID of the job to control.
            dag_yaml: Path to the YAML file containing the DAG definition.
            job_logger: Logger instance for this specific job.
            starting: Set of job IDs that are currently starting.
            job_tasks_lock: Lock for synchronizing access to the job tasks
            dictionary.
        """

        self.starting = starting
        self.job_tasks_lock = job_tasks_lock

        self._logger = job_logger
        self._logger.info(f'Initializing JobsController for job_id={job_id}, '
                          f'dag_yaml={dag_yaml}')

        self._job_id = job_id
        self._dag_yaml = dag_yaml
        self._dag, self._dag_name = _get_dag_and_name(dag_yaml)
        self._logger.info(f'Loaded DAG: {self._dag}')

        self._backend = cloud_vm_ray_backend.CloudVmRayBackend()

        # pylint: disable=line-too-long
        # Add a unique identifier to the task environment variables, so that
        # the user can have the same id for multiple recoveries.
        #   Example value: sky-2022-10-04-22-46-52-467694_my-spot-name_spot_id-17-0
        job_id_env_vars = []
        for i, task in enumerate(self._dag.tasks):
            if len(self._dag.tasks) <= 1:
                task_name = self._dag_name
            else:
                assert task.name is not None, task
                task_name = task.name
                # This is guaranteed by the jobs.launch API, where we fill in
                # the task.name with
                # dag_utils.maybe_infer_and_fill_dag_and_task_names.
                assert task_name is not None, self._dag
                task_name = f'{self._dag_name}_{task_name}'

            job_id_env_var = common_utils.get_global_job_id(
                self._backend.run_timestamp,
                f'{task_name}',
                str(self._job_id),
                task_id=i,
                is_managed_job=True)
            job_id_env_vars.append(job_id_env_var)

        for i, task in enumerate(self._dag.tasks):
            task_envs = task.envs or {}
            task_envs[constants.TASK_ID_ENV_VAR] = job_id_env_vars[i]
            task_envs[constants.TASK_ID_LIST_ENV_VAR] = '\n'.join(
                job_id_env_vars)
            task.update_envs(task_envs)

    def _download_log_and_stream(
        self, task_id: Optional[int],
        handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle']
    ) -> None:
        """Downloads and streams the logs of the current job with given task ID.

        We do not stream the logs from the cluster directly, as the
        donwload and stream should be faster, and more robust against
        preemptions or ssh disconnection during the streaming.
        """
        if handle is None:
            self._logger.info(f'Cluster for job {self._job_id} is not found. '
                              'Skipping downloading and streaming the logs.')
            return

        managed_job_logs_dir = os.path.join(constants.SKY_LOGS_DIRECTORY,
                                            'managed_jobs')

        log_file = controller_utils.download_and_stream_latest_job_log(
            self._backend, handle, managed_job_logs_dir)

        if log_file is not None:
            # Set the path of the log file for the current task, so it can
            # be accessed even after the job is finished
            managed_job_state.set_local_log_file(self._job_id, task_id,
                                                 log_file)
        else:
            self._logger.warning(
                f'No log file was downloaded for job {self._job_id}, '
                f'task {task_id}')

        self._logger.info(f'\n== End of logs (ID: {self._job_id}) ==')

    async def _run_one_task(self, task_id: int, task: 'sky.Task') -> bool:
        """Busy loop monitoring cluster status and handling recovery.

        When the task is successfully completed, this function returns True,
        and will terminate the cluster before returning.

        If the user program fails, i.e. the task is set to FAILED or
        FAILED_SETUP, this function will return False.
        In other cases, the function will raise exceptions.
        All the failure cases will rely on the caller to clean up the spot
        cluster(s) and storages.

        Returns:
            True if the job is successfully completed; False otherwise.

        Raises:
            exceptions.ProvisionPrechecksError: This will be raised when the
                underlying `sky.launch` fails due to precheck errors only.
                I.e., none of the failover exceptions, if
                any, is due to resources unavailability. This exception
                includes the following cases:
                1. The optimizer cannot find a feasible solution.
                2. Precheck errors: invalid cluster name, failure in getting
                cloud user identity, or unsupported feature.
            exceptions.ManagedJobReachedMaxRetriesError: This will be raised
                when all prechecks passed but the maximum number of retries is
                reached for `sky.launch`. The failure of `sky.launch` can be
                due to:
                1. Any of the underlying failover exceptions is due to resources
                unavailability.
                2. The cluster is preempted or failed before the job is
                submitted.
                3. Any unexpected error happens during the `sky.launch`.
        Other exceptions may be raised depending on the backend.
        """
        task_start_time = time.time()
        self._logger.info(
            f'Starting task {task_id} ({task.name}) for job {self._job_id}')

        latest_task_id, last_task_prev_status = (
            await
            managed_job_state.get_latest_task_id_status_async(self._job_id))

        is_resume = False
        if (latest_task_id is not None and last_task_prev_status !=
                managed_job_state.ManagedJobStatus.PENDING):
            assert latest_task_id >= task_id, (latest_task_id, task_id)
            if latest_task_id > task_id:
                self._logger.info(f'Task {task_id} ({task.name}) has already '
                                  'been executed. Skipping...')
                return True
            if latest_task_id == task_id:
                # Start recovery.
                is_resume = True
                self._logger.info(
                    f'Resuming task {task_id} from previous execution')

        callback_func = managed_job_utils.event_callback_func(
            job_id=self._job_id, task_id=task_id, task=task)

        if task.run is None:
            self._logger.info(
                f'Skip running task {task_id} ({task.name}) due to its '
                'run commands being empty.')
            # Call set_started first to initialize columns in the state table,
            # including start_at and last_recovery_at to avoid issues for
            # uninitialized columns.
            await managed_job_state.set_started_async(
                job_id=self._job_id,
                task_id=task_id,
                start_time=time.time(),
                callback_func=callback_func)
            await managed_job_state.set_succeeded_async(
                job_id=self._job_id,
                task_id=task_id,
                end_time=time.time(),
                callback_func=callback_func)
            self._logger.info(
                f'Empty task {task_id} marked as succeeded immediately')
            return True

        usage_lib.messages.usage.update_task_id(task_id)
        task_id_env_var = task.envs[constants.TASK_ID_ENV_VAR]
        assert task.name is not None, task
        cluster_name = managed_job_utils.generate_managed_job_cluster_name(
            task.name, self._job_id)

        self._strategy_executor = recovery_strategy.StrategyExecutor.make(
            cluster_name, self._backend, task, self._job_id, task_id,
            self._logger)

        if not is_resume:
            submitted_at = time.time()
            if task_id == 0:
                submitted_at = backend_utils.get_timestamp_from_run_timestamp(
                    self._backend.run_timestamp)

            resources_str = backend_utils.get_task_resources_str(
                task, is_managed_job=True)

            await managed_job_state.set_starting_async(
                self._job_id,
                task_id,
                self._backend.run_timestamp,
                submitted_at,
                resources_str=resources_str,
                specs={
                    'max_restarts_on_errors':
                        self._strategy_executor.max_restarts_on_errors
                },
                callback_func=callback_func)
            self._logger.info(f'Submitted managed job {self._job_id} '
                              f'(task: {task_id}, name: {task.name!r}); '
                              f'{constants.TASK_ID_ENV_VAR}: {task_id_env_var}')

        self._logger.info('Started monitoring.')

        # Only do the initial cluster launch if not resuming from a controller
        # failure. Otherwise, we will transit to recovering immediately.
        remote_job_submitted_at = time.time()
        if not is_resume:
            launch_start = time.time()

            # Run the launch in a separate thread to avoid blocking the event
            # loop. The scheduler functions used internally already have their
            # own file locks.
            remote_job_submitted_at = await managed_job_utils.to_thread(
                self._strategy_executor.launch)

            launch_time = time.time() - launch_start
            self._logger.info(f'Cluster launch completed in {launch_time:.2f}s')
            assert remote_job_submitted_at is not None, remote_job_submitted_at

        if not is_resume:
            await managed_job_state.set_started_async(
                job_id=self._job_id,
                task_id=task_id,
                start_time=remote_job_submitted_at,
                callback_func=callback_func)

        monitoring_start_time = time.time()
        status_check_count = 0

        async with self.job_tasks_lock:
            try:
                self.starting.remove(self._job_id)
            except KeyError:
                # should not happen except maybe in a weird case, but either
                # we doesn't matter, since its no longer in the set
                pass

        while True:
            status_check_count += 1

            # NOTE: if we are resuming from a controller failure, we only keep
            # monitoring if the job is in RUNNING state. For all other cases,
            # we will directly transit to recovering since we have no idea what
            # the cluster status is.
            force_transit_to_recovering = False
            if is_resume:
                prev_status = await (
                    managed_job_state.get_job_status_with_task_id_async(
                        job_id=self._job_id, task_id=task_id))

                if prev_status is not None:
                    if prev_status.is_terminal():
                        self._logger.info(
                            f'Task {task_id} already in terminal state: '
                            f'{prev_status}')
                        return (prev_status ==
                                managed_job_state.ManagedJobStatus.SUCCEEDED)
                    if (prev_status ==
                            managed_job_state.ManagedJobStatus.CANCELLING):
                        # If the controller is down when cancelling the job,
                        # we re-raise the error to run the `_cleanup` function
                        # again to clean up any remaining resources.
                        self._logger.info(
                            f'Task {task_id} was being cancelled, '
                            're-raising cancellation')
                        raise exceptions.ManagedJobUserCancelledError(
                            'Recovering cancel signal.')
                if prev_status != managed_job_state.ManagedJobStatus.RUNNING:
                    force_transit_to_recovering = True
                # This resume logic should only be triggered once.
                is_resume = False

            await asyncio.sleep(managed_job_utils.JOB_STATUS_CHECK_GAP_SECONDS)

            # Check the network connection to avoid false alarm for job failure.
            # Network glitch was observed even in the VM.
            try:
                await backend_utils.async_check_network_connection()
            except exceptions.NetworkError:
                self._logger.info(
                    'Network is not available. Retrying again in '
                    f'{managed_job_utils.JOB_STATUS_CHECK_GAP_SECONDS} '
                    'seconds.')
                continue

            # NOTE: we do not check cluster status first because race condition
            # can occur, i.e. cluster can be down during the job status check.
            # NOTE: If fetching the job status fails or we force to transit to
            # recovering, we will set the job status to None, which will force
            # enter the recovering logic.
            job_status = None
            if not force_transit_to_recovering:
                try:
                    job_status = await managed_job_utils.to_thread(
                        managed_job_utils.get_job_status, self._backend,
                        cluster_name, self._logger)
                except exceptions.FetchClusterInfoError as fetch_e:
                    self._logger.info(
                        'Failed to fetch the job status. Start recovery.\n'
                        f'Exception: {common_utils.format_exception(fetch_e)}\n'
                        f'Traceback: {traceback.format_exc()}')

            if job_status == job_lib.JobStatus.SUCCEEDED:
                self._logger.info(f'Task {task_id} succeeded!'
                                  'Getting end time and cleaning up')
                success_end_time = await managed_job_utils.to_thread(
                    managed_job_utils.try_to_get_job_end_time, self._backend,
                    cluster_name)
                # The job is done. Set the job to SUCCEEDED first before start
                # downloading and streaming the logs to make it more responsive.
                await managed_job_state.set_succeeded_async(
                    self._job_id,
                    task_id,
                    end_time=success_end_time,
                    callback_func=callback_func)
                self._logger.info(
                    f'Managed job {self._job_id} (task: {task_id}) SUCCEEDED. '
                    f'Cleaning up the cluster {cluster_name}.')
                try:
                    clusters = backend_utils.get_clusters(
                        cluster_names=[cluster_name],
                        refresh=common.StatusRefreshMode.NONE,
                        all_users=True)
                    if clusters:
                        assert len(clusters) == 1, (clusters, cluster_name)
                        handle = clusters[0].get('handle')
                        # Best effort to download and stream the logs.
                        await managed_job_utils.to_thread(
                            self._download_log_and_stream, task_id, handle)
                except Exception as e:  # pylint: disable=broad-except
                    # We don't want to crash here, so just log and continue.
                    self._logger.warning(
                        f'Failed to download and stream logs: '
                        f'{common_utils.format_exception(e)}',
                        exc_info=True)
                # Only clean up the cluster, not the storages, because tasks may
                # share storages.
                await managed_job_utils.to_thread(
                    managed_job_utils.terminate_cluster, cluster_name)

                task_total_time = time.time() - task_start_time
                monitoring_time = time.time() - monitoring_start_time
                self._logger.info(f'Task {task_id} completed successfully in '
                                  f'{task_total_time:.2f}s '
                                  f'(monitoring time: {monitoring_time:.2f}s, '
                                  f'status checks: {status_check_count})')
                return True

            # For single-node jobs, non-terminated job_status indicates a
            # healthy cluster. We can safely continue monitoring.
            # For multi-node jobs, since the job may not be set to FAILED
            # immediately (depending on user program) when only some of the
            # nodes are preempted or failed, need to check the actual cluster
            # status.
            if (job_status is not None and not job_status.is_terminal() and
                    task.num_nodes == 1):
                continue

            if job_status in job_lib.JobStatus.user_code_failure_states():
                # Add a grace period before the check of preemption to avoid
                # false alarm for job failure.
                await asyncio.sleep(5)

            # Pull the actual cluster status from the cloud provider to
            # determine whether the cluster is preempted or failed.
            # TODO(zhwu): For hardware failure, such as GPU failure, it may not
            # be reflected in the cluster status, depending on the cloud, which
            # can also cause failure of the job, and we need to recover it
            # rather than fail immediately.
            (cluster_status,
             handle) = backend_utils.refresh_cluster_status_handle(
                 cluster_name,
                 force_refresh_statuses=set(status_lib.ClusterStatus))

            if cluster_status != status_lib.ClusterStatus.UP:
                # The cluster is (partially) preempted or failed. It can be
                # down, INIT or STOPPED, based on the interruption behavior of
                # the cloud. Spot recovery is needed (will be done later in the
                # code).
                cluster_status_str = ('' if cluster_status is None else
                                      f' (status: {cluster_status.value})')
                self._logger.info(
                    f'Cluster is preempted or failed{cluster_status_str}. '
                    'Recovering...')
            else:
                if job_status is not None and not job_status.is_terminal():
                    # The multi-node job is still running, continue monitoring.
                    continue
                elif (job_status
                      in job_lib.JobStatus.user_code_failure_states() or
                      job_status == job_lib.JobStatus.FAILED_DRIVER):
                    # The user code has probably crashed, fail immediately.
                    self._logger.info(
                        f'Task {task_id} failed with status: {job_status}')
                    end_time = await managed_job_utils.to_thread(
                        managed_job_utils.try_to_get_job_end_time,
                        self._backend, cluster_name)
                    self._logger.info(
                        f'The user job failed ({job_status}). Please check the '
                        'logs below.\n'
                        f'== Logs of the user job (ID: {self._job_id}) ==\n')

                    # TODO(luca): this is a hack to avoid blocking the asyncio
                    # event loop. We should make _download_log_and_stream async
                    # however that will require a lot of changes to the code.
                    await managed_job_utils.to_thread(
                        self._download_log_and_stream, task_id, handle)

                    failure_reason = (
                        'To see the details, run: '
                        f'sky jobs logs --controller {self._job_id}')

                    managed_job_status = (
                        managed_job_state.ManagedJobStatus.FAILED)
                    if job_status == job_lib.JobStatus.FAILED_SETUP:
                        managed_job_status = (
                            managed_job_state.ManagedJobStatus.FAILED_SETUP)
                    elif job_status == job_lib.JobStatus.FAILED_DRIVER:
                        # FAILED_DRIVER is kind of an internal error, so we mark
                        # this as FAILED_CONTROLLER, even though the failure is
                        # not strictly within the controller.
                        managed_job_status = (
                            managed_job_state.ManagedJobStatus.FAILED_CONTROLLER
                        )
                        failure_reason = (
                            'The job driver on the remote cluster failed. This '
                            'can be caused by the job taking too much memory '
                            'or other resources. Try adding more memory, CPU, '
                            f'or disk in your job definition. {failure_reason}')
                    should_restart_on_failure = (
                        self._strategy_executor.should_restart_on_failure())
                    if should_restart_on_failure:
                        max_restarts = (
                            self._strategy_executor.max_restarts_on_errors)
                        self._logger.info(
                            f'User program crashed '
                            f'({managed_job_status.value}). '
                            f'Retry the job as max_restarts_on_errors is '
                            f'set to {max_restarts}. '
                            f'[{self._strategy_executor.restart_cnt_on_failure}'
                            f'/{max_restarts}]')
                    else:
                        self._logger.info(
                            f'Task {task_id} failed and will not be retried')
                        await managed_job_state.set_failed_async(
                            self._job_id,
                            task_id,
                            failure_type=managed_job_status,
                            failure_reason=failure_reason,
                            end_time=end_time,
                            callback_func=callback_func)
                        return False
                elif job_status is not None:
                    # Either the job is cancelled (should not happen) or in some
                    # unknown new state that we do not handle.
                    self._logger.error(f'Unknown job status: {job_status}')
                    failure_reason = (
                        f'Unknown job status {job_status}. To see the details, '
                        f'run: sky jobs logs --controller {self._job_id}')
                    await managed_job_state.set_failed_async(
                        self._job_id,
                        task_id,
                        failure_type=managed_job_state.ManagedJobStatus.
                        FAILED_CONTROLLER,
                        failure_reason=failure_reason,
                        callback_func=callback_func)
                    return False
                else:
                    # Although the cluster is healthy, we fail to access the
                    # job status. Try to recover the job (will not restart the
                    # cluster, if the cluster is healthy).
                    assert job_status is None, job_status
                    self._logger.info(
                        'Failed to fetch the job status while the '
                        'cluster is healthy. Try to recover the job '
                        '(the cluster will not be restarted).')
            # When the handle is None, the cluster should be cleaned up already.
            if handle is not None:
                resources = handle.launched_resources
                assert resources is not None, handle
                # If we are forcing to transit to recovering, we need to clean
                # up the cluster as it is possible that we already submitted the
                # job to the worker cluster, but state is not updated yet. In
                # this case, it is possible that we will double-submit the job
                # to the worker cluster. So we always clean up the cluster here.
                # TODO(tian,cooperc): We can check if there is a running job on
                # the worker cluster, and if so, we can skip the cleanup.
                # Challenge: race condition when the worker cluster thought it
                # does not have a running job yet but later the job is launched.
                if (resources.need_cleanup_after_preemption_or_failure() or
                        force_transit_to_recovering):
                    # Some spot resource (e.g., Spot TPU VM) may need to be
                    # cleaned up after preemption, as running launch again on
                    # those clusters again may fail.
                    self._logger.info(
                        'Cleaning up the preempted or failed cluster'
                        '...')
                    await managed_job_utils.to_thread(
                        managed_job_utils.terminate_cluster, cluster_name)

            # Try to recover the managed jobs, when the cluster is preempted or
            # failed or the job status is failed to be fetched.
            self._logger.info(f'Starting recovery for task {task_id}, '
                              f'it is currently {job_status}')
            await managed_job_state.set_recovering_async(
                job_id=self._job_id,
                task_id=task_id,
                force_transit_to_recovering=force_transit_to_recovering,
                callback_func=callback_func)

            recovery_start = time.time()
            await managed_job_utils.to_thread(self._strategy_executor.recover)
            recovery_time = time.time() - recovery_start

            self._logger.info(
                f'Recovery completed for task {task_id} in {recovery_time:.2f}s'
            )
            await managed_job_state.set_recovered_async(
                self._job_id,
                task_id,
                recovered_time=recovery_time,
                callback_func=callback_func)

    async def run(self):
        """Run controller logic and handle exceptions."""
        self._logger.info(f'Starting JobsController run for job {self._job_id}')
        task_id = 0

        try:
            succeeded = True
            # We support chain DAGs only for now.
            for task_id, task in enumerate(self._dag.tasks):
                self._logger.info(
                    f'Processing task {task_id}/{len(self._dag.tasks)-1}: '
                    f'{task.name}')
                task_start = time.time()
                succeeded = await self._run_one_task(task_id, task)
                task_time = time.time() - task_start
                self._logger.info(
                    f'Task {task_id} completed in {task_time:.2f}s '
                    f'with success={succeeded}')

                if not succeeded:
                    self._logger.info(
                        f'Task {task_id} failed, stopping execution')
                    break

        except exceptions.ProvisionPrechecksError as e:
            # Please refer to the docstring of self._run for the cases when
            # this exception can occur.
            self._logger.error(f'Provision prechecks failed for task {task_id}')
            failure_reason = ('; '.join(
                common_utils.format_exception(reason, use_bracket=True)
                for reason in e.reasons))
            self._logger.error(failure_reason)
            await self._update_failed_task_state(
                task_id, managed_job_state.ManagedJobStatus.FAILED_PRECHECKS,
                failure_reason)
        except exceptions.ManagedJobReachedMaxRetriesError as e:
            # Please refer to the docstring of self._run for the cases when
            # this exception can occur.
            self._logger.error(
                f'Managed job reached max retries for task {task_id}')
            failure_reason = common_utils.format_exception(e)
            self._logger.error(failure_reason)
            # The managed job should be marked as FAILED_NO_RESOURCE, as the
            # managed job may be able to launch next time.
            await self._update_failed_task_state(
                task_id, managed_job_state.ManagedJobStatus.FAILED_NO_RESOURCE,
                failure_reason)
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            # have this here to avoid getting caught by the general except block
            # below.
            raise
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            self._logger.error(
                f'Unexpected error in JobsController run for task {task_id}')
            with ux_utils.enable_traceback():
                self._logger.error(traceback.format_exc())
            msg = ('Unexpected error occurred: ' +
                   common_utils.format_exception(e, use_bracket=True))
            self._logger.error(msg)
            await self._update_failed_task_state(
                task_id, managed_job_state.ManagedJobStatus.FAILED_CONTROLLER,
                msg)
        finally:
            callback_func = managed_job_utils.event_callback_func(
                job_id=self._job_id,
                task_id=task_id,
                task=self._dag.tasks[task_id])
            await managed_job_state.set_cancelling_async(
                job_id=self._job_id, callback_func=callback_func)
            await managed_job_state.set_cancelled_async(
                job_id=self._job_id, callback_func=callback_func)

    async def _update_failed_task_state(
            self, task_id: int,
            failure_type: managed_job_state.ManagedJobStatus,
            failure_reason: str):
        """Update the state of the failed task."""
        self._logger.info(f'Updating failed task state: task_id={task_id}, '
                          f'failure_type={failure_type}')
        await managed_job_state.set_failed_async(
            self._job_id,
            task_id=task_id,
            failure_type=failure_type,
            failure_reason=failure_reason,
            callback_func=managed_job_utils.event_callback_func(
                job_id=self._job_id,
                task_id=task_id,
                task=self._dag.tasks[task_id]))


class Controller:
    """Controller for managing jobs."""

    def __init__(self):
        # Global state for active jobs
        self.job_tasks: Dict[int, asyncio.Task] = {}
        self.starting: Set[int] = set()

        # Lock for synchronizing access to global state dictionary
        self._job_tasks_lock = asyncio.Lock()
        self._background_tasks_lock = asyncio.Lock()

    async def _cleanup(self, job_id: int, dag_yaml: str,
                       job_logger: logging.Logger):
        """Clean up the cluster(s) and storages.

        (1) Clean up the succeeded task(s)' ephemeral storage. The storage has
            to be cleaned up after the whole job is finished, as the tasks
            may share the same storage.
        (2) Clean up the cluster(s) that are not cleaned up yet, which can
            happen when the task failed or cancelled. At most one cluster
            should be left when reaching here, as we currently only support
            chain DAGs, and only one task is executed at a time.
        """

        # Cleanup the HA recovery script first as it is possible that some error
        # was raised when we construct the task object (e.g.,
        # sky.exceptions.ResourcesUnavailableError).
        def task_cleanup(task: 'sky.Task', job_id: int):
            assert task.name is not None, task
            cluster_name = managed_job_utils.generate_managed_job_cluster_name(
                task.name, job_id)
            managed_job_utils.terminate_cluster(cluster_name)

            # Clean up Storages with persistent=False.
            # TODO(zhwu): this assumes the specific backend.
            backend = cloud_vm_ray_backend.CloudVmRayBackend()
            # Need to re-construct storage object in the controller process
            # because when SkyPilot API server machine sends the yaml config to
            # the controller machine, only storage metadata is sent, not the
            # storage object itself.
            for storage in task.storage_mounts.values():
                storage.construct()
            backend.teardown_ephemeral_storage(task)

            # Clean up any files mounted from the local disk, such as two-hop
            # file mounts.
            consolidation_mode = managed_job_utils.is_consolidation_mode()
            for file_mount in (task.file_mounts or {}).values():
                try:
                    # For consolidation mode, there is no two-hop file mounts
                    # and the file path here represents the real user data.
                    # We skip the cleanup for consolidation mode.
                    if (not data_utils.is_cloud_store_url(file_mount) and
                            not consolidation_mode):
                        path = os.path.expanduser(file_mount)
                        if os.path.isdir(path):
                            shutil.rmtree(path)
                        else:
                            os.remove(path)
                except Exception as e:  # pylint: disable=broad-except
                    job_logger.warning(
                        f'Failed to clean up file mount {file_mount}: {e}')

        await managed_job_state.remove_ha_recovery_script_async(job_id)
        dag, _ = _get_dag_and_name(dag_yaml)
        for task in dag.tasks:
            # most things in this function are blocking
            await managed_job_utils.to_thread(task_cleanup, task, job_id)

    async def run_job_loop(self,
                           job_id: int,
                           dag_yaml: str,
                           job_logger: logging.Logger,
                           env_file_path: Optional[str] = None):
        """Background task that runs the job loop."""
        # Initialize context for this job to provide environment isolation
        context.initialize()

        # Replace os.environ with ContextualEnviron to enable per-job
        # environment isolation. This allows each job to have its own
        # environment variables without affecting other jobs or the main
        # process.
        original_environ = os.environ
        os.environ = context.ContextualEnviron(original_environ)  # type: ignore

        # Load and apply environment variables from the job's environment file
        if env_file_path and os.path.exists(env_file_path):
            try:
                # Load environment variables from the file
                env_vars = dotenv.dotenv_values(env_file_path)
                job_logger.info(f'Loading environment from {env_file_path}: '
                                f'{list(env_vars.keys())}')

                # Apply environment variables to the job's context
                ctx = context.get()
                if ctx is not None:
                    for key, value in env_vars.items():
                        if value is not None:
                            ctx.override_envs({key: value})
                            job_logger.debug(
                                f'Set environment variable: {key}={value}')
                else:
                    job_logger.error(
                        'Context is None, cannot set environment variables')
            except Exception as e:  # pylint: disable=broad-except
                job_logger.error(
                    f'Failed to load environment file {env_file_path}: {e}')
        elif env_file_path:
            job_logger.error(f'Environment file not found: {env_file_path}')

        cancelling = False
        try:
            job_logger.info(f'Starting job loop for {job_id}')

            controller = JobsController(job_id, dag_yaml, job_logger,
                                        self.starting, self._job_tasks_lock)

            async with self._job_tasks_lock:
                if job_id in self.job_tasks:
                    job_logger.error(
                        f'Job {job_id} already exists in job_tasks')
                    raise ValueError(f'Job {job_id} already exists')

                # Create the task and store it
                # This function should return instantly and run the job loop in
                # the background.
                task = asyncio.create_task(controller.run())
                self.job_tasks[job_id] = task
            await task
        except asyncio.CancelledError:
            job_logger.info(f'Job {job_id} was cancelled')
            dag, _ = _get_dag_and_name(dag_yaml)
            task_id, _ = await (
                managed_job_state.get_latest_task_id_status_async(job_id))
            assert task_id is not None, job_id
            job_logger.info(f'Cancelling managed job, job_id: {job_id}, '
                            f'task_id: {task_id}')
            await managed_job_state.set_cancelling_async(
                job_id=job_id,
                callback_func=managed_job_utils.event_callback_func(
                    job_id=job_id, task_id=task_id, task=dag.tasks[task_id]))
            cancelling = True
            raise
        except Exception as e:
            job_logger.error(f'Unexpected error in job loop for {job_id}: '
                             f'{common_utils.format_exception(e)}')
            raise
        finally:
            # Restore original os.environ to avoid affecting other jobs
            os.environ = original_environ

            await self._cleanup(job_id,
                                dag_yaml=dag_yaml,
                                job_logger=job_logger)
            job_logger.info(
                f'Cluster of managed job {job_id} has been cleaned up.')

            if cancelling:
                # Since it's set with cancelling
                assert task_id is not None, job_id
                await managed_job_state.set_cancelled_async(
                    job_id=job_id,
                    callback_func=managed_job_utils.event_callback_func(
                        job_id=job_id, task_id=task_id,
                        task=dag.tasks[task_id]))

            # We should check job status after 'set_cancelled', otherwise
            # the job status is not terminal.
            job_status = await managed_job_state.get_status_async(job_id)
            assert job_status is not None
            # The job can be non-terminal if the controller exited abnormally,
            # e.g. failed to launch cluster after reaching the MAX_RETRY.
            if not job_status.is_terminal():
                job_logger.info(f'Previous job status: {job_status.value}')
                await managed_job_state.set_failed_async(
                    job_id,
                    task_id=None,
                    failure_type=managed_job_state.ManagedJobStatus.
                    FAILED_CONTROLLER,
                    failure_reason=(
                        'Unexpected error occurred. For details, '
                        f'run: sky jobs logs --controller {job_id}'))

            await scheduler.job_done_async(job_id)

            async with self._job_tasks_lock:
                try:
                    # just in case we were cancelled or some other error
                    # occurred during launch
                    self.starting.remove(job_id)
                except KeyError:
                    pass

            # Remove the job from the job_tasks dictionary.
            async with self._job_tasks_lock:
                if job_id in self.job_tasks:
                    del self.job_tasks[job_id]

    async def start_job(
        self,
        job_id: int,
        dag_yaml: str,
        env_file_path: Optional[str] = None,
    ):
        """Start a new job.

        Args:
            job_id: The ID of the job to start.
            dag_yaml: Path to the YAML file containing the DAG definition.
            env_file_path: Optional path to environment file for the job.
        """
        # Create a job-specific logger
        log_dir = os.path.expanduser(jobs_constants.JOBS_CONTROLLER_LOGS_DIR)
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f'{job_id}.log')

        job_logger = logging.getLogger(f'sky.jobs.{job_id}')
        job_logger.setLevel(logging.INFO)

        # Create file handler
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)

        # Use Sky's standard formatter
        file_handler.setFormatter(sky_logging.FORMATTER)

        # Add the handler to the logger
        job_logger.addHandler(file_handler)

        # Prevent log propagation to avoid duplicate logs
        job_logger.propagate = False

        job_logger.info(f'Starting job {job_id} with dag_yaml={dag_yaml}, '
                        f'env_file_path={env_file_path}')

        await create_background_task(
            self.run_job_loop(job_id, dag_yaml, job_logger, env_file_path))

        job_logger.info(f'Job {job_id} started successfully')

    async def cancel_job(self):
        """Cancel an existing job."""
        while True:
            cancels = os.listdir(jobs_constants.CONSOLIDATED_SIGNAL_PATH)
            for cancel in cancels:
                async with self._job_tasks_lock:
                    job_id = int(cancel)
                    if job_id in self.job_tasks:
                        logger.info(f'Cancelling job {job_id}')

                        task = self.job_tasks[job_id]

                        # Run the cancellation in the background, so we can
                        # return immediately.
                        task.cancel()
                        logger.info(f'Job {job_id} cancelled successfully')

                        os.remove(f'{jobs_constants.CONSOLIDATED_SIGNAL_PATH}/'
                                  f'{job_id}')
            await asyncio.sleep(1)

    async def monitor_loop(self):
        """Monitor the job loop."""
        logger.info('Starting monitor loop...')

        while True:
            async with self._job_tasks_lock:
                running_tasks = [
                    task for task in self.job_tasks.values() if not task.done()
                ]

            async with self._job_tasks_lock:
                starting_count = len(self.starting)

            if starting_count >= scheduler.LAUNCHES_PER_WORKER:
                # launching a job takes around 1 minute, so lets wait half that
                # time
                await asyncio.sleep(30)
                continue

            if len(running_tasks) >= scheduler.JOBS_PER_WORKER:
                await asyncio.sleep(60)
                continue

            # Check if there are any jobs that are waiting to launch
            try:
                waiting_job = await managed_job_state.get_waiting_job(
                    pid=-os.getpid())
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Failed to get waiting job: {e}')
                await asyncio.sleep(5)
                continue

            if waiting_job is None:
                await asyncio.sleep(10)
                continue

            if (waiting_job['schedule_state'] !=
                    managed_job_state.ManagedJobScheduleState.WAITING):
                # in this case it is already currently being restarted

                # two options, old_pid is > 0 meaning it is an old job, in which
                # case we do not support taking it over
                # if old_pid is < 0, that means it was started by the new
                # consolidated controller, in which case we can take it over if
                # the controller managing it is dead. if it is not dead, the
                # other process will handle restarting it.
                if waiting_job['old_pid'] < 0:
                    try:
                        process = psutil.Process(-waiting_job['old_pid'])
                        if process.is_running():
                            continue
                    except psutil.NoSuchProcess:
                        pass

            job_id = waiting_job['job_id']
            dag_yaml_path = waiting_job['dag_yaml_path']
            env_file_path = waiting_job.get('env_file_path')

            cancels = os.listdir(jobs_constants.CONSOLIDATED_SIGNAL_PATH)
            if str(job_id) in cancels:
                logger.info(f'Job {job_id} cancelled')
                os.remove(f'{jobs_constants.CONSOLIDATED_SIGNAL_PATH}/{job_id}')
                await managed_job_state.set_cancelling_async(
                    job_id=job_id,
                    callback_func=managed_job_utils.event_callback_func(
                        job_id=job_id, task_id=None, task=None))
                await managed_job_state.set_cancelled_async(
                    job_id=job_id,
                    callback_func=managed_job_utils.event_callback_func(
                        job_id=job_id, task_id=None, task=None))
                continue

            # here we need to sync, because we are modifying it
            async with self._job_tasks_lock:
                self.starting.add(job_id)

            await self.start_job(job_id, dag_yaml_path, env_file_path)


async def main():
    controller = Controller()

    # Will happen multiple times, who cares though
    os.makedirs(jobs_constants.CONSOLIDATED_SIGNAL_PATH, exist_ok=True)

    # Increase number of files we can open
    soft = None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    except OSError as e:
        logger.warning(f'Failed to increase number of files we can open: {e}\n'
                       f'Current soft limit: {soft}, hard limit: {hard}')

    # Will loop forever, do it in the background
    cancel_job_task = asyncio.create_task(controller.cancel_job())
    monitor_loop_task = asyncio.create_task(controller.monitor_loop())

    try:
        await asyncio.gather(cancel_job_task, monitor_loop_task)
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Controller server crashed: {e}')
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
