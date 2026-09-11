"""LoadBalancingPolicy: Policy to select endpoint."""
import collections
import dataclasses
import random
import threading
import typing
from typing import Callable, Dict, List, Optional

from sky import sky_logging

if typing.TYPE_CHECKING:
    import fastapi

logger = sky_logging.init_logger(__name__)


@dataclasses.dataclass(frozen=True)
class ReadyReplica:
    """A ready replica's stable identity and current routing information.

    ``replica_id`` is scoped to a service and is used as the load-accounting
    key. Each load balancer currently handles a single service.
    """

    # TODO(jgsweets): Include service_name in the identity and accounting key if
    # a load balancer ever handles multiple services.

    replica_id: int
    url: str
    gpu_type: str = 'unknown'


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
        self.ready_replicas: List[ReadyReplica] = []

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

    def set_ready_replicas(self, ready_replicas: List[ReadyReplica]) -> None:
        raise NotImplementedError

    def select_replica(self,
                       request: 'fastapi.Request') -> Optional[ReadyReplica]:
        replica = self._select_replica(request)
        if replica is not None:
            logger.info(
                f'Selected replica {replica.replica_id} at {replica.url} '
                f'for request {_request_repr(request)}')
        else:
            logger.warning('No replica selected for request '
                           f'{_request_repr(request)}')
        return replica

    # TODO(tian): We should have an abstract class for Request to
    # compatible with all frameworks.
    def _select_replica(self,
                        request: 'fastapi.Request') -> Optional[ReadyReplica]:
        raise NotImplementedError

    def begin_request(self, replica: ReadyReplica,
                      request: 'fastapi.Request') -> Callable[[], None]:
        """Begin request accounting and return a release callback."""
        del replica, request
        return lambda: None

    def pre_execute_hook(self, replica: ReadyReplica,
                         request: 'fastapi.Request') -> None:
        del replica, request
        pass

    def post_execute_hook(self, replica: ReadyReplica,
                          request: 'fastapi.Request') -> None:
        del replica, request
        pass


class RoundRobinPolicy(LoadBalancingPolicy, name='round_robin'):
    """Round-robin load balancing policy."""

    def __init__(self) -> None:
        super().__init__()
        self.index = 0

    def set_ready_replicas(self, ready_replicas: List[ReadyReplica]) -> None:
        if set(self.ready_replicas) == set(ready_replicas):
            return
        ready_replicas = list(ready_replicas)
        # If the autoscaler keeps scaling up and down the replicas,
        # we need this shuffle to not let the first replica have the
        # most of the load.
        random.shuffle(ready_replicas)
        self.ready_replicas = ready_replicas
        self.index = 0

    def _select_replica(self,
                        request: 'fastapi.Request') -> Optional[ReadyReplica]:
        del request  # Unused.
        if not self.ready_replicas:
            return None
        ready_replica = self.ready_replicas[self.index]
        self.index = (self.index + 1) % len(self.ready_replicas)
        return ready_replica


class LeastLoadPolicy(LoadBalancingPolicy, name='least_load', default=True):
    """Least load load balancing policy."""

    def __init__(self) -> None:
        super().__init__()
        self.load_map: Dict[int, int] = collections.defaultdict(int)
        self.lock = threading.Lock()
        self._tie_breaker_index = 0

    def set_ready_replicas(self, ready_replicas: List[ReadyReplica]) -> None:
        ready_replica_set = set(ready_replicas)
        if set(self.ready_replicas) == ready_replica_set:
            return
        ready_replicas = list(ready_replicas)
        ready_replica_ids = {replica.replica_id for replica in ready_replicas}
        with self.lock:
            self.ready_replicas = ready_replicas
            # Retain retired replica IDs with in-flight requests so their
            # completions can decrement the same identity. Remove idle
            # retired IDs immediately.
            for replica_id in list(self.load_map.keys()):
                if (replica_id not in ready_replica_ids and
                        self.load_map[replica_id] == 0):
                    del self.load_map[replica_id]
            for replica in ready_replicas:
                self.load_map[replica.replica_id] = self.load_map.get(
                    replica.replica_id, 0)

    def _select_replica(self,
                        request: 'fastapi.Request') -> Optional[ReadyReplica]:
        del request  # Unused.
        if not self.ready_replicas:
            return None
        with self.lock:
            min_load = min(
                self.load_map.get(replica.replica_id, 0)
                for replica in self.ready_replicas)
            tied_replicas = [
                replica for replica in self.ready_replicas
                if self.load_map.get(replica.replica_id, 0) == min_load
            ]
            return self._select_tied_replica(tied_replicas)

    def begin_request(self, replica: ReadyReplica,
                      request: 'fastapi.Request') -> Callable[[], None]:
        load_released = False

        def release_load() -> None:
            nonlocal load_released
            if load_released:
                return
            load_released = True
            self.post_execute_hook(replica, request)

        # Keep this as the last operation before returning the release
        # callback. It increments load_map; a potentially-raising operation
        # after it would leak the increment before the callback is handed to
        # the caller.
        self.pre_execute_hook(replica, request)
        return release_load

    def _select_tied_replica(self,
                             replicas: List[ReadyReplica]) -> ReadyReplica:
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

    def pre_execute_hook(self, replica: ReadyReplica,
                         request: 'fastapi.Request') -> None:
        del request  # Unused.
        with self.lock:
            self.load_map[replica.replica_id] += 1

    def post_execute_hook(self, replica: ReadyReplica,
                          request: 'fastapi.Request') -> None:
        del request  # Unused.
        with self.lock:
            current_load = self.load_map.get(replica.replica_id)
            if current_load is None:
                # The replica may have retired before this request completed.
                return
            current_load = max(0, current_load - 1)
            ready_replica_ids = {
                ready_replica.replica_id
                for ready_replica in self.ready_replicas
            }
            if (current_load == 0 and
                    replica.replica_id not in ready_replica_ids):
                del self.load_map[replica.replica_id]
            else:
                self.load_map[replica.replica_id] = current_load


class InstanceAwareLeastLoadPolicy(LeastLoadPolicy,
                                   name='instance_aware_least_load'):
    """Instance-aware least load load balancing policy.

    This policy considers the accelerator type and its QPS capabilities
    when distributing load. It normalizes the load by dividing the current
    load by the target QPS for that accelerator type.
    """

    def __init__(self) -> None:
        super().__init__()
        self.target_qps_per_accelerator: Dict[str, float] = {
        }  # accelerator_type -> target_qps

    def set_target_qps_per_accelerator(
            self, target_qps_per_accelerator: Dict[str, float]) -> None:
        """Set target QPS for each accelerator type."""
        with self.lock:
            self.target_qps_per_accelerator = target_qps_per_accelerator

    def _get_normalized_load(self, replica: ReadyReplica) -> float:
        """Get normalized load for a replica based on its accelerator type."""
        current_load = self.load_map.get(replica.replica_id, 0)

        accelerator_type = replica.gpu_type

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
            replica.replica_id, accelerator_type, current_load, target_qps,
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

    def _select_replica(self,
                        request: 'fastapi.Request') -> Optional[ReadyReplica]:
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

    # set_ready_replicas, begin_request, pre_execute_hook, and
    # post_execute_hook are inherited from LeastLoadPolicy.
