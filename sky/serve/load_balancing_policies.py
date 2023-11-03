"""LoadBalancingPolicy: Policy to select endpoint."""
import random
from typing import Any, Dict, List, Optional, Set, Type

import fastapi

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

POLICIES: Dict[str, Type['LoadBalancingPolicy']] = dict()


def _repr_requests(request: fastapi.Request) -> str:
    return ('<Request '
            f'method="{request.method}" '
            f'url="{request.url}" '
            f'headers={dict(request.headers)} '
            f'query_params={dict(request.query_params)}'
            '>')


class LoadBalancingPolicy:
    """Abstract class for load balancing policies."""
    NAME: str = 'abstract'

    def __init__(self) -> None:
        # TODO(tian): Refactor this.
        self.ready_replicas: Set[str] = set()

    def __init_subclass__(cls) -> None:
        assert (cls.NAME not in POLICIES and
                cls.NAME != 'abstract'), f'Name {cls.NAME} already exists'
        POLICIES[cls.NAME] = cls

    def set_ready_replicas(self, ready_replicas: Dict[str, Any]) -> None:
        raise NotImplementedError

    def select_replica(self, request: fastapi.Request) -> Optional[str]:
        raise NotImplementedError


class RoundRobinPolicy(LoadBalancingPolicy):
    """Round-robin load balancing policy."""
    NAME: str = 'round_robin'

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.replicas_queue: List[str] = []
        self.index = 0

    def set_ready_replicas(self, ready_replicas: Dict[str, Any]) -> None:
        if set(ready_replicas.keys()) != set(self.ready_replicas):
            self.ready_replicas = set(ready_replicas.keys())
            self.replicas_queue = list(ready_replicas.keys())
            self.index = 0

    def select_replica(self, request: fastapi.Request) -> Optional[str]:
        if not self.replicas_queue:
            return None
        replica = self.replicas_queue[self.index]
        self.index = (self.index + 1) % len(self.replicas_queue)
        request_repr = _repr_requests(request)
        logger.info(f'Selected replica {replica} for request {request_repr}')
        return replica


DEFAULT_POLICY_NAME = RoundRobinPolicy.NAME


class WeightedProbabilityProbePolicy(LoadBalancingPolicy):
    """Weighted probability probing load balancing policy."""
    NAME: str = 'weighted_probability_probe'

    def __init__(self) -> None:
        super().__init__()
        self.replicas: List[str] = []
        self.probs: List[float] = []

    def set_ready_replicas(self, ready_replicas: Dict[str, Any]) -> None:
        replicas, probs = [], []
        for replica, latency in ready_replicas.items():
            replicas.append(replica)
            probs.append(1 / latency)
        self.replicas = replicas
        self.probs = [prob / sum(probs) for prob in probs]
        # info all replica and their weights
        replica_identities = ' '.join(
            [str((r, p)) for r, p in zip(self.replicas, self.probs)])
        logger.info(f'Replica to weight: {replica_identities}')

    def select_replica(self, request: fastapi.Request) -> Optional[str]:
        if not self.replicas:
            return None

        replica = random.choices(self.replicas, weights=self.probs)[0]

        request_repr = _repr_requests(request)
        logger.info(f'Selected replica {replica} for request {request_repr}')
        return replica
