"""LoadBalancer: probe endpoints and select by load balancing algorithm."""
import time
from collections import deque

# import aiohttp
import logging

from typing import Optional, Deque

logger = logging.getLogger(__name__)


class LoadBalancer:
    """Abstract class for load balancers."""

    def __init__(self) -> None:
        self.request_count: int = 0
        self.request_timestamps: Deque[float] = deque()

    def increment_request_count(self, count: int = 1) -> None:
        self.request_count += count
        self.request_timestamps.append(time.time())

    def select_server(self, request) -> Optional[str]:
        raise NotImplementedError


class RoundRobinLoadBalancer(LoadBalancer):
    """Round-robin load balancer."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.servers_queue: Deque[str] = deque()

    def select_server(self, request) -> Optional[str]:
        if not self.servers_queue:
            return None

        server_ip = self.servers_queue.popleft()
        self.servers_queue.append(server_ip)
        logger.info(f'Selected server {server_ip} for request {request}')
        return server_ip
