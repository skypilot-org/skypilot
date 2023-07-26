"""InfraProvider: handles the creation and deletion of servers."""
import logging
import os
from typing import List, Dict
import time
import pickle
import base64
import multiprocessing
import signal

import sky
from sky.backends import backend_utils

logger = logging.getLogger(__name__)

_PROCESS_POOL_REFRESH_INTERVAL = 5


class InfraProvider:
    """Each infra provider manages one services."""

    def get_server_ips(self) -> List[str]:
        raise NotImplementedError

    def total_servers(self) -> int:
        # Returns the total number of servers, including those under
        # provisioning and deletion
        raise NotImplementedError

    def scale_up(self, n: int) -> None:
        raise NotImplementedError

    def scale_down(self, n: int) -> None:
        # TODO - Scale down must also pass in a list of servers to
        # delete or the number of servers to delete
        raise NotImplementedError

    def terminate_servers(self, unhealthy_servers: List[str]) -> None:
        # Terminates the servers with endpoints in the list
        raise NotImplementedError

    def get_failed_servers(self):
        # Returns a list of failed servers
        raise NotImplementedError

    def terminate(self):
        # Terminate service
        raise NotImplementedError


# class DummyInfraProvider(InfraProvider):
#     """A dummy infra provider that not actually provision any servers."""

#     def __init__(self):
#         self.default_entrypoints = [
#             'https://httpbin.org/get?id=basecase', 'https://www.google.com',
#             'http://thiswebsitedoesntexistitsonlyfortesting.com'
#         ]
#         self.current_endpoints = self.default_entrypoints.copy()

#     def get_server_ips(self) -> List[str]:
#         logger.info(f'Returning current endpoints: {self.current_endpoints}')
#         return self.current_endpoints

#     def total_servers(self) -> int:
#         return len(self.current_endpoints)

#     def scale_up(self, n: int) -> None:
#         logger.info(f'DummyInfraProvider.scale_up called with n={n}. '
#                     'Sleeping for 30s.')
#         for i in range(30):
#             logger.info(f'DummyInfraProvider.scale_up: {i}/30')
#             time.sleep(1)
#         # Add n new endpoints
#         for i in range(n):
#             self.current_endpoints.append('https://httpbin.org/get?id=' +
#                                           str(len(self.current_endpoints)))
#         logger.info('DummyInfraProvider.scale_up: done sleeping.')

#     def scale_down(self, n: int) -> None:
#         logger.info(f'DummyInfraProvider.scale_down called with n={n}. '
#                     'Doing nothing.')

#     def terminate_servers(self, unhealthy_servers: List[str]):
#         # Remove unhealthy servers from current_endpoints
#         logger.info('DummyInfraProvider.terminate_servers called with '
#                     f'unhealthy_servers={unhealthy_servers}')
#         self.current_endpoints = [
#             endpoint for endpoint in self.current_endpoints
#             if endpoint not in unhealthy_servers
#         ]


