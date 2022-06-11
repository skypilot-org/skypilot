"""The strategy to handle launching/recovery/termination of spot clusters."""
import time
import typing
from typing import Optional

import sky
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.skylet import job_lib
from sky.spot import spot_utils

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import task as task_lib

logger = sky_logging.init_logger(__name__)

SPOT_STRATEGIES = dict()
SPOT_DEFAULT_STRATEGY = None


class StrategyExecutor:
    """Handle each launching, recovery and termination of the spot clusters."""

    def __init__(self, cluster_name: str, backend: 'backends.Backend',
                 task: 'task_lib.Task') -> None:
        self.dag = sky.Dag()
        self.dag.add(task)
        self.cluster_name = cluster_name
        self.backend = backend

    def __init_subclass__(cls, name: str, default: bool = False):
        SPOT_STRATEGIES[name] = cls
        if default:
            global SPOT_DEFAULT_STRATEGY
            assert SPOT_DEFAULT_STRATEGY is None, (
                'Only one strategy can be default.')
            SPOT_DEFAULT_STRATEGY = name

    @classmethod
    def make(cls, cluster_name: str, backend: 'backends.Backend',
             task: 'task_lib.Task') -> 'StrategyExecutor':
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
        return SPOT_STRATEGIES[spot_recovery](cluster_name, backend, task)

    def launch(self,
               max_retry=3,
               retry_init_gap_seconds=60,
               raise_on_failure=True) -> Optional[float]:
        """Launch the spot cluster for the first time.

        It can fail if resource is not available. Need to check the cluster
        status, after calling.

        Returns: The job's start timestamp, or None if failed to start.
        """
        # TODO(zhwu): handle the failure during `preparing sky runtime`.
        retry_cnt = 0
        backoff = backend_utils.Backoff(retry_init_gap_seconds)
        while True:
            retry_cnt += 1
            try:
                sky.launch(self.dag,
                           cluster_name=self.cluster_name,
                           detach_run=True)
                logger.info('Spot cluster launched.')
            except SystemExit:
                # If the launch fails, it will be recovered by the following
                # code.
                logger.info('Failed to launch the spot cluster.')

            cluster_status, _ = backend_utils.refresh_cluster_status_handle(
                self.cluster_name, force_refresh=True)
            if cluster_status == global_user_state.ClusterStatus.UP:
                # Wait the job to be started
                status = spot_utils.get_job_status(self.backend,
                                                   self.cluster_name)
                while status is None or status == job_lib.JobStatus.INIT:
                    time.sleep(spot_utils.JOB_STARTED_STATUS_CHECK_GAP_SECONDS)
                    status = spot_utils.get_job_status(self.backend,
                                                       self.cluster_name)
                launch_time = spot_utils.get_job_timestamp(self.backend,
                                                           self.cluster_name,
                                                           get_end_time=False)
                return launch_time

            # TODO(zhwu): maybe exponential backoff is better?
            if retry_cnt >= max_retry:
                if raise_on_failure:
                    raise exceptions.ResourcesUnavailableError(
                        f'Failed to launch the spot cluster after {max_retry} '
                        'retries.')
                else:
                    return None
            gap_seconds = backoff.current_backoff()
            logger.info(
                f'Retrying to launch the spot cluster in {gap_seconds:.1f} '
                'seconds.')
            time.sleep(gap_seconds)

    def recover(self) -> float:
        """Relaunch the spot cluster after failure and wait until job starts.

        When recover() is called the cluster should be in STOPPED status (i.e.
        partially down).

        Returns: The timestamp job started.
        """
        raise NotImplementedError

    def terminate_cluster(self):
        """Terminate the spot cluster."""
        handle = global_user_state.get_handle_from_cluster_name(
            self.cluster_name)
        if handle is not None:
            self.backend.teardown(handle, terminate=True)


class FailoverStrategyExecutor(StrategyExecutor, name='FAILOVER', default=True):
    """Failover strategy: wait in same region and failover after timout."""

    _MAX_RETRY_CNT = 240  # Retry for 4 hours.
    _RETRY_INIT_GAP_SECONDS = 60

    def recover(self) -> float:
        # 1. Cancel the jobs and launch the cluster with the STOPPED status,
        #    so that it will try on the current region first until timeout.
        # 2. Tear down the cluster, if the step 1 failed to launch the cluster.
        # 3. Launch the cluster with no cloud/region constraint or respect the
        #    original user specification.

        # Step 1
        # Cluster should be in STOPPED status.
        handle = global_user_state.get_handle_from_cluster_name(
            self.cluster_name)
        try:
            self.backend.cancel_jobs(handle, jobs=None)
        except SystemExit:
            # Ignore the failure as the cluster can be totally stopped, and the
            # job canceling can get connection error.
            logger.info('Ignoring the job cancellation failure; the spot '
                        'cluster is likely completely stopped. Recovering.')

        # Add region constraint to the task, to retry on the same region first.
        task = self.dag.tasks[0]
        resources = list(task.resources)[0]
        original_resources = resources.copy()
        launched_region = handle.launched_resources.region
        new_resources = resources.copy(region=launched_region)
        task.set_resources({new_resources})
        launched_time = self.launch(raise_on_failure=False)
        # Restore the original dag, i.e. reset the region constraint.
        task.set_resources({original_resources})
        if launched_time is not None:
            return launched_time

        # Step 2
        self.terminate_cluster()

        # Step 3
        launched_time = self.launch(
            max_retry=self._MAX_RETRY_CNT,
            retry_init_gap_seconds=self._RETRY_INIT_GAP_SECONDS,
            raise_on_failure=False)
        if launched_time is None:
            raise exceptions.ResourcesUnavailableError(
                f'Failed to recover the spot cluster after retrying '
                f'{self._MAX_RETRY_CNT} times.')
        return launched_time
