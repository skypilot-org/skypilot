"""The strategy to handle each launching, recovery and termination of the spot clusters."""
import tempfile
from typing import Any, Dict

from sky.backends import backend_utils


class Strategy:
    SPOT_STRATEGIES = dict()

    def __init__(self, cluster_name: str, task_config: Dict[str, Any]):
        self.task_config = task_config
        self.cluster_name = cluster_name
        assert 'resources' in self.task_config, (
            'resources is required in task_config')
        assert self.task_config['resources'].get('use_spot'), (
            'use_spot is required in task_config')

    def __init_subclass__(cls, name):
        cls.SPOT_STRATEGIES[name] = cls

    @classmethod
    def from_strategy(cls, recovery_strategy, task_config) -> 'Strategy':
        if recovery_strategy not in cls.SPOT_STRATEGIES:
            raise ValueError(f'Spot recovery strategy {recovery_strategy} '
                             'is not supported. The strategy should be among '
                             f'{cls.SPOT_STRATEGIES}')
        return cls.SPOT_STRATEGIES.get(recovery_strategy)(task_config)

    def initial_launch(self):
        """Launch the spot cluster at the first time."""
        raise NotImplementedError

    def recover(self):
        """Relaunch the spot cluster after failure and wait until job starts."""
        raise NotImplementedError

    def terminate(self):
        """Terminate the spot cluster."""
        raise NotImplementedError


class FailoverStrategy(Strategy, name='FAILOVER'):
    """Failover strategy: Wait in the smae region and failover after timout during recovery."""
    def initial_launch(self):
        with tempfile.NamedTemporaryFile('w',
                                         prefix=f'sky_spot_{self.cluster_name}',
                                         delete=False) as fp:
            backend_utils.dump_yaml(fp.name, self.task_config)
        return fp.name

    def recover(self):
        # TODO(zhwu): We should do the following:
        # 1. Launch the cluster with the INIT status, so that it will try on the
        #    current region first until timeout.
        # 2. Tear down the cluster, if the step 1 failed to launch the cluster.
        # 3. Launch the cluster with no cloud/region constraint or respect the
        #    original user specification.
        raise NotImplementedError