class SkyPilotInfraProvider(InfraProvider):
    """Infra provider for SkyPilot clusters."""

    def __init__(self, task_yaml_path: str, cluster_name_prefix: str):
        self.task_yaml_path: str = task_yaml_path
        self.cluster_name_prefix: str = cluster_name_prefix + '-'
        self.id_counter: int = self._get_id_start()
        self.launch_process_pool: Dict[str, multiprocessing.Process] = dict()
        self.down_process_pool: Dict[str, multiprocessing.Process] = dict()
        self.failed_servers: List[str] = []

        self.process_pool_refresh_process = multiprocessing.Process(
            target=self._refresh_process_pool)
        self.process_pool_refresh_process.start()

    def _get_id_start(self) -> int:
        """
        Returns the id to start from when creating a new cluster.
        """
        clusters = sky.global_user_state.get_clusters()
        # Filter out clusters that don't have the prefix
        clusters = [
            cluster for cluster in clusters
            if self.cluster_name_prefix in cluster['name']
        ]
        # Get the greatest id
        max_id = 0
        for cluster in clusters:
            name = cluster['name']
            server_id = int(name.split('-')[-1])
            if server_id > max_id:
                max_id = server_id
        return max_id + 1

    def _get_ip_clusname_map(self) -> Dict[str, str]:
        """
        Returns a map of ip to cluster name for all clusters with the prefix.
        """
        clusters = sky.global_user_state.get_clusters()
        ip_clusname_map = {}
        for cluster in clusters:
            name = cluster['name']
            if self.cluster_name_prefix in name:
                handle = cluster['handle']
                try:
                    # Get the head node ip
                    ip = backend_utils.get_node_ips(handle.cluster_yaml,
                                                    handle.launched_nodes,
                                                    handle)[0]
                    ip_clusname_map[ip] = name
                except sky.exceptions.FetchIPError:
                    logger.warning(f'Unable to get IP for cluster {name}.')
                    continue
        return ip_clusname_map

    def get_replica_info(self) -> List[Dict[str, str]]:
        clusters = sky.global_user_state.get_clusters()
        infos = []
        for cluster in clusters:
            if self.cluster_name_prefix in cluster['name']:
                info = {
                    'name': cluster['name'],
                    'handle': cluster['handle'],
                    'status': cluster['status'],
                }
                info = {
                    k: base64.b64encode(pickle.dumps(v)).decode('utf-8')
                    for k, v in info.items()
                }
                infos.append(info)
        return infos

    def _get_server_ips(self) -> List[str]:
        return list(self._get_ip_clusname_map().keys())

    def _return_total_servers(self) -> int:
        clusters = sky.global_user_state.get_clusters()
        # Filter out clusters that don't have the prefix
        # FIXME - this is a hack to get around.
        # should implement a better filtering mechanism
        clusters = [
            cluster for cluster in clusters
            if self.cluster_name_prefix in cluster['name']
        ]
        return len(clusters)

    def get_failed_servers(self) -> List[str]:
        return self.failed_servers

    def _launch_cluster(self, cluster_name: str, task: sky.Task) -> None:
        p = multiprocessing.Process(target=sky.launch,
                                    args=(task,),
                                    kwargs={
                                        'cluster_name': cluster_name,
                                        'detach_run': True,
                                        'retry_until_up': True
                                    })
        self.launch_process_pool[cluster_name] = p
        p.start()

    def _teardown_cluster(self, cluster_name: str) -> None:
        p = multiprocessing.Process(target=sky.down,
                                    args=(cluster_name,),
                                    kwargs={'purge': True})
        self.down_process_pool[cluster_name] = p
        p.start()

    def _scale_up(self, n: int) -> None:
        # Launch n new clusters
        task = sky.Task.from_yaml(self.task_yaml_path)
        for _ in range(0, n):
            cluster_name = f'{self.cluster_name_prefix}{self.id_counter}'
            logger.info(f'Creating SkyPilot cluster {cluster_name}')
            self._launch_cluster(cluster_name, task)
            self.id_counter += 1

    def _scale_down(self, n: int) -> None:
        # Delete n clusters
        # Currently deletes the first n clusters
        clusters = sky.global_user_state.get_clusters()
        # Filter out clusters that don't have the prefix
        clusters = [
            cluster for cluster in clusters
            if self.cluster_name_prefix in cluster['name']
        ]
        num_clusters = len(clusters)
        if num_clusters > 0:
            if n > num_clusters:
                logger.warning(
                    f'Trying to delete {n} clusters, but only {num_clusters} '
                    'clusters exist. Deleting all clusters.')
                n = num_clusters
            for i in range(0, n):
                cluster = clusters[i]
                logger.info(f'Deleting SkyPilot cluster {cluster["name"]}')
                self._teardown_cluster(cluster['name'])

    def _refresh_process_pool(self) -> None:
        while True:
            logger.info('Refreshing process pool.')
            for pool in [self.launch_process_pool, self.down_process_pool]:
                for cluster_name, p in list(pool.items()):
                    if not p.is_alive():
                        logger.info(f'Process for {cluster_name} is dead.')
                        del pool[cluster_name]
                        if p.exitcode != 0:
                            logger.info(f'Process for {cluster_name} exited '
                                        f'abnormally with code {p.exitcode}.')
                            self.failed_servers.append(cluster_name)
            time.sleep(_PROCESS_POOL_REFRESH_INTERVAL)

    def get_server_ips(self) -> List[str]:
        ips = self._get_server_ips()
        logger.info(f'Returning SkyPilot endpoints: {ips}')
        return ips

    def total_servers(self) -> int:
        return self._return_total_servers()

    def scale_up(self, n: int) -> None:
        self._scale_up(n)

    def scale_down(self, n: int) -> None:
        self._scale_down(n)

    def terminate_servers(self, unhealthy_servers: List[str]) -> None:
        # Remove unhealthy servers from current_endpoints
        logger.info('SkyPilotInfraProvider.terminate_servers called with '
                    f'unhealthy_servers={unhealthy_servers}')
        for endpoint_url in unhealthy_servers:
            ip_to_name_map = self._get_ip_clusname_map()
            if endpoint_url not in ip_to_name_map:
                logger.warning(
                    f'Unable to find cluster name for endpoint {endpoint_url}. '
                    'Skipping.')
                continue
            name = ip_to_name_map[endpoint_url]
            if endpoint_url in unhealthy_servers:
                logger.info(f'Deleting SkyPilot cluster {name}')
                self._teardown_cluster(name)

    def terminate(self) -> None:
        self.process_pool_refresh_process.terminate()
        for name, p in self.launch_process_pool.items():
            # Use keyboard interrupt here since sky.launch has great
            # handling for it
            # Edge case: sky.launched finished after the
            # process_pool_refresh_process terminates
            if p.is_alive():
                assert p.pid is not None
                os.kill(p.pid, signal.SIGINT)
                p.join()
                self._teardown_cluster(name)
        server_ips = self._get_server_ips()
        self.terminate_servers(server_ips)
        for _, p in self.down_process_pool.items():
            p.join()
