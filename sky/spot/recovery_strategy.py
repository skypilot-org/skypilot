"""The strategy to handle launching/recovery/termination of spot clusters."""
import time
import typing

import sky
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils

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
               retry_gap_seconds=60,
               raise_on_failure=True) -> bool:
        """Launch the spot cluster for the first time.

        It can fail if resource is not available. Need to check the cluster
        status, after calling.

        Returns: True if the cluster is successfully launched.
        """
        # TODO(zhwu): handle the failure during `preparing sky runtime`.
        retry_cnt = 0
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
                return True
            # TODO(zhwu): maybe exponential backoff is better?
            if retry_cnt >= max_retry:
                if raise_on_failure:
                    raise RuntimeError(
                        f'Failed to launch the spot cluster after {max_retry} '
                        'retries.')
                else:
                    return False
            logger.info(
                f'Retrying to launch the spot cluster in {retry_gap_seconds} '
                'seconds.')
            time.sleep(retry_gap_seconds)

    def recover(self):
        """Relaunch the spot cluster after failure and wait until job starts.

        When recover() is called the cluster should be in STOPPED status (i.e.
        partially down).
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
    _RETRY_GAP_SECONDS = 60

    def recover(self):
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
            self.backend.cancel_jobs(handle, None)
        except SystemExit:
            # Ignore the failure as the cluster can be totally stopped, and the
            # job canceling can get connection error.
            pass

        is_launched = self.launch(raise_on_failure=False)
        if is_launched:
            return

        # Step 2
        self.terminate_cluster()

        # Step 3
        is_launched = self.launch(max_retry=self._MAX_RETRY_CNT,
                                  retry_gap_seconds=self._RETRY_GAP_SECONDS,
                                  raise_on_failure=False)
        if not is_launched:
            logger.error(f'Failed to recover the spot cluster after retrying '
                         f'{self._MAX_RETRY_CNT} times every '
                         f'{self._RETRY_GAP_SECONDS} seconds.')
            raise RuntimeError('Failed to recover the spot cluster.')
