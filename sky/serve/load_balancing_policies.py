"""LoadBalancingPolicy: Policy to select endpoint."""
import collections
import random
import threading
import typing
from typing import Any, Dict, List, Optional

from sky import sky_logging

if typing.TYPE_CHECKING:
    import fastapi

logger = sky_logging.init_logger(__name__)

# Define a registry for load balancing policies
LB_POLICIES = {}
DEFAULT_LB_POLICY = None


def _request_repr(request: 'fastapi.Request') -> str:
    return ('<Request '
            f'method="{request.method}" '
            f'url="{request.url}" '
            f'headers={dict(request.headers)} '
            f'query_params={dict(request.query_params)}>')


class LoadBalancingPolicy:
    """Abstract class for load balancing policies."""

    def __init__(self) -> None:
        self.ready_replicas: List[str] = []

    def __init_subclass__(cls, name: str, default: bool = False):
        LB_POLICIES[name] = cls
        if default:
            global DEFAULT_LB_POLICY
            assert DEFAULT_LB_POLICY is None, (
                'Only one policy can be default.')
            DEFAULT_LB_POLICY = name

    @classmethod
    def make_policy_name(cls, policy_name: Optional[str]) -> str:
        """Return the policy name."""
        assert DEFAULT_LB_POLICY is not None, 'No default policy set.'
        if policy_name is None:
            return DEFAULT_LB_POLICY
        return policy_name

    @classmethod
    def make(cls, policy_name: Optional[str] = None) -> 'LoadBalancingPolicy':
        """Create a load balancing policy from a name."""
        policy_name = cls.make_policy_name(policy_name)
        if policy_name not in LB_POLICIES:
            raise ValueError(f'Unknown load balancing policy: {policy_name}')
        return LB_POLICIES[policy_name]()

    def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        raise NotImplementedError

    def select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        replica = self._select_replica(request)
        if replica is not None:
            logger.info(f'Selected replica {replica} '
                        f'for request {_request_repr(request)}')
        else:
            logger.warning('No replica selected for request '
                           f'{_request_repr(request)}')
        return replica

    # TODO(tian): We should have an abstract class for Request to
    # compatible with all frameworks.
    def _select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        raise NotImplementedError

    def pre_execute_hook(self, replica_url: str,
                         request: 'fastapi.Request') -> None:
        pass

    def post_execute_hook(self, replica_url: str,
                          request: 'fastapi.Request') -> None:
        pass


class RoundRobinPolicy(LoadBalancingPolicy, name='round_robin'):
    """Round-robin load balancing policy."""

    def __init__(self) -> None:
        super().__init__()
        self.index = 0

    def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        if set(self.ready_replicas) == set(ready_replicas):
            return
        # If the autoscaler keeps scaling up and down the replicas,
        # we need this shuffle to not let the first replica have the
        # most of the load.
        random.shuffle(ready_replicas)
        self.ready_replicas = ready_replicas
        self.index = 0

    def _select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        del request  # Unused.
        if not self.ready_replicas:
            return None
        ready_replica_url = self.ready_replicas[self.index]
        self.index = (self.index + 1) % len(self.ready_replicas)
        return ready_replica_url


class LeastLoadPolicy(LoadBalancingPolicy, name='least_load', default=True):
    """Least load load balancing policy."""

    def __init__(self) -> None:
        super().__init__()
        self.load_map: Dict[str, int] = collections.defaultdict(int)
        self.lock = threading.Lock()
        self._tie_breaker_index = 0

    def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        ready_replica_set = set(ready_replicas)
        if set(self.ready_replicas) == ready_replica_set:
            return
        with self.lock:
            self.ready_replicas = ready_replicas
            for r in list(self.load_map.keys()):
                # Keep the accounting for retired replicas until their
                # in-flight requests finish. Otherwise a late completion can
                # recreate the entry with a negative load.
                if r not in ready_replica_set and self.load_map[r] <= 0:
                    del self.load_map[r]
            for replica in ready_replicas:
                self.load_map[replica] = self.load_map.get(replica, 0)

    def _select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        del request  # Unused.
        if not self.ready_replicas:
            return None
        with self.lock:
            min_load = min(
                self.load_map.get(replica, 0)
                for replica in self.ready_replicas)
            tied_replicas = [
                replica for replica in self.ready_replicas
                if self.load_map.get(replica, 0) == min_load
            ]
            return self._select_tied_replica(tied_replicas)

    def _select_tied_replica(self, replicas: List[str]) -> str:
        """Select among tied replicas without favoring the first one.

        The cursor is relative to all ready replicas, so changes to the set
        of tied replicas do not cause the same endpoint to win repeatedly.
        """
        assert replicas and self.ready_replicas, (
            'Cannot select from empty replica lists.')
        tied_replicas = set(replicas)
        for offset in range(len(self.ready_replicas)):
            index = (self._tie_breaker_index + offset) % len(
                self.ready_replicas)
            replica = self.ready_replicas[index]
            if replica in tied_replicas:
                self._tie_breaker_index = (index + 1) % len(self.ready_replicas)
                return replica
        raise RuntimeError('No tied replica found among ready replicas.')

    def pre_execute_hook(self, replica_url: str,
                         request: 'fastapi.Request') -> None:
        del request  # Unused.
        with self.lock:
            self.load_map[replica_url] += 1

    def post_execute_hook(self, replica_url: str,
                          request: 'fastapi.Request') -> None:
        del request  # Unused.
        with self.lock:
            current_load = self.load_map.get(replica_url)
            if current_load is None:
                return
            current_load = max(0, current_load - 1)
            if current_load == 0 and replica_url not in self.ready_replicas:
                del self.load_map[replica_url]
            else:
                self.load_map[replica_url] = current_load


