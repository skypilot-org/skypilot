"""The strategy to handle each launching, recovery and termination of the spot clusters."""
import tempfile
import time
import typing
from typing import Any, Dict

import sky
from sky import global_user_state
from sky.backends import backend_utils

if typing.TYPE_CHECKING:
    from sky.backends import Backend

SPOT_STRATEGIES = dict()


class Strategy:
    """Handle each launching, recovery and termination of the spot clusters."""

    def __init__(self, cluster_name: str, backend: 'Backend',
                 task_config: Dict[str, Any]):
        self.task_config = task_config
        self.cluster_name = cluster_name
        assert 'resources' in self.task_config, (
            'resources is required in task_config')
        assert self.task_config['resources'].get('use_spot'), (
            'use_spot is required in task_config')

        with tempfile.NamedTemporaryFile('w',
                                         prefix=f'sky_spot_{self.cluster_name}',
                                         delete=False) as fp:
            backend_utils.dump_yaml(fp.name, self.task_config)
        self.task_yaml = fp.name
        self.backend = backend

    def __init_subclass__(cls, name):
        SPOT_STRATEGIES[name] = cls

    @classmethod
    def from_task_config(cls, cluster_name: str, backend: 'Backend',
                         task_config: Dict[str, Any]) -> 'Strategy':
        assert 'resources' in task_config, (
            'resources is required in task_config')
        assert 'spot_recovery' in task_config['resources'], (
            'spot_recovery is required in task_config')
        recovery_strategy = task_config['resources']['spot_recovery'].upper()
        assert recovery_strategy in SPOT_STRATEGIES, (
            f'Spot recovery strategy {recovery_strategy} '
            'is not supported. The strategy should be among '
            f'{SPOT_STRATEGIES}')
        return SPOT_STRATEGIES.get(recovery_strategy)(cluster_name, backend,
                                                      task_config)

    def launch(self):
        """Launch the spot cluster at the first time.
        It can fail if resource is not available. Need to check the cluster status, after calling."""
        with sky.Dag() as dag:
            sky.Task.from_yaml(self.task_yaml)
        sky.launch(dag, cluster_name=self.cluster_name, detach_run=True)
        # TODO(zhwu): set the upscaling_speed in ray yaml to be 0 so that ray
        # will not try to launch another worker if one worker preempted.

    def recover(self):
        """Relaunch the spot cluster after failure and wait until job starts.

        When recover() is called the cluster should be in STOPPED status (i.e. partially down).
        """
        self.launch()

    def terminate(self):
        """Terminate the spot cluster."""
        handle = global_user_state.get_handle_from_cluster_name(
            self.cluster_name)
        if handle is not None:
            self.backend.teardown(handle, terminate=True)


class FailoverStrategy(Strategy, name='FAILOVER'):
    """Failover strategy: Wait in the smae region and failover after timout during recovery."""

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
        handle = global_user_state.get_handle_from_cluster_name(self.cluster_name)
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
        retry_cnt = 0
        while retry_cnt < self._MAX_RETRY_CNT:
            self.launch()
            cluster_status = backend_utils.get_cluster_status_with_refresh(
                self.cluster_name, force_refresh=True)
            if cluster_status == global_user_state.ClusterStatus.UP:
                return
            retry_cnt += 1
            # TODO(zhwu): maybe exponential backoff is better?
            time.sleep(self._RETRY_GAP_SECONDS)
