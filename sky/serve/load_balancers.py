"""LoadBalancer: probe endpoints and select by load balancing algorithm."""
import time
from collections import deque

# import aiohttp
import logging
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Any, List, Deque, Dict

from sky.serve.infra_providers import InfraProvider

logger = logging.getLogger(__name__)


class LoadBalancer:
    """Abstract class for load balancers."""

    def __init__(self,
                 infra_provider: InfraProvider,
                 endpoint_path: str,
                 readiness_timeout: int,
                 post_data: Optional[Any] = None):
        self.available_servers: List[str] = []
        self.request_count: int = 0
        self.request_timestamps: Deque[float] = deque()
        self.infra_provider: InfraProvider = infra_provider
        self.endpoint_path: str = endpoint_path
        self.readiness_timeout: int = readiness_timeout
        self.post_data: Any = post_data

    def increment_request_count(self, count: int = 1) -> None:
        self.request_count += count
        self.request_timestamps.append(time.time())

    def probe_endpoints(self, endpoint_ips: List[str]) -> None:
        raise NotImplementedError

    def select_server(self, request) -> Optional[str]:
        raise NotImplementedError


class RoundRobinLoadBalancer(LoadBalancer):
    """Round-robin load balancer."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.servers_queue: Deque[str] = deque()
        self.first_unhealthy_time: Dict[str, float] = {}
        logger.info(f'Endpoint path: {self.endpoint_path}')

    def probe_endpoints(self, endpoint_ips: List[str]) -> None:

        def probe_endpoint(endpoint_ip: str) -> Optional[str]:
            try:
                if self.post_data:
                    response = requests.post(
                        f'http://{endpoint_ip}{self.endpoint_path}',
                        json=self.post_data,
                        timeout=3)
                else:
                    response = requests.get(
                        f'http://{endpoint_ip}{self.endpoint_path}', timeout=3)
                if response.status_code == 200:
                    logger.info(f'Server {endpoint_ip} is available.')
                    return endpoint_ip
            except requests.exceptions.RequestException as e:
                logger.info(e)
                logger.info(f'Server {endpoint_ip} is not available.')
                pass
            return None

        with ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(probe_endpoint, endpoint_url)
                for endpoint_url in endpoint_ips
            ]
            healthy_servers = [
                future.result()
                for future in as_completed(futures)
                if future.result() is not None
            ]
        logger.info(f'Healthy servers: {healthy_servers}')
        # Add newly available servers
        for server in healthy_servers:
            assert server is not None
            if server not in self.available_servers:
                logger.info(f'Server {server} is newly available. '
                            'Adding to available servers.')
                self.available_servers.append(server)
                self.servers_queue.append(server)
        # Remove servers that are no longer available
        unhealthy_servers = set()
        for server in self.available_servers:
            if server not in healthy_servers:
                logger.info(f'Server {server} is no longer available. '
                            'Removing from available servers.')
                self.available_servers.remove(server)
                self.servers_queue.remove(server)
                unhealthy_servers.add(server)
        # Tell the infra provider to remove endpoints that are
        # no longer available
        for server in endpoint_ips:
            if server not in healthy_servers:
                unhealthy_servers.add(server)
        logger.info(f'Unhealthy servers: {unhealthy_servers}')
        if unhealthy_servers:
            servers_to_terminate = []
            for server in unhealthy_servers:
                if server not in self.first_unhealthy_time:
                    self.first_unhealthy_time[server] = time.time()
                # coldstart time limitation is `self.readiness_timeout`.
                elif time.time(
                ) - self.first_unhealthy_time[server] > self.readiness_timeout:
                    servers_to_terminate.append(server)
            self.infra_provider.terminate_servers(servers_to_terminate)

    def select_server(self, request) -> Optional[str]:
        if not self.servers_queue:
            return None

        server_ip = self.servers_queue.popleft()
        self.servers_queue.append(server_ip)
        logger.info(f'Selected server {server_ip} for request {request}')
        return server_ip
