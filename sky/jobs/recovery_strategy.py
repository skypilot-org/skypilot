"""Strategies to handle launching/recovery/termination of managed job clusters.

In the YAML file, the user can specify the strategy to use for managed jobs.

resources:
    job_recovery: EAGER_NEXT_REGION
"""
import asyncio
import logging
import os
import traceback
import typing
from typing import List, Optional, Set

from sky import backends
from sky import dag as dag_lib
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.backends import backend_utils
from sky.client import sdk
from sky.jobs import scheduler
from sky.jobs import state
from sky.jobs import utils as managed_job_utils
from sky.serve import serve_utils
from sky.skylet import constants
from sky.skylet import job_lib
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import instance_links as instance_links_utils
from sky.utils import registry
from sky.utils import status_lib
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources
    from sky import task as task_lib

logger = sky_logging.init_logger(__name__)

# Waiting time for job from INIT/PENDING to RUNNING
# 10 * JOB_STARTED_STATUS_CHECK_GAP_SECONDS = 10 * 5 = 50 seconds
MAX_JOB_CHECKING_RETRY = 10

# Minutes to job cluster autodown. This should be significantly larger than
# managed_job_utils.JOB_STATUS_CHECK_GAP_SECONDS, to avoid tearing down the
# cluster before its status can be updated by the job controller.
_AUTODOWN_MINUTES = 10

ENV_VARS_TO_CLEAR = [
    skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
    constants.USER_ID_ENV_VAR,
    constants.USER_ENV_VAR,
    env_options.Options.SHOW_DEBUG_INFO.env_key,
]


