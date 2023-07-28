"""LoadBalancer: select endpoint by load balancing algorithm."""
from collections import deque
import fastapi
import time
import logging
from typing import Optional, Deque, Set

logger = logging.getLogger(__name__)

_DEFAULT_QUERY_INTERVAL = 60


class LoadBalancer:
    """Abstract class for load balancers."""

    def __init__(self) -> None:
        self.healthy_replicas: Set[str] = set()
        self.request_count: int = 0
        self.request_timestamps: Deque[float] = deque()
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
        while (self.request_timestamps and
               time.time() - self.request_timestamps[0] > self.query_interval):
            self.request_timestamps.popleft()
        return len(self.request_timestamps)

    def set_healthy_replicas(self, healthy_replicas: Set[str]) -> None:
        raise NotImplementedError

    def select_replica(self, request: fastapi.Request) -> Optional[str]:
        raise NotImplementedError


class RoundRobinLoadBalancer(LoadBalancer):
    """Round-robin load balancer."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.replicas_queue: Deque[str] = deque()

    def set_healthy_replicas(self, healthy_replicas: Set[str]) -> None:
        if set(healthy_replicas) != set(self.healthy_replicas):
            self.healthy_replicas = healthy_replicas
            self.replicas_queue = deque(healthy_replicas)

    def select_replica(self, request: fastapi.Request) -> Optional[str]:
        if not self.replicas_queue:
            return None
        replica_ip = self.replicas_queue.popleft()
        self.replicas_queue.append(replica_ip)
        logger.info(f'Selected replica {replica_ip} for request {request}')
        return replica_ip
