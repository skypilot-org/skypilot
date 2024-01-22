"""The strategy to handle launching/recovery/termination of spot clusters.

In the YAML file, the user can specify the strategy to use for spot jobs.

resources:
    spot_recovery: EAGER_NEXT_REGION
"""
import time
import traceback
import typing
from typing import Optional

import sky
from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import status_lib
from sky.backends import backend_utils
from sky.skylet import job_lib
from sky.spot import spot_utils
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import task as task_lib

logger = sky_logging.init_logger(__name__)

SPOT_STRATEGIES = {}
SPOT_DEFAULT_STRATEGY = None

# Waiting time for job from INIT/PENDING to RUNNING
# 10 * JOB_STARTED_STATUS_CHECK_GAP_SECONDS = 10 * 5 = 50 seconds
MAX_JOB_CHECKING_RETRY = 10


def terminate_cluster(cluster_name: str, max_retry: int = 3) -> None:
    """Terminate the spot cluster."""
    retry_cnt = 0
    while True:
        try:
            usage_lib.messages.usage.set_internal()
            sky.down(cluster_name)
            return
        except ValueError:
            # The cluster is already down.
            return
        except Exception as e:  # pylint: disable=broad-except
            retry_cnt += 1
            if retry_cnt >= max_retry:
                raise RuntimeError('Failed to terminate the spot cluster '
                                   f'{cluster_name}.') from e
            logger.error('Failed to terminate the spot cluster '
                         f'{cluster_name}. Retrying.'
                         f'Details: {common_utils.format_exception(e)}')
            with ux_utils.enable_traceback():
                logger.error(f'  Traceback: {traceback.format_exc()}')