class StrategyExecutor:
    """Handle the launching, recovery and termination of managed job clusters"""

    RETRY_INIT_GAP_SECONDS = 60

    def __init__(
        self,
        cluster_name: Optional[str],
        backend: 'backends.Backend',
        task: 'task_lib.Task',
        max_restarts_on_errors: int,
        job_id: int,
        task_id: int,
        pool: Optional[str],
        starting: Set[int],
        starting_lock: asyncio.Lock,
        starting_signal: asyncio.Condition,
        recover_on_exit_codes: Optional[List[int]] = None,
    ) -> None:
        """Initialize the strategy executor.

        Args:
            cluster_name: The name of the cluster.
            backend: The backend to use. Only CloudVMRayBackend is supported.
            task: The task to execute.
            max_restarts_on_errors: Maximum number of restarts on errors.
            job_id: The ID of the job.
            task_id: The ID of the task.
            starting: Set of job IDs that are currently starting.
            starting_lock: Lock to synchronize starting jobs.
            starting_signal: Condition to signal when a job can start.
            recover_on_exit_codes: List of exit codes that should trigger
                recovery regardless of max_restarts_on_errors limit.
        """
        assert isinstance(backend, backends.CloudVmRayBackend), (
            'Only CloudVMRayBackend is supported.')
        self.dag = dag_lib.Dag()
        self.dag.add(task)
        # For jobs submitted to a pool, the cluster name might change after each
        # recovery. Initially this is set to an empty string to indicate that no
        # cluster is assigned yet, and in `_launch`, it will be set to one of
        # the cluster names in the pool.
        self.cluster_name = cluster_name
        self.backend = backend
        self.max_restarts_on_errors = max_restarts_on_errors
        self.recover_on_exit_codes = recover_on_exit_codes or []
        self.job_id = job_id
        self.task_id = task_id
        self.pool = pool
        self.restart_cnt_on_failure = 0
        self.job_id_on_pool_cluster: Optional[int] = None
        self.starting = starting
        self.starting_lock = starting_lock
        self.starting_signal = starting_signal

    @classmethod
    def make(
        cls,
        cluster_name: Optional[str],
        backend: 'backends.Backend',
        task: 'task_lib.Task',
        job_id: int,
        task_id: int,
        pool: Optional[str],
        starting: Set[int],
        starting_lock: asyncio.Lock,
        starting_signal: asyncio.Condition,
    ) -> 'StrategyExecutor':
        """Create a strategy from a task."""

        # TODO(cooperc): Consider defaulting to FAILOVER if using k8s with a
        # single context, since there are not multiple clouds/regions to
        # failover through.
        resource_list = list(task.resources)
        job_recovery = resource_list[0].job_recovery
        for resource in resource_list:
            if resource.job_recovery != job_recovery:
                raise ValueError(
                    'The job recovery strategy should be the same for all '
                    'resources.')
        # Remove the job_recovery field from the resources, as the strategy
        # will be handled by the strategy class.
        new_resources_list = [r.copy(job_recovery=None) for r in resource_list]
        # set the new_task_resources to be the same type (list or set) as the
        # original task.resources
        task.set_resources(type(task.resources)(new_resources_list))
        if isinstance(job_recovery, dict):
            name = job_recovery.pop(
                'strategy', registry.JOBS_RECOVERY_STRATEGY_REGISTRY.default)
            assert name is None or isinstance(name, str), (
                name, 'The job recovery strategy name must be a string or None')
            job_recovery_name: Optional[str] = name
            max_restarts_on_errors = job_recovery.pop('max_restarts_on_errors',
                                                      0)
            recover_exit_codes = job_recovery.pop('recover_on_exit_codes', None)
            # Normalize single integer to list
            recover_on_exit_codes: Optional[List[int]] = None
            if isinstance(recover_exit_codes, int):
                recover_on_exit_codes = [recover_exit_codes]
            elif isinstance(recover_exit_codes, list):
                recover_on_exit_codes = [
                    int(code) for code in recover_exit_codes
                ]
        else:
            job_recovery_name = job_recovery
            max_restarts_on_errors = 0
            recover_on_exit_codes = None
        job_recovery_strategy = (registry.JOBS_RECOVERY_STRATEGY_REGISTRY.
                                 from_str(job_recovery_name))
        assert job_recovery_strategy is not None, job_recovery_name
        return job_recovery_strategy(cluster_name, backend, task,
                                     max_restarts_on_errors, job_id, task_id,
                                     pool, starting, starting_lock,
                                     starting_signal, recover_on_exit_codes)

    async def launch(self) -> float:
        """Launch the cluster for the first time.

        It can fail if resource is not available. Need to check the cluster
        status, after calling.

        Returns: The job's submit timestamp, on success (otherwise, an
            exception is raised).

        Raises: Please refer to the docstring of self._launch().
        """

        job_submit_at = await self._launch(max_retry=None)
        assert job_submit_at is not None
        return job_submit_at

    async def recover(self) -> float:
        """Relaunch the cluster after failure and wait until job starts.

        When recover() is called the cluster should be in STOPPED status (i.e.
        partially down).

        Returns: The timestamp job started.
        """
        raise NotImplementedError

    async def _try_cancel_jobs(self):
        if self.cluster_name is None:
            return
        handle = await asyncio.to_thread(
            global_user_state.get_handle_from_cluster_name, self.cluster_name)
        if handle is None or self.pool is not None:
            return
        try:
            usage_lib.messages.usage.set_internal()
            # Note that `sky.cancel()` may not go through for a variety of
            # reasons:
            # (1) head node is preempted; or
            # (2) somehow user programs escape the cancel codepath's kill.
            # The latter is silent and is a TODO.
            #
            # For the former, an exception will be thrown, in which case we
            # fallback to terminate_cluster() in the except block below. This
            # is because in the event of recovery on the same set of remaining
            # worker nodes, we don't want to leave some old job processes
            # running.
            # TODO(zhwu): This is non-ideal and we should figure out another way
            # to reliably cancel those processes and not have to down the
            # remaining nodes first.
            #
            # In the case where the worker node is preempted, the `sky.cancel()`
            # should be functional with the `_try_cancel_if_cluster_is_init`
            # flag, i.e. it sends the cancel signal to the head node, which will
            # then kill the user process on remaining worker nodes.
            # Only cancel the corresponding job for pool.
            if self.pool is None:
                request_id = await asyncio.to_thread(
                    sdk.cancel,
                    cluster_name=self.cluster_name,
                    all=True,
                    _try_cancel_if_cluster_is_init=True,
                )
            else:
                request_id = await asyncio.to_thread(
                    sdk.cancel,
                    cluster_name=self.cluster_name,
                    job_ids=[self.job_id_on_pool_cluster],
                    _try_cancel_if_cluster_is_init=True,
                )
            logger.debug(f'sdk.cancel request ID: {request_id}')
            await asyncio.to_thread(
                sdk.get,
                request_id,
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.info('Failed to cancel the job on the cluster. The cluster '
                        'might be already down or the head node is preempted.'
                        '\n  Detailed exception: '
                        f'{common_utils.format_exception(e)}\n'
                        'Terminating the cluster explicitly to ensure no '
                        'remaining job process interferes with recovery.')
            await asyncio.to_thread(self._cleanup_cluster)

    async def _wait_until_job_starts_on_cluster(self) -> Optional[float]:
        """Wait for MAX_JOB_CHECKING_RETRY times until job starts on the cluster

        Returns:
            The timestamp of when the job is submitted, or None if failed to
            submit.
        """
        assert self.cluster_name is not None
        status = None
        job_checking_retry_cnt = 0
        while job_checking_retry_cnt < MAX_JOB_CHECKING_RETRY:
            # Avoid the infinite loop, if any bug happens.
            job_checking_retry_cnt += 1
            try:
                cluster_status, _ = (await asyncio.to_thread(
                    backend_utils.refresh_cluster_status_handle,
                    self.cluster_name,
                    force_refresh_statuses=set(status_lib.ClusterStatus)))
            except Exception as e:  # pylint: disable=broad-except
                # If any unexpected error happens, retry the job checking
                # loop.
                # TODO(zhwu): log the unexpected error to usage collection
                # for future debugging.
                logger.info(f'Unexpected exception: {e}\nFailed to get the '
                            'refresh the cluster status. Retrying.')
                continue
            if cluster_status not in (status_lib.ClusterStatus.UP,
                                      status_lib.ClusterStatus.AUTOSTOPPING):
                # The cluster can be preempted before the job is
                # launched.
                # Break to let the retry launch kick in.
                logger.info('The cluster is preempted before the job '
                            'is submitted.')
                # TODO(zhwu): we should recover the preemption with the
                # recovery strategy instead of the current while loop.
                break

            try:
                status, transient_error_reason = (
                    await managed_job_utils.get_job_status(
                        self.backend,
                        self.cluster_name,
                        job_id=self.job_id_on_pool_cluster))
            except Exception as e:  # pylint: disable=broad-except
                transient_error_reason = common_utils.format_exception(e)
                # If any unexpected error happens, retry the job checking
                # loop.
                # Note: the CommandError is already handled in the
                # get_job_status, so it should not happen here.
                # TODO(zhwu): log the unexpected error to usage collection
                # for future debugging.
                logger.info('Unexpected exception during fetching job status: '
                            f'{common_utils.format_exception(e)}')
                continue
            if transient_error_reason is not None:
                logger.info('Transient error when fetching the job status: '
                            f'{transient_error_reason}')
                continue

            # Check the job status until it is not in initialized status
            if status is not None and status > job_lib.JobStatus.INIT:
                try:
                    job_submitted_at = await asyncio.to_thread(
                        managed_job_utils.get_job_timestamp,
                        self.backend,
                        self.cluster_name,
                        self.job_id_on_pool_cluster,
                        get_end_time=False)
                    return job_submitted_at
                except Exception as e:  # pylint: disable=broad-except
                    # If we failed to get the job timestamp, we will retry
                    # job checking loop.
                    logger.info(f'Unexpected Exception: {e}\nFailed to get '
                                'the job start timestamp. Retrying.')
                    continue
            # Wait for the job to be started
            await asyncio.sleep(
                managed_job_utils.JOB_STARTED_STATUS_CHECK_GAP_SECONDS)
        return None

    def _cleanup_cluster(self) -> None:
        if self.cluster_name is None:
            return
        if self.pool is None:
            managed_job_utils.terminate_cluster(self.cluster_name)

    async def _launch(self,
                      max_retry: Optional[int] = 3,
                      raise_on_failure: bool = True,
                      recovery: bool = False) -> Optional[float]:
        """Implementation of launch().

        The function will wait until the job starts running, but will leave the
        handling for the preemption to the caller.

        Args:
            max_retry: The maximum number of retries. If None, retry forever.
            raise_on_failure: Whether to raise an exception if the launch fails.

        Returns:
            The job's submit timestamp, or None if failed to submit the job
            (either provisioning fails or any error happens in job submission)
            and raise_on_failure is False.

        Raises:
            non-exhaustive list of exceptions:
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
                2. The cluster is preempted before the job is submitted.
                3. Any unexpected error happens during the `sky.launch`.
        Other exceptions may be raised depending on the backend.
        """
        # TODO(zhwu): handle the failure during `preparing sky runtime`.
        retry_cnt = 0
        backoff = common_utils.Backoff(self.RETRY_INIT_GAP_SECONDS)
        while True:
            retry_cnt += 1
            try:
                async with scheduler.scheduled_launch(
                        self.job_id,
                        self.starting,
                        self.starting_lock,
                        self.starting_signal,
                ):
                    # The job state may have been PENDING during backoff -
                    # update to STARTING or RECOVERING.
                    # On the first attempt (when retry_cnt is 1), we should
                    # already be in STARTING or RECOVERING.
                    if retry_cnt > 1:
                        await state.set_restarting_async(
                            self.job_id, self.task_id, recovery)
                    try:
                        usage_lib.messages.usage.set_internal()
                        if self.pool is None:
                            assert self.cluster_name is not None

                            # sdk.launch will implicitly start the API server,
                            # but then the API server will inherit the current
                            # env vars/user, which we may not want.
                            # Instead, clear env vars here and call api_start
                            # explicitly.
                            vars_to_restore = {}
                            try:
                                for env_var in ENV_VARS_TO_CLEAR:
                                    vars_to_restore[env_var] = os.environ.pop(
                                        env_var, None)
                                    logger.debug('Cleared env var: '
                                                 f'{env_var}')
                                logger.debug('Env vars for api_start: '
                                             f'{os.environ}')
                                await asyncio.to_thread(sdk.api_start)
                                logger.info('API server started.')
                            finally:
                                for env_var, value in vars_to_restore.items():
                                    if value is not None:
                                        logger.debug('Restored env var: '
                                                     f'{env_var}: {value}')
                                        os.environ[env_var] = value

                            request_id = None
                            try:
                                request_id = await asyncio.to_thread(
                                    sdk.launch,
                                    self.dag,
                                    cluster_name=self.cluster_name,
                                    # We expect to tear down the cluster as soon
                                    # as the job is finished. However, in case
                                    # the controller dies, we may end up with a
                                    # resource leak.
                                    # Ideally, we should autodown to be safe,
                                    # but it's fine to disable it for now, as
                                    # Nebius doesn't support autodown yet.
                                    # TODO(kevin): set down=True once Nebius
                                    # supports autodown.
                                    # idle_minutes_to_autostop=(
                                    #     _AUTODOWN_MINUTES),
                                    # down=True,
                                    _is_launched_by_jobs_controller=True,
                                )
                                logger.debug('sdk.launch request ID: '
                                             f'{request_id}')
                                await asyncio.to_thread(
                                    sdk.stream_and_get,
                                    request_id,
                                )
                            except asyncio.CancelledError:
                                if request_id:
                                    req = await asyncio.to_thread(
                                        sdk.api_cancel, request_id)
                                    logger.debug('sdk.api_cancel request '
                                                 f'ID: {req}')
                                    try:
                                        await asyncio.to_thread(sdk.get, req)
                                    except Exception as e:  # pylint: disable=broad-except
                                        # we must still return a CancelledError
                                        logger.error(
                                            f'Failed to cancel the job: {e}')
                                raise
                            logger.info('Managed job cluster launched.')
                        else:
                            # Get task resources from DAG for resource-aware
                            # scheduling.
                            task_resources = None
                            if self.dag.tasks:
                                task = self.dag.tasks[self.task_id]
                                task_resources = task.resources

                            self.cluster_name = await (asyncio.to_thread(
                                serve_utils.get_next_cluster_name, self.pool,
                                self.job_id, task_resources))
                            if self.cluster_name is None:
                                raise exceptions.NoClusterLaunchedError(
                                    'No cluster name found in the pool.')
                            request_id = None
                            try:
                                request_id = await asyncio.to_thread(
                                    sdk.exec,
                                    self.dag,
                                    cluster_name=self.cluster_name,
                                )
                                logger.debug('sdk.exec request ID: '
                                             f'{request_id}')
                                job_id_on_pool_cluster, _ = (await
                                                             asyncio.to_thread(
                                                                 sdk.get,
                                                                 request_id))
                            except asyncio.CancelledError:
                                if request_id:
                                    req = await asyncio.to_thread(
                                        sdk.api_cancel, request_id)
                                    logger.debug('sdk.api_cancel request '
                                                 f'ID: {req}')
                                    try:
                                        await asyncio.to_thread(sdk.get, req)
                                    except Exception as e:  # pylint: disable=broad-except
                                        # we must still return a CancelledError
                                        logger.error(
                                            f'Failed to cancel the job: {e}')
                                raise
                            assert job_id_on_pool_cluster is not None, (
                                self.cluster_name, self.job_id)
                            self.job_id_on_pool_cluster = job_id_on_pool_cluster
                            await state.set_job_id_on_pool_cluster_async(
                                self.job_id, job_id_on_pool_cluster)
                        logger.info('Managed job cluster launched.')
                    except (exceptions.InvalidClusterNameError,
                            exceptions.NoCloudAccessError,
                            exceptions.ResourcesMismatchError,
                            exceptions.StorageSpecError,
                            exceptions.StorageError) as e:
                        logger.error('Failure happened before provisioning. '
                                     f'{common_utils.format_exception(e)}')
                        if raise_on_failure:
                            raise exceptions.ProvisionPrechecksError(
                                reasons=[e])
                        return None
                    except exceptions.ResourcesUnavailableError as e:
                        # This is raised when the launch fails due to prechecks
                        # or after failing over through all the candidates.
                        # Please refer to the docstring of `sky.launch` for more
                        # details of how the exception will be structured.
                        if not any(
                                isinstance(err,
                                           exceptions.ResourcesUnavailableError)
                                for err in e.failover_history):
                            # _launch() (this function) should fail/exit
                            # directly, if none of the failover reasons were
                            # because of resource unavailability or no failover
                            # was attempted (the optimizer cannot find feasible
                            # resources for requested resources), i.e.,
                            # e.failover_history is empty. Failing directly
                            # avoids the infinite loop of retrying the launch
                            # when, e.g., an invalid cluster name is used and
                            # --retry-until-up is specified.
                            reasons = (e.failover_history
                                       if e.failover_history else [e])
                            reasons_str = '; '.join(
                                common_utils.format_exception(err)
                                for err in reasons)
                            logger.error(
                                'Failure happened before provisioning. '
                                f'Failover reasons: {reasons_str}')
                            if raise_on_failure:
                                raise exceptions.ProvisionPrechecksError(
                                    reasons)
                            return None
                        logger.info('Failed to launch a cluster with error: '
                                    f'{common_utils.format_exception(e)})')
                    except Exception as e:  # pylint: disable=broad-except
                        # If the launch fails, it will be recovered by the
                        # following code.
                        logger.info('Failed to launch a cluster with error: '
                                    f'{common_utils.format_exception(e)})')
                        with ux_utils.enable_traceback():
                            logger.info(
                                f'  Traceback: {traceback.format_exc()}')
                    else:  # No exception, the launch succeeds.
                        # At this point, a sky.launch() has succeeded. Cluster
                        # may be UP (no preemption since) or DOWN (newly
                        # preempted).
                        # Auto-populate instance links if cluster is on a real
                        # cloud
                        if self.cluster_name is not None and self.pool is None:
                            try:
                                handle = await asyncio.to_thread(
                                    global_user_state.
                                    get_handle_from_cluster_name,
                                    self.cluster_name)
                                if (handle is not None and hasattr(
                                        handle, 'cached_cluster_info') and
                                        handle.cached_cluster_info is not None):
                                    cluster_info = handle.cached_cluster_info
                                    instance_links = (instance_links_utils.
                                                      generate_instance_links(
                                                          cluster_info,
                                                          self.cluster_name))
                                    if instance_links:
                                        # Store instance links directly in
                                        # database
                                        await state.update_links_async(
                                            self.job_id, self.task_id,
                                            instance_links)
                                        logger.debug(
                                            f'Auto-populated instance links: '
                                            f'{instance_links}')
                                    else:
                                        logger.debug('Failed to generate '
                                                     'instance links')
                                else:
                                    logger.debug(
                                        'Cluster handle not found or '
                                        'cached cluster info is None so'
                                        'not populating instance links')
                            except Exception as e:  # pylint: disable=broad-except
                                # Don't fail the launch if we can't generate
                                # links
                                logger.debug(
                                    'Failed to auto-populate instance links: '
                                    f'{e}')
                        else:
                            if self.pool:
                                logger.debug('Not populating instance links '
                                             'since the cluster is for a pool')
                            else:
                                logger.debug('Not populating instance links '
                                             'since the cluster name is None')
                        job_submitted_at = await (
                            self._wait_until_job_starts_on_cluster())
                        if job_submitted_at is not None:
                            return job_submitted_at
                        # The job fails to start on the cluster, retry the
                        # launch.
                        # TODO(zhwu): log the unexpected error to usage
                        # collection for future debugging.
                        logger.info(
                            'Failed to successfully submit the job to the '
                            'launched cluster, due to unexpected submission '
                            'errors or the cluster being preempted during '
                            'job submission.')

                    # If we get here, the launch did not succeed. Tear down the
                    # cluster and retry.
                    await asyncio.to_thread(self._cleanup_cluster)
                    if max_retry is not None and retry_cnt >= max_retry:
                        # Retry forever if max_retry is None.
                        if raise_on_failure:
                            with ux_utils.print_exception_no_traceback():
                                raise (
                                    exceptions.ManagedJobReachedMaxRetriesError(
                                        'Resources unavailable: failed to '
                                        f'launch clusters after {max_retry} '
                                        'retries.'))
                        else:
                            return None

                    # Raise NoClusterLaunchedError to indicate that the job is
                    # in retry backoff. This will trigger special handling in
                    # scheduler.schedule_launched().
                    # We will exit the scheduled_launch context so that the
                    # schedule state is ALIVE_BACKOFF during the backoff. This
                    # allows other jobs to launch.
                    raise exceptions.NoClusterLaunchedError()

            except exceptions.NoClusterLaunchedError:
                # Update the status to PENDING during backoff.
                await state.set_backoff_pending_async(self.job_id, self.task_id)
                # Calculate the backoff time and sleep.
                gap_seconds = (backoff.current_backoff()
                               if self.pool is None else 1)
                logger.info('Retrying to launch the cluster in '
                            f'{gap_seconds:.1f} seconds.')
                await asyncio.sleep(gap_seconds)
                continue
            else:
                # The inner loop should either return or throw
                # NoClusterLaunchedError.
                assert False, 'Unreachable'

    def should_restart_on_failure(self,
                                  exit_codes: Optional[List[int]] = None
                                 ) -> bool:
        """Increments counter & checks if job should be restarted on a failure.

        Args:
            exit_codes: List of exit codes from the failed job. If any exit code
                matches recover_on_exit_codes, recovery will be triggered
                regardless of max_restarts_on_errors limit.

        Returns:
            True if the job should be restarted, otherwise False.
        """
        # Check if any exit code matches the configured recover_on_exit_codes
        # This triggers recovery without incrementing the counter
        if exit_codes and self.recover_on_exit_codes:
            for exit_code in exit_codes:
                if exit_code in self.recover_on_exit_codes:
                    logger.info(f'Exit code {exit_code} matched '
                                'recover_on_exit_codes, triggering recovery')
                    return True

        # Otherwise, check the max_restarts_on_errors counter
        self.restart_cnt_on_failure += 1
        if self.restart_cnt_on_failure > self.max_restarts_on_errors:
            return False
        logger.info(f'Restart count {self.restart_cnt_on_failure} '
                    'is less than max_restarts_on_errors, '
                    'restarting job')
        return True


@registry.JOBS_RECOVERY_STRATEGY_REGISTRY.type_register(name='FAILOVER',
                                                        default=False)
class FailoverStrategyExecutor(StrategyExecutor):
    """Failover strategy: wait in same region and failover after timeout."""

    _MAX_RETRY_CNT = 240  # Retry for 4 hours.

    def __init__(
        self,
        cluster_name: Optional[str],
        backend: 'backends.Backend',
        task: 'task_lib.Task',
        max_restarts_on_errors: int,
        job_id: int,
        task_id: int,
        pool: Optional[str],
        starting: Set[int],
        starting_lock: asyncio.Lock,
        starting_signal: asyncio.Condition,
        recover_on_exit_codes: Optional[List[int]] = None,
    ) -> None:
        super().__init__(cluster_name, backend, task, max_restarts_on_errors,
                         job_id, task_id, pool, starting, starting_lock,
                         starting_signal, recover_on_exit_codes)
        # Note down the cloud/region of the launched cluster, so that we can
        # first retry in the same cloud/region. (Inside recover() we may not
        # rely on cluster handle, as it can be None if the cluster is
        # preempted.)
        self._launched_resources: Optional['resources.Resources'] = None

    async def _launch(self,
                      max_retry: Optional[int] = 3,
                      raise_on_failure: bool = True,
                      recovery: bool = False) -> Optional[float]:
        job_submitted_at = await super()._launch(max_retry, raise_on_failure,
                                                 recovery)
        if job_submitted_at is not None and self.cluster_name is not None:
            # Only record the cloud/region if the launch is successful.
            handle = await asyncio.to_thread(
                global_user_state.get_handle_from_cluster_name,
                self.cluster_name)
            assert isinstance(handle, backends.CloudVmRayResourceHandle), (
                'Cluster should be launched.', handle)
            launched_resources = handle.launched_resources
            self._launched_resources = launched_resources

            # Persist infra info to database for sorting/filtering
            if launched_resources is not None:
                cloud = str(launched_resources.cloud
                           ) if launched_resources.cloud else None
                await asyncio.to_thread(
                    state.set_job_infra,
                    self.job_id,
                    cloud=cloud,
                    region=launched_resources.region,
                    zone=launched_resources.zone,
                )
        else:
            self._launched_resources = None
        return job_submitted_at

    async def recover(self) -> float:
        # 1. Cancel the jobs and launch the cluster with the STOPPED status,
        #    so that it will try on the current region first until timeout.
        # 2. Tear down the cluster, if the step 1 failed to launch the cluster.
        # 3. Launch the cluster with no cloud/region constraint or respect the
        #    original user specification.

        # Step 1
        await self._try_cancel_jobs()

        while True:
            # Add region constraint to the task, to retry on the same region
            # first (if valid).
            if self._launched_resources is not None:
                task = self.dag.tasks[0]
                original_resources = task.resources
                launched_cloud = self._launched_resources.cloud
                launched_region = self._launched_resources.region
                new_resources = self._launched_resources.copy(
                    cloud=launched_cloud, region=launched_region, zone=None)
                task.set_resources({new_resources})
                # Not using self.launch to avoid the retry until up logic.
                job_submitted_at = await self._launch(raise_on_failure=False,
                                                      recovery=True)
                # Restore the original dag, i.e. reset the region constraint.
                task.set_resources(original_resources)
                if job_submitted_at is not None:
                    return job_submitted_at

            # Step 2
            logger.debug('Terminating unhealthy cluster and reset cloud '
                         'region.')
            await asyncio.to_thread(self._cleanup_cluster)

            # Step 3
            logger.debug('Relaunch the cluster  without constraining to prior '
                         'cloud/region.')
            # Not using self.launch to avoid the retry until up logic.
            job_submitted_at = await self._launch(max_retry=self._MAX_RETRY_CNT,
                                                  raise_on_failure=False,
                                                  recovery=True)
            if job_submitted_at is None:
                # Failed to launch the cluster.
                gap_seconds = self.RETRY_INIT_GAP_SECONDS
                logger.info('Retrying to recover the cluster in '
                            f'{gap_seconds:.1f} seconds.')
                await asyncio.sleep(gap_seconds)
                continue

            return job_submitted_at


@registry.JOBS_RECOVERY_STRATEGY_REGISTRY.type_register(
    name='EAGER_NEXT_REGION', default=True)
class EagerFailoverStrategyExecutor(FailoverStrategyExecutor):
    """Eager failover strategy.

    This strategy is an extension of the FAILOVER strategy. Instead of waiting
    in the same region when the preemption happens, it immediately terminates
    the cluster and relaunches it in a different region. This is based on the
    observation that the preemption is likely to happen again shortly in the
    same region, so trying other regions first is more likely to get a longer
    running cluster.

    Example: Assume the user has access to 3 regions, R1, R2, R3, in that price
    order. Then the following are some possible event sequences:

        R1Z1 (preempted) -> R2 (success)

        R1Z1 (preempted) -> R2 (failed to launch) -> R3 (success)

        R1Z1 (preempted) -> R2 (failed to launch) -> R3 (failed to launch)
                                                  -> R1Z2 (success)

        R1Z1 (preempted) -> R2 (failed to launch) -> R3 (failed to launch)
                                                  -> R1Z1 (success)
    """

    async def recover(self) -> float:
        # 1. Terminate the current cluster
        # 2. Launch again by explicitly blocking the previously launched region
        # (this will failover through the entire search space except the
        # previously launched region)
        # 3. (If step 2 failed) Retry forever: Launch again with no blocked
        # locations (this will failover through the entire search space)
        #
        # The entire search space is defined by the original task request,
        # task.resources.

        # Step 1
        logger.debug('Terminating unhealthy cluster and reset cloud region.')
        await asyncio.to_thread(self._cleanup_cluster)

        # Step 2
        logger.debug('Relaunch the cluster skipping the previously launched '
                     'cloud/region.')
        if self._launched_resources is not None:
            task = self.dag.tasks[0]
            requested_resources = self._launched_resources
            if (requested_resources.region is None and
                    requested_resources.zone is None):
                # Optimization: We only block the previously launched region,
                # if the requested resources does not specify a region or zone,
                # because, otherwise, we will spend unnecessary time for
                # skipping the only specified region/zone.
                launched_cloud = self._launched_resources.cloud
                launched_region = self._launched_resources.region
                task.blocked_resources = {
                    requested_resources.copy(cloud=launched_cloud,
                                             region=launched_region)
                }
                # Not using self.launch to avoid the retry until up logic.
                job_submitted_at = await self._launch(raise_on_failure=False,
                                                      recovery=True)
                task.blocked_resources = None
                if job_submitted_at is not None:
                    return job_submitted_at

        while True:
            # Step 3
            logger.debug('Relaunch the cluster without constraining to prior '
                         'cloud/region.')
            # Not using self.launch to avoid the retry until up logic.
            job_submitted_at = await self._launch(max_retry=self._MAX_RETRY_CNT,
                                                  raise_on_failure=False,
                                                  recovery=True)
            if job_submitted_at is None:
                # Failed to launch the cluster.
                gap_seconds = self.RETRY_INIT_GAP_SECONDS
                logger.info('Retrying to recover the cluster in '
                            f'{gap_seconds:.1f} seconds.')
                await asyncio.sleep(gap_seconds)
                continue

            return job_submitted_at


def _get_logger_file(file_logger: logging.Logger) -> Optional[str]:
    """Gets the file path that the logger writes to."""
    for handler in file_logger.handlers:
        if isinstance(handler, logging.FileHandler):
            return handler.baseFilename
    return None
