"""LoadBalancingPolicy: Policy to select endpoint."""
from typing import List, Optional, Set

import fastapi

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


class LoadBalancingPolicy:
    """Abstract class for load balancing policies."""

    def __init__(self) -> None:
        self.ready_replicas: Set[str] = set()

    def set_ready_replicas(self, ready_replicas: Set[str]) -> None:
        raise NotImplementedError

    def select_replica(self, request: fastapi.Request) -> Optional[str]:
        raise NotImplementedError


class RoundRobinPolicy(LoadBalancingPolicy):
    """Round-robin load balancing policy."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.replicas_queue: List[str] = []
        self.index = 0

    def set_ready_replicas(self, ready_replicas: Set[str]) -> None:
        if set(ready_replicas) != set(self.ready_replicas):
            self.ready_replicas = ready_replicas
            self.replicas_queue = list(ready_replicas)
            self.index = 0

    def select_replica(self, request: fastapi.Request) -> Optional[str]:
        if not self.replicas_queue:
            return None
        replica = self.replicas_queue[self.index]
        self.index = (self.index + 1) % len(self.replicas_queue)
        request_repr = ('<Request '
                        f'method="{request.method}" '
                        f'url="{request.url}" '
                        f'headers={dict(request.headers)} '
                        f'query_params={dict(request.query_params)}'
                        '>')
        logger.info(f'Selected replica {replica} for request {request_repr}')
        return replica
