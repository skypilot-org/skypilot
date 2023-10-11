"""LoadBalancingPolicy: Policy to select endpoint."""
import collections
import logging
import time
from typing import Deque, List, Optional, Set

import fastapi

logger = logging.getLogger(__name__)

_DEFAULT_QUERY_INTERVAL = 60


class LoadBalancingPolicy:
    """Abstract class for load balancing policies."""

    def __init__(self) -> None:
        self.ready_replicas: Set[str] = set()
        self.request_count: int = 0
        self.request_timestamps: Deque[float] = collections.deque()
        self.query_interval: Optional[float] = None

    def increment_request_count(self, count: int = 1) -> None:
        self.request_count += count
        self.request_timestamps.append(time.time())

    def set_query_interval(self, query_interval: Optional[float]) -> None:
        if query_interval is not None:
            self.query_interval = query_interval
        else:
            self.query_interval = _DEFAULT_QUERY_INTERVAL

    def deprecate_old_requests(self) -> int:
        if self.query_interval is None:
            logger.error('Query interval is not set. '
                         'Use default interval instead.')
            self.set_query_interval(None)
        assert self.query_interval is not None
        # TODO(tian): Optimize by binary search.
        while (self.request_timestamps and
               time.time() - self.request_timestamps[0] > self.query_interval):
            self.request_timestamps.popleft()
        return len(self.request_timestamps)

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
        replica_ip = self.replicas_queue[self.index]
        self.index = (self.index + 1) % len(self.replicas_queue)
        request_repr = ('<Request '
                        f'method="{request.method}" '
                        f'url="{request.url}" '
                        f'headers={dict(request.headers)} '
                        f'query_params={dict(request.query_params)}'
                        '>')
        logger.info(f'Selected replica {replica_ip} for request {request_repr}')
        return replica_ip
