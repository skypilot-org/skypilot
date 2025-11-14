"""Controller: handles scheduling and the life cycle of a managed job.
"""
import asyncio
import io
import os
import pathlib
import resource
import shutil
import sys
import threading
import time
import traceback
import typing
from typing import Dict, Optional, Set

import dotenv

import sky
from sky import core
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.data import data_utils
from sky.jobs import constants as jobs_constants
from sky.jobs import file_content_utils
from sky.jobs import log_gc
from sky.jobs import recovery_strategy
from sky.jobs import scheduler
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants
from sky.skylet import job_lib
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import common
from sky.utils import common_utils
from sky.utils import context
from sky.utils import context_utils
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import status_lib
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import psutil
else:
    psutil = adaptors_common.LazyImport('psutil')

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
        # TODO(cooperc): Discard needs a lock?
        task.add_done_callback(_background_tasks.discard)


# Make sure to limit the size as we don't want to cache too many DAGs in memory.
@annotations.lru_cache(scope='global', maxsize=50)
def _get_dag(job_id: int) -> 'sky.Dag':
    dag_content = file_content_utils.get_job_dag_content(job_id)
    if dag_content is None:
        raise RuntimeError('Managed job DAG YAML content is unavailable for '
                           f'job {job_id}. This can happen if the job was '
                           'submitted before file migration completed or if '
                           'the submission failed to persist the DAG. Please '
                           're-submit the job.')

    dag = dag_utils.load_chain_dag_from_yaml_str(dag_content)
    assert dag.name is not None, dag
    return dag