class InstanceAwareLeastLoadPolicy(LeastLoadPolicy,
                                   name='instance_aware_least_load'):
    """Instance-aware least load load balancing policy.

    This policy considers the accelerator type and its QPS capabilities
    when distributing load. It normalizes the load by dividing the current
    load by the target QPS for that accelerator type.
    """

    def __init__(self) -> None:
        super().__init__()
        self.replica_info: Dict[str, Dict[str, Any]] = {}  # replica_url -> info
        self.target_qps_per_accelerator: Dict[str, float] = {
        }  # accelerator_type -> target_qps

    def set_replica_info(self, replica_info: Dict[str, Dict[str, Any]]) -> None:
        """Set replica information including accelerator types.

        Args:
            replica_info: Dict mapping replica URL to replica information
                         e.g., {'http://url1': {'gpu_type': 'A100'}}
        """
        with self.lock:
            self.replica_info = replica_info
            logger.debug('Set replica info: %s', self.replica_info)

    def set_target_qps_per_accelerator(
            self, target_qps_per_accelerator: Dict[str, float]) -> None:
        """Set target QPS for each accelerator type."""
        with self.lock:
            self.target_qps_per_accelerator = target_qps_per_accelerator

    def _get_normalized_load(self, replica_url: str) -> float:
        """Get normalized load for a replica based on its accelerator type."""
        current_load = self.load_map.get(replica_url, 0)

        # Get accelerator type for this replica
        replica_data = self.replica_info.get(replica_url, {})
        accelerator_type = replica_data.get('gpu_type', 'unknown')

        # Get target QPS for this accelerator type with flexible matching
        target_qps = self._get_target_qps_for_accelerator(accelerator_type)
        if target_qps <= 0:
            logger.warning(
                'Non-positive target QPS (%s) for accelerator type %s; '
                'using default value 1.0 to avoid division by zero.',
                target_qps, accelerator_type)
            target_qps = 1.0

        # Load is normalized by target QPS
        normalized_load = current_load / target_qps

        logger.debug(
            'InstanceAwareLeastLoadPolicy: Replica %s - GPU type: %s, '
            'current load: %s, target QPS: %s, normalized load: %s',
            replica_url, accelerator_type, current_load, target_qps,
            normalized_load)

        return normalized_load

    def _get_target_qps_for_accelerator(self, accelerator_type: str) -> float:
        """Get target QPS for accelerator type with flexible matching."""
        # Direct match first
        if accelerator_type in self.target_qps_per_accelerator:
            return self.target_qps_per_accelerator[accelerator_type]

        # Try matching by base name (e.g., 'A100' matches 'A100:1')
        for config_key in self.target_qps_per_accelerator.keys():
            # Remove count suffix (e.g., 'A100:1' -> 'A100')
            base_name = config_key.split(':')[0]
            if accelerator_type == base_name:
                return self.target_qps_per_accelerator[config_key]

        # Fallback to minimum QPS
        logger.warning(
            f'No matching QPS found for accelerator type: {accelerator_type}. '
            f'Available types: {list(self.target_qps_per_accelerator.keys())}. '
            f'Using default value 1.0 as fallback.')
        return 1.0

    def _select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        del request  # Unused.
        if not self.ready_replicas:
            return None
        with self.lock:
            # Calculate normalized loads for all replicas
            replica_loads = []
            for replica in self.ready_replicas:
                normalized_load = self._get_normalized_load(replica)
                replica_loads.append((replica, normalized_load))

            # Select a minimum-load replica, rotating among ties.
            min_load = min(load for _, load in replica_loads)
            tied_replicas = [
                replica for replica, load in replica_loads if load == min_load
            ]
            selected_replica = self._select_tied_replica(tied_replicas)
            logger.debug('Available replicas and loads: %s', replica_loads)
            logger.debug('Selected replica: %s', selected_replica)
            return selected_replica

    # set_ready_replicas, pre_execute_hook, and post_execute_hook are inherited
    # from LeastLoadPolicy.
