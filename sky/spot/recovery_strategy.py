"""The strategy to handle launching/recovery/termination of spot clusters."""
import time
import typing
from typing import Callable, Optional

import sky
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.skylet import job_lib
from sky.spot import spot_utils
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import task as task_lib

logger = sky_logging.init_logger(__name__)

SPOT_STRATEGIES = dict()
SPOT_DEFAULT_STRATEGY = None

# Waiting time for job from INIT/PENDING to RUNNING
# 10 * JOB_STARTED_STATUS_CHECK_GAP_SECONDS = 10 * 5 = 50 seconds
MAX_JOB_CHECKING_RETRY = 10


class StrategyExecutor:
    """Handle each launching, recovery and termination of the spot clusters."""

    RETRY_INIT_GAP_SECONDS = 60

    def __init__(self, cluster_name: str, backend: 'backends.Backend',
                 task: 'task_lib.Task', retry_until_up: bool,
                 signal_handler: Callable) -> None:
        """Initialize the strategy executor.

        Args:
            cluster_name: The name of the cluster.
            backend: The backend to use. Only CloudVMRayBackend is supported.
            task: The task to execute.
            retry_until_up: Whether to retry until the cluster is up.
            signal_handler: The signal handler that will raise an exception if a
                SkyPilot signal is received.
        """
        self.dag = sky.Dag()
        self.dag.add(task)
        self.cluster_name = cluster_name
        self.backend = backend
        self.retry_until_up = retry_until_up
        self.signal_handler = signal_handler

    def __init_subclass__(cls, name: str, default: bool = False):
        SPOT_STRATEGIES[name] = cls
        if default:
            global SPOT_DEFAULT_STRATEGY
            assert SPOT_DEFAULT_STRATEGY is None, (
                'Only one strategy can be default.')
            SPOT_DEFAULT_STRATEGY = name

    @classmethod
    def make(cls, cluster_name: str, backend: 'backends.Backend',
             task: 'task_lib.Task', retry_until_up: bool,
             signal_handler: Callable) -> 'StrategyExecutor':
        """Create a strategy from a task."""
        resources = task.resources
        assert len(resources) == 1, 'Only one resource is supported.'
        resources: 'sky.Resources' = list(resources)[0]

        spot_recovery = resources.spot_recovery
        assert spot_recovery is not None, (
            'spot_recovery is required to use spot strategy.')
        # Remove the spot_recovery field from the resources, as the strategy
        # will be handled by the strategy class.
        task.set_resources({resources.copy(spot_recovery=None)})
        return SPOT_STRATEGIES[spot_recovery](cluster_name, backend, task,
                                              retry_until_up, signal_handler)

    def launch(self) -> Optional[float]:
        """Launch the spot cluster for the first time.

        It can fail if resource is not available. Need to check the cluster
        status, after calling.

        Returns: The job's start timestamp, or None if failed to start.
        """
        if self.retry_until_up:
            return self._launch(max_retry=None)
        return self._launch()

    def recover(self) -> float:
        """Relaunch the spot cluster after failure and wait until job starts.

        When recover() is called the cluster should be in STOPPED status (i.e.
        partially down).

        Returns: The timestamp job started.
        """
        raise NotImplementedError

    def terminate_cluster(self, max_retry: int = 3) -> None:
        """Terminate the spot cluster."""
        retry_cnt = 0
        while True:
            try:
                handle = global_user_state.get_handle_from_cluster_name(
                    self.cluster_name)
                if handle is None:
                    return
                self.backend.teardown(handle, terminate=True)
                return
            except Exception as e:  # pylint: disable=broad-except
                retry_cnt += 1
                if retry_cnt >= max_retry:
                    raise RuntimeError('Failed to terminate the spot cluster '
                                       f'{self.cluster_name}.') from e
                logger.error('Failed to terminate the spot cluster '
                             f'{self.cluster_name}. Retrying.')

    def _try_cancel_all_jobs(self):
        handle = global_user_state.get_handle_from_cluster_name(
            self.cluster_name)
        try:
            self.backend.cancel_jobs(handle, jobs=None)
        except Exception as e:  # pylint: disable=broad-except
            # Ignore the failure as the cluster can be totally stopped, and the
            # job canceling can get connection error.
            logger.info(
                f'Ignoring the job cancellation failure (Exception: {e}); '
                'the spot cluster is likely completely stopped.')

    def _launch(self, max_retry=3, raise_on_failure=True) -> Optional[float]:
        """Implementation of launch().

        The function will wait until the job starts running, but will leave the
        handling for the preemption to the caller.

        Args:
            max_retry: The maximum number of retries. If None, retry forever.
            raise_on_failure: Whether to raise an exception if the launch fails.
        """
        # TODO(zhwu): handle the failure during `preparing sky runtime`.
        retry_cnt = 0
        backoff = common_utils.Backoff(self.RETRY_INIT_GAP_SECONDS)
        while True:
            retry_cnt += 1
            # Check the signal every time to be more responsive to user
            # signals, such as Cancel.
            self.signal_handler()
            retry_launch = False
            exception = None
            try:
                usage_lib.messages.usage.set_internal()
                sky.launch(self.dag,
                           cluster_name=self.cluster_name,
                           detach_run=True)
                logger.info('Spot cluster launched.')
            except exceptions.InvalidClusterNameError as e:
                # The cluster name is too long.
                raise exceptions.ResourcesUnavailableError(str(e)) from e
            except Exception as e:  # pylint: disable=broad-except
                # If the launch fails, it will be recovered by the following
                # code.
                logger.info('Failed to launch the spot cluster with error: '
                            f'{type(e)}: {e}')
                retry_launch = True
                exception = e

            # At this point, a sky.launch() has succeeded. Cluster may be
            # UP (no preemption since) or DOWN (newly preempted).
            status = None
            job_checking_retry_cnt = 0
            while not retry_launch:
                job_checking_retry_cnt += 1
                if job_checking_retry_cnt >= MAX_JOB_CHECKING_RETRY:
                    # Avoid the infinite loop, if any bug happens.
                    retry_launch = True
                    # TODO(zhwu): log the unexpected error to usage collection
                    # for future debugging.
                    logger.info(
                        'Failed to get the job status, due to unexpected '
                        'job submission error.')
                    break

                try:
                    cluster_status, _ = (
                        backend_utils.refresh_cluster_status_handle(
                            self.cluster_name, force_refresh=True))
                except Exception as e:  # pylint: disable=broad-except
                    # If any unexpected error happens, retry the job checking
                    # loop.
                    # TODO(zhwu): log the unexpected error to usage collection
                    # for future debugging.
                    logger.info(f'Unexpected exception: {e}\nFailed to get the '
                                'refresh the cluster status. Retrying.')
                    continue
                if cluster_status != global_user_state.ClusterStatus.UP:
                    # The cluster can be preempted before the job is launched.
                    # Break to let the retry launch kick in.
                    logger.info('The cluster is preempted before the job '
                                'starts.')
                    # TODO(zhwu): we should recover the preemption with the
                    # recovery strategy instead of the current while loop.
                    retry_launch = True
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
                if status not in (None, job_lib.JobStatus.INIT,
                                  job_lib.JobStatus.PENDING):
                    try:
                        launch_time = spot_utils.get_job_timestamp(
                            self.backend, self.cluster_name, get_end_time=False)
                        return launch_time
                    except Exception as e:  # pylint: disable=broad-except
                        # If we failed to get the job timestamp, we will retry
                        # job checking loop.
                        logger.info(f'Unexpected Exception: {e}\nFailed to get '
                                    'the job start timestamp. Retrying.')
                        continue
                # Wait for the job to be started
                time.sleep(spot_utils.JOB_STARTED_STATUS_CHECK_GAP_SECONDS)

            assert retry_launch

            self.terminate_cluster()
            if max_retry is not None and retry_cnt >= max_retry:
                # Retry forever if max_retry is None.
                if raise_on_failure:
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.ResourcesUnavailableError(
                            'Failed to launch the spot cluster after '
                            f'{max_retry} retries.') from exception
                else:
                    return None
            gap_seconds = backoff.current_backoff()
            logger.info('Retrying to launch the spot cluster in '
                        f'{gap_seconds:.1f} seconds.')
            time.sleep(gap_seconds)


