"""LoadBalancer: probe endpoints and select by load balancing algorithm."""
import time
from collections import deque

# import aiohttp
import logging

from typing import Optional, Deque, List

logger = logging.getLogger(__name__)

_DEFAULT_QUERY_INTERVAL = 60


class LoadBalancer:
    """Abstract class for load balancers."""

    def __init__(self) -> None:
        self.available_servers: List[str] = []
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

    def set_available_servers(self, server_ips: List[str]) -> None:
        raise NotImplementedError

    def select_server(self, request) -> Optional[str]:
        raise NotImplementedError


class RoundRobinLoadBalancer(LoadBalancer):
    """Round-robin load balancer."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.servers_queue: Deque[str] = deque()

    def set_available_servers(self, server_ips: List[str]) -> None:
        if set(server_ips) != set(self.available_servers):
            self.servers_queue = deque(server_ips)

    def select_server(self, request) -> Optional[str]:
        if not self.servers_queue:
            return None
        server_ip = self.servers_queue.popleft()
        self.servers_queue.append(server_ip)
        logger.info(f'Selected server {server_ip} for request {request}')
        return server_ip
