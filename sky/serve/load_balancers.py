import time
from collections import deque

import aiohttp
import logging

from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

logger = logging.getLogger(__name__)


class LoadBalancer:

    def __init__(self, infra_provider, endpoint_path, readiness_timeout, post_data=None):
        self.available_servers = []
        self.request_count = 0
        self.request_timestamps = deque()
        self.infra_provider = infra_provider
        self.endpoint_path = endpoint_path
        self.readiness_timeout = readiness_timeout
        self.post_data = post_data

    def increment_request_count(self, count=1):
        self.request_count += count
        self.request_timestamps.append(time.time())

    def probe_endpoints(self, endpoint_ips):
        raise NotImplementedError

    def select_server(self, request):
        raise NotImplementedError


class RoundRobinLoadBalancer(LoadBalancer):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.servers_queue = deque()
        self.first_unhealthy_time = {}
        logger.info(f'Endpoint path: {self.endpoint_path}')

    def probe_endpoints(self, endpoint_ips):

        def probe_endpoint(endpoint_ip):
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
            if server not in self.available_servers:
                logger.info(
                    f'Server {server} is newly available. Adding to available servers.'
                )
                self.available_servers.append(server)
                self.servers_queue.append(server)
        # Remove servers that are no longer available
        unhealthy_servers = set()
        for server in self.available_servers:
            if server not in healthy_servers:
                logger.info(
                    f'Server {server} is no longer available. Removing from available servers.'
                )
                self.available_servers.remove(server)
                self.servers_queue.remove(server)
                unhealthy_servers.add(server)
        # Tell the infra provider to remove endpoints that are no longer available
        for server in endpoint_ips:
            if server not in healthy_servers:
                unhealthy_servers.add(server)
        logger.info(f'Unhealthy servers: {unhealthy_servers}')
        if unhealthy_servers:
            servers_to_terminate = []
            for server in unhealthy_servers:
                if server not in self.first_unhealthy_time:
                    self.first_unhealthy_time[server] = time.time()
                elif time.time() - self.first_unhealthy_time[
                        server] > self.readiness_timeout:  # cooldown before terminating a dead server to avoid hysterisis
                    servers_to_terminate.append(server)
            self.infra_provider.terminate_servers(servers_to_terminate)

    def select_server(self, request):
        if not self.servers_queue:
            return None

        server_ip = self.servers_queue.popleft()
        self.servers_queue.append(server_ip)
        logger.info(f'Selected server {server_ip} for request {request}')
        return server_ip


class LeastLoadedLoadBalancer(LoadBalancer):

    def __init__(self, *args, n=10, **kwargs):

        super().__init__(*args, **kwargs)
        self.server_loads = {}
        self.n = n

    def probe_endpoints(self, endpoint_ips):
        timeout = aiohttp.ClientTimeout(total=2)
        with aiohttp.ClientSession(timeout=timeout) as session:
            for server_ip in endpoint_ips:
                try:
                    start_time = time()
                    with session.get(f'{server_ip}') as response:
                        if response.status == 200:
                            load = time() - start_time

                            if server_ip not in self.server_loads:
                                self.server_loads[server_ip] = [load] * self.n
                            else:
                                self.server_loads[server_ip].append(load)
                                if len(self.server_loads[server_ip]) > self.n:
                                    self.server_loads[server_ip].pop(0)

                            if server_ip not in self.available_servers:
                                self.available_servers.append(server_ip)
                        elif server_ip in self.available_servers:
                            self.available_servers.remove(server_ip)
                            del self.server_loads[server_ip]
                except:
                    if server_ip in self.available_servers:
                        self.available_servers.remove(server_ip)
                        del self.server_loads[server_ip]

    def select_server(self, request):
        if not self.server_loads:
            return None

        server_ip = min(
            self.server_loads,
            key=lambda x: sum(self.server_loads[x]) / len(self.server_loads[x]))
        return server_ip