class FailoverStrategyExecutor(StrategyExecutor, name='FAILOVER', default=True):
    """Failover strategy: wait in same region and failover after timout."""

    _MAX_RETRY_CNT = 240  # Retry for 4 hours.

    def recover(self) -> float:
        # 1. Cancel the jobs and launch the cluster with the STOPPED status,
        #    so that it will try on the current region first until timeout.
        # 2. Tear down the cluster, if the step 1 failed to launch the cluster.
        # 3. Launch the cluster with no cloud/region constraint or respect the
        #    original user specification.

        # Step 1
        self._try_cancel_all_jobs()

        # Retry the entire block until the cluster is up, so that the ratio of
        # the time spent in the current region and the time spent in the other
        # region is consistent during the retry.
        handle = global_user_state.get_handle_from_cluster_name(
            self.cluster_name)
        while True:
            # Add region constraint to the task, to retry on the same region
            # first.
            task = self.dag.tasks[0]
            resources = list(task.resources)[0]
            original_resources = resources

            launched_cloud = handle.launched_resources.cloud
            launched_region = handle.launched_resources.region
            new_resources = resources.copy(cloud=launched_cloud,
                                           region=launched_region)
            task.set_resources({new_resources})
            # Not using self.launch to avoid the retry until up logic.
            launched_time = self._launch(raise_on_failure=False)
            # Restore the original dag, i.e. reset the region constraint.
            task.set_resources({original_resources})
            if launched_time is not None:
                return launched_time

            # Step 2
            logger.debug('Terminating unhealthy spot cluster.')
            self.terminate_cluster()

            # Step 3
            logger.debug('Relaunch the cluster  without constraining to prior '
                         'cloud/region.')
            # Not using self.launch to avoid the retry until up logic.
            launched_time = self._launch(max_retry=self._MAX_RETRY_CNT,
                                         raise_on_failure=False)
            if launched_time is None:
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

            return launched_time