class JobController:
    """Controls the lifecycle of a single managed job.

    This controller executes the chain DAG recorded for the job by:
    - Loading the DAG and preparing per-task environment variables so each task
      has a stable global job identifier across recoveries.
    - Launching the task on the configured backend (``CloudVmRayBackend``),
      optionally via a pool.
    - Persisting state transitions to the managed jobs state store
      (e.g., STARTING → RUNNING → SUCCEEDED/FAILED/CANCELLED).
    - Monitoring execution, downloading/streaming logs, detecting failures or
      preemptions, and invoking recovery through
      ``recovery_strategy.StrategyExecutor``.
    - Cleaning up clusters and ephemeral resources when tasks finish.

    Concurrency and coordination:
    - Runs inside an ``asyncio`` event loop.
    - Shares a ``starting`` set, guarded by ``starting_lock`` and signaled via
      ``starting_signal``, to throttle concurrent launches across jobs that the
      top-level ``Controller`` manages.

    Key attributes:
    - ``_job_id``: Integer identifier of this managed job.
    - ``_dag`` / ``_dag_name``: The job definition and metadata loaded from the
      database-backed job YAML.
    - ``_backend``: Backend used to launch and manage clusters.
    - ``_pool``: Optional pool name if using a pool.
    - ``starting`` / ``starting_lock`` / ``starting_signal``: Shared scheduler
      coordination primitives. ``starting_lock`` must be used for accessing
      ``starting_signal`` and ``starting``
    - ``_strategy_executor``: Recovery/launch strategy executor (created per
      task).
    """

    def __init__(
        self,
        job_id: int,
        starting: Set[int],
        starting_lock: asyncio.Lock,
        starting_signal: asyncio.Condition,
        pool: Optional[str] = None,
    ) -> None:
        """Initialize a ``JobsController``.

        Args:
            job_id: Integer ID of the managed job.
            starting: Shared set of job IDs currently in the STARTING phase,
                used to limit concurrent launches.
            starting_lock: ``asyncio.Lock`` guarding access to the shared
                scheduler state (e.g., the ``starting`` set).
            starting_signal: ``asyncio.Condition`` used to notify when a job
                exits STARTING so more jobs can be admitted.
            pool: Optional pool name. When provided, the job is
                submitted to the pool rather than launching a dedicated
                cluster.
        """

        self.starting = starting
        self.starting_lock = starting_lock
        self.starting_signal = starting_signal

        logger.info('Initializing JobsController for job_id=%s', job_id)

        self._job_id = job_id
        self._dag = _get_dag(job_id)
        self._dag_name = self._dag.name
        logger.info(f'Loaded DAG: {self._dag}')

        self._backend = cloud_vm_ray_backend.CloudVmRayBackend()
        self._pool = pool

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
        self,
        task_id: Optional[int],
        handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
        job_id_on_pool_cluster: Optional[int],
    ) -> None:
        """Downloads and streams the logs of the current job with given task ID.

        We do not stream the logs from the cluster directly, as the
        donwload and stream should be faster, and more robust against
        preemptions or ssh disconnection during the streaming.
        """
        if handle is None:
            logger.info(f'Cluster for job {self._job_id} is not found. '
                        'Skipping downloading and streaming the logs.')
            return

        managed_job_logs_dir = os.path.join(constants.SKY_LOGS_DIRECTORY,
                                            'managed_jobs',
                                            f'job-id-{self._job_id}')
        log_file = controller_utils.download_and_stream_job_log(
            self._backend,
            handle,
            managed_job_logs_dir,
            job_ids=[str(job_id_on_pool_cluster)]
            if job_id_on_pool_cluster is not None else None)
        if log_file is not None:
            # Set the path of the log file for the current task, so it can
            # be accessed even after the job is finished
            managed_job_state.set_local_log_file(self._job_id, task_id,
                                                 log_file)
        else:
            logger.warning(
                f'No log file was downloaded for job {self._job_id}, '
                f'task {task_id}')

        logger.info(f'\n== End of logs (ID: {self._job_id}) ==')

    async def _cleanup_cluster(self, cluster_name: Optional[str]) -> None:
        if cluster_name is None:
            return
        if self._pool is None:
            await context_utils.to_thread(managed_job_utils.terminate_cluster,
                                          cluster_name)

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
        logger.info(
            f'Starting task {task_id} ({task.name}) for job {self._job_id}')

        latest_task_id, last_task_prev_status = (
            await
            managed_job_state.get_latest_task_id_status_async(self._job_id))

        is_resume = False
        if (latest_task_id is not None and last_task_prev_status !=
                managed_job_state.ManagedJobStatus.PENDING):
            assert latest_task_id >= task_id, (latest_task_id, task_id)
            if latest_task_id > task_id:
                logger.info(f'Task {task_id} ({task.name}) has already '
                            'been executed. Skipping...')
                return True
            if latest_task_id == task_id:
                # Start recovery.
                is_resume = True
                logger.info(f'Resuming task {task_id} from previous execution')

        callback_func = managed_job_utils.event_callback_func(
            job_id=self._job_id, task_id=task_id, task=task)

        if task.run is None:
            logger.info(f'Skip running task {task_id} ({task.name}) due to its '
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
            logger.info(f'Empty task {task_id} marked as succeeded immediately')
            return True

        usage_lib.messages.usage.update_task_id(task_id)
        task_id_env_var = task.envs[constants.TASK_ID_ENV_VAR]
        assert task.name is not None, task
        # Set the cluster name to None if the job is submitted
        # to a pool. This will be updated when we later calls the `launch`
        # or `recover` function from the strategy executor.
        cluster_name = managed_job_utils.generate_managed_job_cluster_name(
            task.name, self._job_id) if self._pool is None else None
        self._strategy_executor = recovery_strategy.StrategyExecutor.make(
            cluster_name, self._backend, task, self._job_id, task_id,
            self._pool, self.starting, self.starting_lock, self.starting_signal)
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
            logger.info(f'Submitted managed job {self._job_id} '
                        f'(task: {task_id}, name: {task.name!r}); '
                        f'{constants.TASK_ID_ENV_VAR}: {task_id_env_var}')

        logger.info('Started monitoring.')

        # Only do the initial cluster launch if not resuming from a controller
        # failure. Otherwise, we will transit to recovering immediately.
        remote_job_submitted_at = time.time()
        if not is_resume:
            launch_start = time.time()

            # Run the launch in a separate thread to avoid blocking the event
            # loop. The scheduler functions used internally already have their
            # own file locks.
            remote_job_submitted_at = await self._strategy_executor.launch()

            launch_time = time.time() - launch_start
            logger.info(f'Cluster launch completed in {launch_time:.2f}s')
            assert remote_job_submitted_at is not None, remote_job_submitted_at
        if self._pool is None:
            job_id_on_pool_cluster = None
        else:
            # Update the cluster name when using pool.
            cluster_name, job_id_on_pool_cluster = (
                await
                managed_job_state.get_pool_submit_info_async(self._job_id))
        if cluster_name is None:
            # Check if we have been cancelled here, in the case where a user
            # quickly cancels the job we want to gracefully handle it here,
            # otherwise we will end up in the FAILED_CONTROLLER state.
            logger.info(f'Cluster name is None for job {self._job_id}, '
                        f'task {task_id}. Checking if we have been '
                        'cancelled.')
            status = await (managed_job_state.get_job_status_with_task_id_async(
                job_id=self._job_id, task_id=task_id))
            logger.debug(f'Status for job {self._job_id}, task {task_id}:'
                         f'{status}')
            if status == managed_job_state.ManagedJobStatus.CANCELLED:
                logger.info(f'Job {self._job_id}, task {task_id} has '
                            'been quickly cancelled.')
                raise asyncio.CancelledError()
        assert cluster_name is not None, (cluster_name, job_id_on_pool_cluster)

        if not is_resume:
            await managed_job_state.set_started_async(
                job_id=self._job_id,
                task_id=task_id,
                start_time=remote_job_submitted_at,
                callback_func=callback_func)

        monitoring_start_time = time.time()
        status_check_count = 0

        async with self.starting_lock:
            try:
                self.starting.remove(self._job_id)
                # its fine if we notify again, better to wake someone up
                # and have them go to sleep again, then have some stuck
                # sleeping.
                # ps. this shouldn't actually happen because if its been
                # removed from the set then we would get a key error.
                self.starting_signal.notify()
            except KeyError:
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
                        logger.info(
                            f'Task {task_id} already in terminal state: '
                            f'{prev_status}')
                        return (prev_status ==
                                managed_job_state.ManagedJobStatus.SUCCEEDED)
                    if (prev_status ==
                            managed_job_state.ManagedJobStatus.CANCELLING):
                        # If the controller is down when cancelling the job,
                        # we re-raise the error to run the `_cleanup` function
                        # again to clean up any remaining resources.
                        logger.info(f'Task {task_id} was being cancelled, '
                                    're-raising cancellation')
                        raise asyncio.CancelledError()
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
                logger.info('Network is not available. Retrying again in '
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
                    job_status = await managed_job_utils.get_job_status(
                        self._backend,
                        cluster_name,
                        job_id=job_id_on_pool_cluster,
                    )
                except exceptions.FetchClusterInfoError as fetch_e:
                    logger.info(
                        'Failed to fetch the job status. Start recovery.\n'
                        f'Exception: {common_utils.format_exception(fetch_e)}\n'
                        f'Traceback: {traceback.format_exc()}')

            if job_status == job_lib.JobStatus.SUCCEEDED:
                logger.info(f'Task {task_id} succeeded! '
                            'Getting end time and cleaning up')
                try:
                    success_end_time = await context_utils.to_thread(
                        managed_job_utils.try_to_get_job_end_time,
                        self._backend, cluster_name, job_id_on_pool_cluster)
                except Exception as e:  # pylint: disable=broad-except
                    logger.warning(
                        f'Failed to get job end time: '
                        f'{common_utils.format_exception(e)}',
                        exc_info=True)
                    success_end_time = 0

                # The job is done. Set the job to SUCCEEDED first before start
                # downloading and streaming the logs to make it more responsive.
                await managed_job_state.set_succeeded_async(
                    self._job_id,
                    task_id,
                    end_time=success_end_time,
                    callback_func=callback_func)
                logger.info(
                    f'Managed job {self._job_id} (task: {task_id}) SUCCEEDED. '
                    f'Cleaning up the cluster {cluster_name}.')
                try:
                    logger.info(f'Downloading logs on cluster {cluster_name} '
                                f'and job id {job_id_on_pool_cluster}.')
                    clusters = await context_utils.to_thread(
                        backend_utils.get_clusters,
                        cluster_names=[cluster_name],
                        refresh=common.StatusRefreshMode.NONE,
                        all_users=True,
                        _include_is_managed=True)
                    if clusters:
                        assert len(clusters) == 1, (clusters, cluster_name)
                        handle = clusters[0].get('handle')
                        # Best effort to download and stream the logs.
                        await context_utils.to_thread(
                            self._download_log_and_stream, task_id, handle,
                            job_id_on_pool_cluster)
                except Exception as e:  # pylint: disable=broad-except
                    # We don't want to crash here, so just log and continue.
                    logger.warning(
                        f'Failed to download and stream logs: '
                        f'{common_utils.format_exception(e)}',
                        exc_info=True)
                # Only clean up the cluster, not the storages, because tasks may
                # share storages.
                await self._cleanup_cluster(cluster_name)

                task_total_time = time.time() - task_start_time
                monitoring_time = time.time() - monitoring_start_time
                logger.info(f'Task {task_id} completed successfully in '
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
                logger.info(
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
                    logger.info(
                        f'Task {task_id} failed with status: {job_status}')
                    end_time = await context_utils.to_thread(
                        managed_job_utils.try_to_get_job_end_time,
                        self._backend, cluster_name, job_id_on_pool_cluster)
                    logger.info(
                        f'The user job failed ({job_status}). Please check the '
                        'logs below.\n'
                        f'== Logs of the user job (ID: {self._job_id}) ==\n')

                    await context_utils.to_thread(self._download_log_and_stream,
                                                  task_id, handle,
                                                  job_id_on_pool_cluster)

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
                        logger.info(
                            f'User program crashed '
                            f'({managed_job_status.value}). '
                            f'Retry the job as max_restarts_on_errors is '
                            f'set to {max_restarts}. '
                            f'[{self._strategy_executor.restart_cnt_on_failure}'
                            f'/{max_restarts}]')
                    else:
                        logger.info(
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
                    logger.error(f'Unknown job status: {job_status}')
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
                    logger.info('Failed to fetch the job status while the '
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
                    logger.info('Cleaning up the preempted or failed cluster'
                                '...')
                    await self._cleanup_cluster(cluster_name)

            # Try to recover the managed jobs, when the cluster is preempted or
            # failed or the job status is failed to be fetched.
            logger.info(f'Starting recovery for task {task_id}, '
                        f'it is currently {job_status}')
            await managed_job_state.set_recovering_async(
                job_id=self._job_id,
                task_id=task_id,
                force_transit_to_recovering=force_transit_to_recovering,
                callback_func=callback_func)

            recovered_time = await self._strategy_executor.recover()

            if self._pool is not None:
                cluster_name, job_id_on_pool_cluster = (
                    await
                    managed_job_state.get_pool_submit_info_async(self._job_id))
                assert cluster_name is not None
            await managed_job_state.set_recovered_async(
                self._job_id,
                task_id,
                recovered_time=recovered_time,
                callback_func=callback_func)

    async def run(self):
        """Run controller logic and handle exceptions."""
        logger.info(f'Starting JobsController run for job {self._job_id}')
        task_id = 0
        cancelled = False

        try:
            succeeded = True
            # We support chain DAGs only for now.
            for task_id, task in enumerate(self._dag.tasks):
                logger.info(
                    f'Processing task {task_id}/{len(self._dag.tasks)-1}: '
                    f'{task.name}')
                task_start = time.time()
                succeeded = await self._run_one_task(task_id, task)
                task_time = time.time() - task_start
                logger.info(f'Task {task_id} completed in {task_time:.2f}s '
                            f'with success={succeeded}')

                if not succeeded:
                    logger.info(f'Task {task_id} failed, stopping execution')
                    break

        except exceptions.ProvisionPrechecksError as e:
            # Please refer to the docstring of self._run for the cases when
            # this exception can occur.
            logger.error(f'Provision prechecks failed for task {task_id}')
            failure_reason = ('; '.join(
                common_utils.format_exception(reason, use_bracket=True)
                for reason in e.reasons))
            logger.error(failure_reason)
            await self._update_failed_task_state(
                task_id, managed_job_state.ManagedJobStatus.FAILED_PRECHECKS,
                failure_reason)
        except exceptions.ManagedJobReachedMaxRetriesError as e:
            # Please refer to the docstring of self._run for the cases when
            # this exception can occur.
            logger.error(f'Managed job reached max retries for task {task_id}')
            failure_reason = common_utils.format_exception(e)
            logger.error(failure_reason)
            # The managed job should be marked as FAILED_NO_RESOURCE, as the
            # managed job may be able to launch next time.
            await self._update_failed_task_state(
                task_id, managed_job_state.ManagedJobStatus.FAILED_NO_RESOURCE,
                failure_reason)
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            # have this here to avoid getting caught by the general except block
            # below.
            cancelled = True
            raise
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            logger.error(
                f'Unexpected error in JobsController run for task {task_id}')
            with ux_utils.enable_traceback():
                logger.error(traceback.format_exc())
            msg = ('Unexpected error occurred: ' +
                   common_utils.format_exception(e, use_bracket=True))
            logger.error(msg)
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
            if not cancelled:
                # the others haven't been run yet so we can set them to
                # cancelled immediately (no resources to clean up).
                # if we are running and get cancelled, we need to clean up the
                # resources first so this will be done later.
                await managed_job_state.set_cancelled_async(
                    job_id=self._job_id, callback_func=callback_func)

    async def _update_failed_task_state(
            self, task_id: int,
            failure_type: managed_job_state.ManagedJobStatus,
            failure_reason: str):
        """Update the state of the failed task."""
        logger.info(f'Updating failed task state: task_id={task_id}, '
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


class ControllerManager:
    """Main loop for a job controller process.

    Many jobs will be handled by this, each by a single JobController.
    """

    def __init__(self, controller_uuid: str) -> None:
        self._controller_uuid = controller_uuid
        # Global state for active jobs
        self.job_tasks: Dict[int, asyncio.Task] = {}
        self.starting: Set[int] = set()

        # Lock for synchronizing access to global state dictionary
        # Must always hold _job_tasks_lock when accessing the _starting_signal.
        self._job_tasks_lock = asyncio.Lock()
        # We signal whenever a job leaves the api server launching state. Feel
        # free to signal as much as you want to be safe from leaks (if you
        # do not signal enough there may be some jobs forever waiting to
        # launch).
        self._starting_signal = asyncio.Condition(lock=self._job_tasks_lock)

        self._pid = os.getpid()
        self._pid_started_at = psutil.Process(self._pid).create_time()

    async def _cleanup(self, job_id: int, pool: Optional[str] = None):
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
        await managed_job_state.remove_ha_recovery_script_async(job_id)

        def task_cleanup(task: 'sky.Task', job_id: int):
            assert task.name is not None, task
            error = None

            try:
                if pool is None:
                    cluster_name = (
                        managed_job_utils.generate_managed_job_cluster_name(
                            task.name, job_id))
                    managed_job_utils.terminate_cluster(cluster_name)
                    status = core.status(cluster_names=[cluster_name],
                                         all_users=True)
                    assert (len(status) == 0 or
                            status[0]['status'] == sky.ClusterStatus.STOPPED), (
                                f'{cluster_name} is not down: {status}')
                    logger.info(f'{cluster_name} is down')
                else:
                    cluster_name, job_id_on_pool_cluster = (
                        managed_job_state.get_pool_submit_info(job_id))
                    if cluster_name is not None:
                        if job_id_on_pool_cluster is not None:
                            core.cancel(cluster_name=cluster_name,
                                        job_ids=[job_id_on_pool_cluster],
                                        _try_cancel_if_cluster_is_init=True)
            except Exception as e:  # pylint: disable=broad-except
                error = e
                logger.warning(
                    f'Failed to terminate cluster {cluster_name}: {e}')
                # we continue to try cleaning up whatever else we can.
            # Clean up Storages with persistent=False.
            # TODO(zhwu): this assumes the specific backend.
            backend = cloud_vm_ray_backend.CloudVmRayBackend()
            # Need to re-construct storage object in the controller process
            # because when SkyPilot API server machine sends the yaml config to
            # the controller machine, only storage metadata is sent, not the
            # storage object itself.
            try:
                for storage in task.storage_mounts.values():
                    storage.construct()
            except (exceptions.StorageSpecError, exceptions.StorageError) as e:
                logger.warning(
                    f'Failed to construct storage object for teardown: {e}\n'
                    'This may happen because storage construction already '
                    'failed during launch, storage was deleted externally, '
                    'credentials expired/changed, or network connectivity '
                    'issues.')
            try:
                backend.teardown_ephemeral_storage(task)
            except Exception as e:  # pylint: disable=broad-except
                error = e
                logger.warning(f'Failed to teardown ephemeral storage: {e}')
                # we continue to try cleaning up whatever else we can.

            # Clean up any files mounted from the local disk, such as two-hop
            # file mounts.
            for file_mount in (task.file_mounts or {}).values():
                try:
                    # For consolidation mode, there is no two-hop file mounts
                    # and the file path here represents the real user data.
                    # We skip the cleanup for consolidation mode.
                    if (not data_utils.is_cloud_store_url(file_mount) and
                            not managed_job_utils.is_consolidation_mode()):
                        path = os.path.expanduser(file_mount)
                        if os.path.isdir(path):
                            shutil.rmtree(path)
                        else:
                            os.remove(path)
                except Exception as e:  # pylint: disable=broad-except
                    logger.warning(
                        f'Failed to clean up file mount {file_mount}: {e}')

            if error is not None:
                raise error

        dag = _get_dag(job_id)
        error = None
        for task in dag.tasks:
            # most things in this function are blocking
            try:
                await context_utils.to_thread(task_cleanup, task, job_id)
            except Exception as e:  # pylint: disable=broad-except
                error = e

        if error is not None:
            # we only raise the last error that occurred, but its fine to lose
            # some data here.
            raise error

    # Use context.contextual to enable per-job output redirection and env var
    # isolation.
    @context.contextual_async
    async def run_job_loop(self,
                           job_id: int,
                           log_file: str,
                           pool: Optional[str] = None):
        """Background task that runs the job loop."""
        ctx = context.get()
        assert ctx is not None, 'Context is not initialized'
        ctx.redirect_log(pathlib.Path(log_file))

        logger.info(f'Starting job loop for {job_id}')
        logger.info(f'  log_file={log_file}')
        logger.info(f'  pool={pool}')
        logger.info(f'From controller {self._controller_uuid}')
        logger.info(f'  pid={self._pid}')

        env_content = file_content_utils.get_job_env_content(job_id)
        if env_content:
            try:
                env_vars = dotenv.dotenv_values(stream=io.StringIO(env_content))
                logger.info('Loading %d environment variables for job %s',
                            len(env_vars), job_id)
                if ctx is not None:
                    for key, value in env_vars.items():
                        if value is not None:
                            ctx.override_envs({key: value})
                            logger.debug('Set environment variable: %s=%s', key,
                                         value)
                    skypilot_config.reload_config()
                else:  # pragma: no cover - defensive
                    logger.error('Context is None, cannot set environment '
                                 'variables')
            except Exception as e:  # pylint: disable=broad-except
                logger.error(
                    'Failed to load environment variables for job %s: '
                    '%s', job_id, e)

        cancelling = False
        try:
            controller = JobController(job_id, self.starting,
                                       self._job_tasks_lock,
                                       self._starting_signal, pool)

            async with self._job_tasks_lock:
                if job_id in self.job_tasks:
                    logger.error(f'Job {job_id} already exists in job_tasks')
                    raise ValueError(f'Job {job_id} already exists')

                # Create the task and store it
                # This function should return instantly and run the job loop in
                # the background.
                task = asyncio.create_task(controller.run())
                self.job_tasks[job_id] = task
            await task
        except asyncio.CancelledError:
            logger.info(f'Job {job_id} was cancelled')
            dag = _get_dag(job_id)
            task_id, _ = await (
                managed_job_state.get_latest_task_id_status_async(job_id))
            assert task_id is not None, job_id
            logger.info(f'Cancelling managed job, job_id: {job_id}, '
                        f'task_id: {task_id}')
            await managed_job_state.set_cancelling_async(
                job_id=job_id,
                callback_func=managed_job_utils.event_callback_func(
                    job_id=job_id, task_id=task_id, task=dag.tasks[task_id]))
            cancelling = True
            raise
        except Exception as e:
            logger.error(f'Unexpected error in job loop for {job_id}: '
                         f'{common_utils.format_exception(e)}')
            raise
        finally:
            try:
                await self._cleanup(job_id, pool=pool)
                logger.info(
                    f'Cluster of managed job {job_id} has been cleaned up.')
            except Exception as e:  # pylint: disable=broad-except
                failure_reason = ('Failed to clean up: '
                                  f'{common_utils.format_exception(e)}')
                await managed_job_state.set_failed_async(
                    job_id,
                    task_id=None,
                    failure_type=managed_job_state.ManagedJobStatus.
                    FAILED_CONTROLLER,
                    failure_reason=failure_reason,
                    override_terminal=True)

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
                logger.info(f'Previous job status: {job_status.value}')
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
                    # its fine if we notify again, better to wake someone up
                    # and have them go to sleep again, then have some stuck
                    # sleeping.
                    self._starting_signal.notify()
                except KeyError:
                    pass

            # Remove the job from the job_tasks dictionary.
            async with self._job_tasks_lock:
                if job_id in self.job_tasks:
                    del self.job_tasks[job_id]

    async def start_job(
        self,
        job_id: int,
        pool: Optional[str] = None,
    ):
        """Start a new job.

        Args:
            job_id: The ID of the job to start.
        """
        # Create log file path for job output redirection
        log_dir = os.path.expanduser(jobs_constants.JOBS_CONTROLLER_LOGS_DIR)
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f'{job_id}.log')

        logger.info(f'Starting job {job_id} with log_file={log_file}')

        async with self._job_tasks_lock:
            self.starting.add(job_id)
        await create_background_task(self.run_job_loop(job_id, log_file, pool))

        logger.info(f'Job {job_id} started successfully')

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
            await asyncio.sleep(15)

    async def monitor_loop(self):
        """Monitor the job loop."""
        logger.info(f'Starting monitor loop for pid {self._pid}...')

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

            # Normally, 200 jobs can run on each controller. But if we have a
            # ton of controllers, we need to limit the number of jobs that can
            # run on each controller, to achieve a total of 2000 jobs across all
            # controllers.
            max_jobs = min(scheduler.MAX_JOBS_PER_WORKER,
                           (scheduler.MAX_TOTAL_RUNNING_JOBS //
                            scheduler.get_number_of_controllers()))

            if len(running_tasks) >= max_jobs:
                logger.info('Too many jobs running, waiting for 60 seconds')
                await asyncio.sleep(60)
                continue

            # Check if there are any jobs that are waiting to launch
            try:
                waiting_job = await managed_job_state.get_waiting_job_async(
                    pid=self._pid, pid_started_at=self._pid_started_at)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Failed to get waiting job: {e}')
                await asyncio.sleep(5)
                continue

            if waiting_job is None:
                logger.info('No waiting job, waiting for 10 seconds')
                await asyncio.sleep(10)
                continue

            logger.info(f'Claiming job {waiting_job["job_id"]}')
            job_id = waiting_job['job_id']
            pool = waiting_job.get('pool', None)

            cancels = os.listdir(jobs_constants.CONSOLIDATED_SIGNAL_PATH)
            if str(job_id) in cancels:
                status = await managed_job_state.get_status_async(job_id)
                if status == managed_job_state.ManagedJobStatus.PENDING:
                    logger.info(f'Job {job_id} cancelled')
                    os.remove(f'{jobs_constants.CONSOLIDATED_SIGNAL_PATH}/'
                              f'{job_id}')
                    await managed_job_state.set_cancelling_async(
                        job_id=job_id,
                        callback_func=managed_job_utils.event_callback_func(
                            job_id=job_id, task_id=None, task=None))
                    await managed_job_state.set_cancelled_async(
                        job_id=job_id,
                        callback_func=managed_job_utils.event_callback_func(
                            job_id=job_id, task_id=None, task=None))
                    continue

            await self.start_job(job_id, pool)


async def main(controller_uuid: str):
    logger.info(f'Starting controller {controller_uuid}')

    context_utils.hijack_sys_attrs()

    controller = ControllerManager(controller_uuid)

    # Will happen multiple times, who cares though
    os.makedirs(jobs_constants.CONSOLIDATED_SIGNAL_PATH, exist_ok=True)

    # Increase number of files we can open
    soft = None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        logger.info(f'Current rlimits for NOFILE: soft={soft}, hard={hard}')
        logger.info(f'Increasing soft limit to {hard}')
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    except OSError as e:
        logger.warning(f'Failed to increase number of files we can open: {e}\n'
                       f'Current soft limit: {soft}, hard limit: {hard}')

    # Will loop forever, do it in the background
    cancel_job_task = asyncio.create_task(controller.cancel_job())
    monitor_loop_task = asyncio.create_task(controller.monitor_loop())
    # Run the garbage collector in a dedicated daemon thread to avoid affecting
    # the main event loop.
    gc_thread = threading.Thread(target=log_gc.elect_for_log_gc, daemon=True)
    gc_thread.start()
    try:
        await asyncio.gather(cancel_job_task, monitor_loop_task)
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Controller server crashed: {e}')
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))
