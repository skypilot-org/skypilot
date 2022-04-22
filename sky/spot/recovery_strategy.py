"""The strategy to handle launching/recovery/termination of spot clusters."""
import time
import typing

import sky
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils

if typing.TYPE_CHECKING:
    from sky.backends import Backend
    from sky import task as task_lib

logger = sky_logging.init_logger(__name__)

SPOT_STRATEGIES = dict()


class Strategy:
    """Handle each launching, recovery and termination of the spot clusters."""

    def __init__(self, cluster_name: str, backend: 'Backend',
                 task: 'task_lib.Task') -> None:
        assert task.use_spot, ('use_spot is required to use spot strategy.')
        self.dag = sky.Dag()
        self.dag.add(task)
        self.cluster_name = cluster_name
        self.backend = backend

    def __init_subclass__(cls, name):
        SPOT_STRATEGIES[name] = cls

    @classmethod
    def from_task(cls, cluster_name: str, backend: 'Backend',
                  task: 'task_lib.Task') -> 'Strategy':
        """Create a strategy from a task."""
        resources: 'sky.Resources' = task.resources
        assert len(resources) == 1, 'Only one resource is supported.'
        resources = list(resources)[0]
        assert resources.spot_recovery is not None, (
            'spot_recovery_strategy is required to use spot strategy.')

        # Remove the spot_recovery field from the resources, as the strategy
        # will be handled by the strategy class.
        task.set_resources({resources.copy(spot_recovery=None)})
        return SPOT_STRATEGIES[task.spot_recovery_strategy](cluster_name,
                                                            backend, task)

    def launch(self, max_retry=3, retry_gap_seconds=60):
        """Launch the spot cluster at the first time.

        It can fail if resource is not available. Need to check the cluster
        status, after calling.
        """
        # TODO(zhwu): handle the failure during `preparing sky runtime`.
        retry_cnt = 0
        while retry_cnt < max_retry:
            try:
                sky.launch(self.dag,
                           cluster_name=self.cluster_name,
                           detach_run=True)
                logger.info('Spot cluster launched.')
            except SystemExit:
                # If the launch failes, it will be recovered by the following
                # code.
                logger.info('Failed to launch the spot cluster.')
            # TODO(zhwu): set the upscaling_speed in ray yaml to be 0 so that
            # ray will not try to launch another worker if one worker preempted.

            cluster_status = backend_utils.get_cluster_status_with_refresh(
                self.cluster_name, force_refresh=True)
            if cluster_status == global_user_state.ClusterStatus.UP:
                return
            retry_cnt += 1
            # TODO(zhwu): maybe exponential backoff is better?
            time.sleep(retry_gap_seconds)

    def recover(self):
        """Relaunch the spot cluster after failure and wait until job starts.

        When recover() is called the cluster should be in STOPPED status (i.e.
        partially down).
        """
        self.launch()

    def terminate(self):
        """Terminate the spot cluster."""
        handle = global_user_state.get_handle_from_cluster_name(
            self.cluster_name)
        if handle is not None:
            self.backend.teardown(handle, terminate=True)


class FailoverStrategy(Strategy, name='FAILOVER'):
    """Failover strategy: wait in same region and failover after timout."""

    _MAX_RETRY_CNT = 3
    _RETRY_GAP_SECONDS = 10

    def recover(self):
        # 1. Cancel the jobs and launch the cluster with the STOPPED status,
        #    so that it will try on the current region first until timeout.
        # 2. Tear down the cluster, if the step 1 failed to launch the cluster.
        # 3. Launch the cluster with no cloud/region constraint or respect the
        #    original user specification.

        # Step 1
        # cluster_status = backend_utils.get_cluster_status_with_refresh(
        #     self.cluster_name, force_refresh=True)
        # assert cluster_status == global_user_state.ClusterStatus.STOPPED

        # Cluster should be in STOPPED status.
        handle = global_user_state.get_handle_from_cluster_name(
            self.cluster_name)
        try:
            self.backend.cancel_jobs(handle, None)
        except SystemExit:
            # Ignore the failure as the cluster can be totally stopped, and the
            # job canceling can get connection error.
            pass
        self.launch()

        cluster_status = backend_utils.get_cluster_status_with_refresh(
            self.cluster_name, force_refresh=True)
        if cluster_status == global_user_state.ClusterStatus.UP:
            return

        # Step 2
        self.terminate()

        # Step 3
        self.launch(max_retry=self._MAX_RETRY_CNT,
                    retry_gap_seconds=self._RETRY_GAP_SECONDS)