class StrategyExecutor:
    """Handle each launching, recovery and termination of the spot clusters."""

    RETRY_INIT_GAP_SECONDS = 60

    def __init__(self, cluster_name: str, backend: 'backends.Backend',
                 task: 'task_lib.Task', retry_until_up: bool) -> None:
        """Initialize the strategy executor.

        Args:
            cluster_name: The name of the cluster.
            backend: The backend to use. Only CloudVMRayBackend is supported.
            task: The task to execute.
            retry_until_up: Whether to retry until the cluster is up.
        """
        assert isinstance(backend, backends.CloudVmRayBackend), (
            'Only CloudVMRayBackend is supported.')
        self.dag = sky.Dag()
        self.dag.add(task)
        self.cluster_name = cluster_name
        self.backend = backend
        self.retry_until_up = retry_until_up

    def __init_subclass__(cls, name: str, default: bool = False):
        SPOT_STRATEGIES[name] = cls
        if default:
            global SPOT_DEFAULT_STRATEGY
            assert SPOT_DEFAULT_STRATEGY is None, (
                'Only one strategy can be default.')
            SPOT_DEFAULT_STRATEGY = name

    @classmethod
    def make(cls, cluster_name: str, backend: 'backends.Backend',
             task: 'task_lib.Task', retry_until_up: bool) -> 'StrategyExecutor':
        """Create a strategy from a task."""

        resource_list = list(task.resources)
        spot_recovery = resource_list[0].spot_recovery
        for resource in resource_list:
            if resource.spot_recovery != spot_recovery:
                raise ValueError(
                    'The spot recovery strategy should be the same for all '
                    'resources.')
        # Remove the spot_recovery field from the resources, as the strategy
        # will be handled by the strategy class.
        new_resources_list = [r.copy(spot_recovery=None) for r in resource_list]
        # set the new_task_resources to be the same type (list or set) as the
        # original task.resources
        task.set_resources(type(task.resources)(new_resources_list))
        return SPOT_STRATEGIES[spot_recovery](cluster_name, backend, task,
                                              retry_until_up)

    def launch(self) -> float:
        """Launch the spot cluster for the first time.

        It can fail if resource is not available. Need to check the cluster
        status, after calling.

        Returns: The job's submit timestamp, on success (otherwise, an
            exception is raised).

        Raises: Please refer to the docstring of self._launch().
        """

        if self.retry_until_up:
            job_submit_at = self._launch(max_retry=None)
        else:
            job_submit_at = self._launch()
        assert job_submit_at is not None
        return job_submit_at

    def recover(self) -> float:
        """Relaunch the spot cluster after failure and wait until job starts.

        When recover() is called the cluster should be in STOPPED status (i.e.
        partially down).

        Returns: The timestamp job started.
        """
        raise NotImplementedError

    def _try_cancel_all_jobs(self):
        handle = global_user_state.get_handle_from_cluster_name(
            self.cluster_name)
        if handle is None:
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
            sky.cancel(cluster_name=self.cluster_name,
                       all=True,
                       _try_cancel_if_cluster_is_init=True)
        except Exception as e:  # pylint: disable=broad-except
            logger.info('Failed to cancel the job on the cluster. The cluster '
                        'might be already down or the head node is preempted.'
                        '\n  Detailed exception: '
                        f'{common_utils.format_exception(e)}\n'
                        'Terminating the cluster explicitly to ensure no '
                        'remaining job process interferes with recovery.')
            terminate_cluster(self.cluster_name)

    def _wait_until_job_starts_on_cluster(self) -> Optional[float]:
        """Wait for MAX_JOB_CHECKING_RETRY times until job starts on the cluster

        Returns:
            The timestamp of when the job is submitted, or None if failed to
            submit.
        """
        status = None
        job_checking_retry_cnt = 0
        while job_checking_retry_cnt < MAX_JOB_CHECKING_RETRY:
            # Avoid the infinite loop, if any bug happens.
            job_checking_retry_cnt += 1
            try:
                cluster_status, _ = (
                    backend_utils.refresh_cluster_status_handle(
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
            if cluster_status != status_lib.ClusterStatus.UP:
                # The cluster can be preempted before the job is
                # launched.
                # Break to let the retry launch kick in.
                logger.info('The cluster is preempted before the job '
                            'is submitted.')
                # TODO(zhwu): we should recover the preemption with the
                # recovery strategy instead of the current while loop.
                break

            try:
                status = spot_utils.get_job_status(self.backend,
                                                   self.cluster_name)
            except Exception as e:  # pylint: disable=broad-except
                # If any unexpected error happens, retry the job checking
                # loop.
                # Note: the CommandError is already handled in the
                # get_job_status, so it should not happen here.
                # TODO(zhwu): log the unexpected error to usage collection
                # for future debugging.
                logger.info(f'Unexpected exception: {e}\nFailed to get the '
                            'job status. Retrying.')
                continue

            # Check the job status until it is not in initialized status
            if status is not None and status > job_lib.JobStatus.INIT:
                try:
                    job_submitted_at = spot_utils.get_job_timestamp(
                        self.backend, self.cluster_name, get_end_time=False)
                    return job_submitted_at
                except Exception as e:  # pylint: disable=broad-except
                    # If we failed to get the job timestamp, we will retry
                    # job checking loop.
                    logger.info(f'Unexpected Exception: {e}\nFailed to get '
                                'the job start timestamp. Retrying.')
                    continue
            # Wait for the job to be started
            time.sleep(spot_utils.JOB_STARTED_STATUS_CHECK_GAP_SECONDS)
        return None

    def _launch(self,
                max_retry: Optional[int] = 3,
                raise_on_failure: bool = True) -> Optional[float]:
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
            exceptions.SpotJobReachedMaxRetryError: This will be raised when
                all prechecks passed but the maximum number of retries is
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
                usage_lib.messages.usage.set_internal()
                # Detach setup, so that the setup failure can be detected
                # by the controller process (job_status -> FAILED_SETUP).
                sky.launch(self.dag,
                           cluster_name=self.cluster_name,
                           detach_setup=True,
                           detach_run=True,
                           _is_launched_by_spot_controller=True)
                logger.info('Spot cluster launched.')
            except (exceptions.InvalidClusterNameError,
                    exceptions.NoCloudAccessError,
                    exceptions.ResourcesMismatchError) as e:
                logger.error('Failure happened before provisioning. '
                             f'{common_utils.format_exception(e)}')
                if raise_on_failure:
                    raise exceptions.ProvisionPrechecksError(reasons=[e])
                return None
            except exceptions.ResourcesUnavailableError as e:
                # This is raised when the launch fails due to prechecks or
                # after failing over through all the candidates.
                # Please refer to the docstring of `sky.launch` for more
                # details of how the exception will be structured.
                if not any(
                        isinstance(err, exceptions.ResourcesUnavailableError)
                        for err in e.failover_history):
                    # _launch() (this function) should fail/exit directly, if
                    # none of the failover reasons were because of resource
                    # unavailability or no failover was attempted (the optimizer
                    # cannot find feasible resources for requested resources),
                    # i.e., e.failover_history is empty.
                    # Failing directly avoids the infinite loop of retrying
                    # the launch when, e.g., an invalid cluster name is used
                    # and --retry-until-up is specified.
                    reasons = (e.failover_history
                               if e.failover_history else [e])
                    reasons_str = '; '.join(
                        common_utils.format_exception(err) for err in reasons)
                    logger.error(
                        'Failure happened before provisioning. Failover '
                        f'reasons: {reasons_str}')
                    if raise_on_failure:
                        raise exceptions.ProvisionPrechecksError(
                            reasons=reasons)
                    return None
                logger.info('Failed to launch the spot cluster with error: '
                            f'{common_utils.format_exception(e)})')
            except Exception as e:  # pylint: disable=broad-except
                # If the launch fails, it will be recovered by the following
                # code.
                logger.info('Failed to launch the spot cluster with error: '
                            f'{common_utils.format_exception(e)})')
                with ux_utils.enable_traceback():
                    logger.info(f'  Traceback: {traceback.format_exc()}')
            else:  # No exception, the launch succeeds.
                # At this point, a sky.launch() has succeeded. Cluster may be
                # UP (no preemption since) or DOWN (newly preempted).
                job_submitted_at = self._wait_until_job_starts_on_cluster()
                if job_submitted_at is not None:
                    return job_submitted_at
                # The job fails to start on the spot cluster, retry the launch.
                # TODO(zhwu): log the unexpected error to usage collection
                # for future debugging.
                logger.info(
                    'Failed to successfully submit the job to the '
                    'launched cluster, due to unexpected submission errors or '
                    'the cluster being preempted during job submission.')

            terminate_cluster(self.cluster_name)
            if max_retry is not None and retry_cnt >= max_retry:
                # Retry forever if max_retry is None.
                if raise_on_failure:
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.SpotJobReachedMaxRetriesError(
                            'Resources unavailable: failed to launch the spot '
                            f'cluster after {max_retry} retries.')
                else:
                    return None
            gap_seconds = backoff.current_backoff()
            logger.info('Retrying to launch the spot cluster in '
                        f'{gap_seconds:.1f} seconds.')
            time.sleep(gap_seconds)


class FailoverStrategyExecutor(StrategyExecutor, name='FAILOVER',
                               default=False):
    """Failover strategy: wait in same region and failover after timeout."""

    _MAX_RETRY_CNT = 240  # Retry for 4 hours.

    def __init__(self, cluster_name: str, backend: 'backends.Backend',
                 task: 'task_lib.Task', retry_until_up: bool) -> None:
        super().__init__(cluster_name, backend, task, retry_until_up)
        # Note down the cloud/region of the launched cluster, so that we can
        # first retry in the same cloud/region. (Inside recover() we may not
        # rely on cluster handle, as it can be None if the cluster is
        # preempted.)
        self._launched_resources: Optional['sky.resources.Resources'] = None

    def _launch(self,
                max_retry: Optional[int] = 3,
                raise_on_failure: bool = True) -> Optional[float]:
        job_submitted_at = super()._launch(max_retry, raise_on_failure)
        if job_submitted_at is not None:
            # Only record the cloud/region if the launch is successful.
            handle = global_user_state.get_handle_from_cluster_name(
                self.cluster_name)
            assert isinstance(handle, backends.CloudVmRayResourceHandle), (
                'Cluster should be launched.', handle)
            launched_resources = handle.launched_resources
            self._launched_resources = launched_resources
        else:
            self._launched_resources = None
        return job_submitted_at

    def recover(self) -> float:
        # 1. Cancel the jobs and launch the cluster with the STOPPED status,
        #    so that it will try on the current region first until timeout.
        # 2. Tear down the cluster, if the step 1 failed to launch the cluster.
        # 3. Launch the cluster with no cloud/region constraint or respect the
        #    original user specification.

        # Step 1
        self._try_cancel_all_jobs()

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
                job_submitted_at = self._launch(raise_on_failure=False)
                # Restore the original dag, i.e. reset the region constraint.
                task.set_resources(original_resources)
                if job_submitted_at is not None:
                    return job_submitted_at

            # Step 2
            logger.debug('Terminating unhealthy spot cluster and '
                         'reset cloud region.')
            terminate_cluster(self.cluster_name)

            # Step 3
            logger.debug('Relaunch the cluster  without constraining to prior '
                         'cloud/region.')
            # Not using self.launch to avoid the retry until up logic.
            job_submitted_at = self._launch(max_retry=self._MAX_RETRY_CNT,
                                            raise_on_failure=False)
            if job_submitted_at is None:
                # Failed to launch the cluster.
                if self.retry_until_up:
                    gap_seconds = self.RETRY_INIT_GAP_SECONDS
                    logger.info('Retrying to recover the spot cluster in '
                                f'{gap_seconds:.1f} seconds.')
                    time.sleep(gap_seconds)
                    continue
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ResourcesUnavailableError(
                        f'Failed to recover the spot cluster after retrying '
                        f'{self._MAX_RETRY_CNT} times.')

            return job_submitted_at


class EagerFailoverStrategyExecutor(FailoverStrategyExecutor,
                                    name='EAGER_NEXT_REGION',
                                    default=True):
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

    def recover(self) -> float:
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
        logger.debug('Terminating unhealthy spot cluster and '
                     'reset cloud region.')
        terminate_cluster(self.cluster_name)

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
                job_submitted_at = self._launch(raise_on_failure=False)
                task.blocked_resources = None
                if job_submitted_at is not None:
                    return job_submitted_at

        while True:
            # Step 3
            logger.debug('Relaunch the cluster without constraining to prior '
                         'cloud/region.')
            # Not using self.launch to avoid the retry until up logic.
            job_submitted_at = self._launch(max_retry=self._MAX_RETRY_CNT,
                                            raise_on_failure=False)
            if job_submitted_at is None:
                # Failed to launch the cluster.
                if self.retry_until_up:
                    gap_seconds = self.RETRY_INIT_GAP_SECONDS
                    logger.info('Retrying to recover the spot cluster in '
                                f'{gap_seconds:.1f} seconds.')
                    time.sleep(gap_seconds)
                    continue
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ResourcesUnavailableError(
                        f'Failed to recover the spot cluster after retrying '
                        f'{self._MAX_RETRY_CNT} times.')

            return job_submitted_at
