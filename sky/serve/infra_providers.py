import logging
from typing import List
import time
import pickle
import base64

import sky
from sky.backends import backend_utils

import urllib
import threading

logger = logging.getLogger(__name__)


class InfraProvider:

    def get_server_ips(self) -> List[str]:
        raise NotImplementedError

    def total_servers(self) -> int:
        # Returns the total number of servers, including those under provisioning and deletion
        raise NotImplementedError

    def scale_up(self, n: int) -> None:
        raise NotImplementedError

    def scale_down(self, n: int) -> None:
        # TODO - Scale down must also pass in a list of servers to delete or the number of servers to delete
        raise NotImplementedError

    def terminate_servers(self, unhealthy_servers: List[str]):
        # Terminates the servers with endpoints in the list
        raise NotImplementedError


class DummyInfraProvider(InfraProvider):

    def __init__(self):
        self.DEFAULT_ENDPOINTS = [
            'https://httpbin.org/get?id=basecase', 'https://www.google.com',
            'http://thiswebsitedoesntexistitsonlyfortesting.com'
        ]
        self.current_endpoints = self.DEFAULT_ENDPOINTS.copy()

    def get_server_ips(self) -> List[str]:
        logger.info('Returning current endpoints: ' +
                    str(self.current_endpoints))
        return self.current_endpoints

    def total_servers(self) -> int:
        return len(self.current_endpoints)

    def scale_up(self, n) -> None:
        logger.info('DummyInfraProvider.scale_up called with n=' + str(n) +
                    '. Sleeping for 30s.')
        for i in range(30):
            logger.info('DummyInfraProvider.scale_up: ' + str(i) + '/30')
            time.sleep(1)
        # Add n new endpoints
        for i in range(n):
            self.current_endpoints.append('https://httpbin.org/get?id=' +
                                          str(len(self.current_endpoints)))
        logger.info('DummyInfraProvider.scale_up: done sleeping.')

    def scale_down(self, n) -> None:
        logger.info('DummyInfraProvider.scale_down called with n=' + str(n) +
                    '. Doing nothing.')

    def terminate_servers(self, unhealthy_servers: List[str]):
        # Remove unhealthy servers from current_endpoints
        logger.info(
            'DummyInfraProvider.terminate_servers called with unhealthy_servers='
            + str(unhealthy_servers))
        self.current_endpoints = [
            endpoint for endpoint in self.current_endpoints
            if endpoint not in unhealthy_servers
        ]


class SkyPilotInfraProvider(InfraProvider):

    def __init__(self, task_yaml_path: str, cluster_name_prefix: str):
        self.task_yaml_path = task_yaml_path
        self.cluster_name_prefix = cluster_name_prefix + '-'
        self.id_counter = self._get_id_start()

    def _get_id_start(self):
        """
        Returns the id to start from when creating a new cluster
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
            id = int(name.split('-')[-1])
            if id > max_id:
                max_id = id
        return max_id + 1

    def _get_ip_clusname_map(self):
        """Returns a map of ip to cluster name for all clusters with the prefix"""
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

    def get_replica_info(self):
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

    def _get_server_ips(self):
        return list(self._get_ip_clusname_map().keys())

    def _return_total_servers(self):
        clusters = sky.global_user_state.get_clusters()
        # Filter out clusters that don't have the prefix
        # FIXME - this is a hack to get around. should implement a better filtering mechanism
        clusters = [
            cluster for cluster in clusters
            if self.cluster_name_prefix in cluster['name']
        ]
        return len(clusters)

    def _scale_up(self, n):
        # Launch n new clusters
        task = sky.Task.from_yaml(self.task_yaml_path)
        for i in range(0, n):
            cluster_name = f'{self.cluster_name_prefix}{self.id_counter}'
            logger.info(f'Creating SkyPilot cluster {cluster_name}')
            sky.launch(task,
                       cluster_name=cluster_name,
                       detach_run=True,
                       retry_until_up=True)  # TODO - make the launch parallel
            self.id_counter += 1

    def _scale_down(self, n):
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
                    f'Trying to delete {n} clusters, but only {num_clusters} clusters exist. Deleting all clusters.'
                )
                n = num_clusters
            for i in range(0, n):
                cluster = clusters[i]
                logger.info(f'Deleting SkyPilot cluster {cluster["name"]}')
                sky.down(cluster['name'], purge=True)

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

    def terminate_servers(self, unhealthy_servers: List[str]):
        # Remove unhealthy servers from current_endpoints
        logger.info(
            'SkyPilotInfraProvider.terminate_servers called with unhealthy_servers='
            + str(unhealthy_servers))
        for endpoint_url in unhealthy_servers:
            ip_to_name_map = self._get_ip_clusname_map()
            if endpoint_url not in ip_to_name_map:
                logger.warning(
                    f'Unable to find cluster name for endpoint {endpoint_url}. Skipping.'
                )
                continue
            name = ip_to_name_map[endpoint_url]
            if endpoint_url in unhealthy_servers:
                logger.info(f'Deleting SkyPilot cluster {name}')
                threading.Thread(target=sky.down,
                                 args=(name,),
                                 kwargs={
                                     'purge': True
                                 }).start()
