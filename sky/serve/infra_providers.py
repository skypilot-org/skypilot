"""InfraProvider: handles the creation and deletion of endpoint replicas."""
import base64
import collections
from concurrent import futures
import logging
import multiprocessing
import os
import pickle
import requests
import signal
import threading
import time
from typing import List, Dict, Set, Optional, Any

import sky
from sky import backends
from sky import status_lib
from sky.backends import backend_utils

logger = logging.getLogger(__name__)

_PROCESS_POOL_REFRESH_INTERVAL = 20
_ENDPOINT_PROBE_INTERVAL = 10
# TODO(tian): Maybe let user determine this threshold
_REPLICA_UNHEALTHY_THRESHOLD_COUNTER = 180 // _ENDPOINT_PROBE_INTERVAL


class InfraProvider:
    """Each infra provider manages one services."""

    def __init__(self,
                 readiness_path: str,
                 readiness_timeout: int,
                 post_data: Optional[Any] = None) -> None:
        self.healthy_replicas: Set[str] = set()
        self.unhealthy_replicas: Set[str] = set()
        self.failed_replicas: Set[str] = set()
        self.first_unhealthy_time: Dict[str, float] = dict()
        self.continuous_unhealthy_counter: Dict[
            str, int] = collections.defaultdict(int)
        self.readiness_path: str = readiness_path
        self.readiness_timeout: int = readiness_timeout
        self.post_data: Any = post_data
        logger.info(f'Readiness probe path: {self.readiness_path}')
        logger.info(f'Post data: {self.post_data} ({type(self.post_data)})')

    def get_replica_info(self) -> List[Dict[str, str]]:
        # Get replica info for all replicas
        raise NotImplementedError

    def _get_replica_ips(self) -> Set[str]:
        # Get all replica ips
        raise NotImplementedError

    def total_replica_num(self) -> int:
        # Returns the total number of replicas, including those under
        # provisioning and deletion
        raise NotImplementedError

    def healthy_replica_num(self) -> int:
        # Returns the total number of available replicas
        raise NotImplementedError

    def get_healthy_replicas(self) -> Set[str]:
        # Returns the endpoints of all healthy replicas
        raise NotImplementedError

    def unhealthy_replica_num(self) -> int:
        # Returns the total number of unhealthy replicas
        raise NotImplementedError

    def failed_replica_num(self) -> int:
        # Returns the number of failed replicas
        raise NotImplementedError

    def scale_up(self, n: int) -> None:
        raise NotImplementedError

    def scale_down(self, n: int) -> None:
        # TODO - Scale down must also pass in a list of replicas to
        # delete or the number of replicas to delete
        raise NotImplementedError

    def _terminate_replicas(self, unhealthy_replicas: Set[str]) -> None:
        # Terminates the replicas with endpoints in the list
        raise NotImplementedError

    def terminate(self) -> Optional[str]:
        # Terminate service
        raise NotImplementedError

    def start_replica_fetcher(self) -> None:
        # Start the replica fetcher thread
        raise NotImplementedError

    def terminate_replica_fetcher(self) -> None:
        # Terminate the replica fetcher thread
        raise NotImplementedError

    def probe_all_endpoints(self) -> None:
        # Probe readiness of all endpoints
        raise NotImplementedError


class SkyPilotInfraProvider(InfraProvider):
    """Infra provider for SkyPilot clusters."""

    def __init__(self, task_yaml_path: str, cluster_name_prefix: str, *args,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.task_yaml_path: str = task_yaml_path
        self.cluster_name_prefix: str = cluster_name_prefix + '-'
        self.id_counter: int = 1
        self.launch_process_pool: Dict[str, multiprocessing.Process] = dict()
        self.down_process_pool: Dict[str, multiprocessing.Process] = dict()

        self._start_refresh_process_pool()

    def _refresh_process_pool(self) -> None:
        while not self.refresh_process_pool_stop_event.is_set():
            logger.info('Refreshing process pool.')
            for op, pool in zip(
                ['Launch', 'Down'],
                [self.launch_process_pool, self.down_process_pool]):
                for cluster_name, p in list(pool.items()):
                    if not p.is_alive():
                        # TODO(tian): Try-catch in process, and have an enum
                        # return value to indicate which type of failure
                        # happened. Currently we only have user code failure
                        # since the retry_until_up flag is set to True, but it
                        # will be helpful when we enable user choose whether to
                        # retry or not.
                        logger.info(
                            f'{op} process for {cluster_name} finished.')
                        del pool[cluster_name]
                        if p.exitcode != 0:
                            logger.info(
                                f'{op} process for {cluster_name} exited '
                                f'abnormally with code {p.exitcode}.')
                            self.failed_replicas.add(cluster_name)
            time.sleep(_PROCESS_POOL_REFRESH_INTERVAL)

    def _start_refresh_process_pool(self) -> None:
        self.refresh_process_pool_stop_event = threading.Event()
        self.refresh_process_pool_thread = threading.Thread(
            target=self._refresh_process_pool)
        self.refresh_process_pool_thread.start()

    def _terminate_refresh_process_pool(self) -> None:
        self.refresh_process_pool_stop_event.set()
        self.refresh_process_pool_thread.join()

    def _get_ip_clusname_map(self) -> Dict[str, str]:
        """
        Returns a map of ip to cluster name for all clusters.
        """
        clusters = sky.global_user_state.get_clusters()
        ip_clusname_map = {}
        dummy_counter = 0
        for cluster in clusters:
            name = cluster['name']
            handle = cluster['handle']
            try:
                # Get the head node ip
                ip = backend_utils.get_node_ips(handle.cluster_yaml,
                                                handle.launched_nodes,
                                                handle)[0]
                ip_clusname_map[ip] = name
            except sky.exceptions.FetchIPError:
                logger.warning(f'Unable to get IP for cluster {name}.'
                               'Use dummp IP instead.')
                ip_clusname_map[f'10.0.0.{dummy_counter}'] = name
                dummy_counter += 1
                continue
        return ip_clusname_map

    def get_replica_info(self) -> List[Dict[str, str]]:

        def _get_replica_status(cluster_status: status_lib.ClusterStatus,
                                ip: str) -> status_lib.ReplicaStatus:
            if ip in self.healthy_replicas:
                return status_lib.ReplicaStatus.RUNNING
            if ip in self.failed_replicas:
                return status_lib.ReplicaStatus.FAILED
            if cluster_status == status_lib.ClusterStatus.UP:
                return status_lib.ReplicaStatus.UNHEALTHY
            return status_lib.ReplicaStatus.INIT

        # TODO(tian): Return failed replica info here if it is already
        # be torn down.
        clusters = sky.global_user_state.get_clusters()
        infos = []
        for cluster in clusters:
            handle = cluster['handle']
            assert isinstance(handle, backends.CloudVmRayResourceHandle)
            ip = handle.head_ip
            info = {
                'name': cluster['name'],
                'handle': handle,
                'status': _get_replica_status(cluster['status'], ip),
            }
            info = {
                k: base64.b64encode(pickle.dumps(v)).decode('utf-8')
                for k, v in info.items()
            }
            infos.append(info)
        return infos

    def _get_replica_ips(self) -> Set[str]:
        ips = set(self._get_ip_clusname_map().keys())
        logger.info(f'Returning SkyPilot endpoints: {ips}')
        return ips

    def total_replica_num(self) -> int:
        clusters = sky.global_user_state.get_clusters()
        # All replica launched in controller is a replica.
        return len(clusters)

    def get_healthy_replicas(self) -> Set[str]:
        return self.healthy_replicas

    def healthy_replica_num(self) -> int:
        return len(self.healthy_replicas)

    def unhealthy_replica_num(self) -> int:
        return len(self.unhealthy_replicas)

    def failed_replica_num(self) -> int:
        return len(self.failed_replicas)

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

    def _scale_up(self, n: int) -> None:
        # Launch n new clusters
        task = sky.Task.from_yaml(self.task_yaml_path)
        for _ in range(0, n):
            cluster_name = f'{self.cluster_name_prefix}{self.id_counter}'
            logger.info(f'Creating SkyPilot cluster {cluster_name}')
            self._launch_cluster(cluster_name, task)
            self.id_counter += 1

    def scale_up(self, n: int) -> None:
        self._scale_up(n)

    def _teardown_cluster(self, cluster_name: str) -> None:
        p = multiprocessing.Process(target=sky.down,
                                    args=(cluster_name,),
                                    kwargs={'purge': True})
        self.down_process_pool[cluster_name] = p
        p.start()

    def _scale_down(self, n: int) -> None:
        # Delete n clusters
        # Currently deletes the first n clusters
        clusters = sky.global_user_state.get_clusters()
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

    def scale_down(self, n: int) -> None:
        self._scale_down(n)

    def _terminate_replicas(self, unhealthy_replicas: Set[str]) -> None:
        # Remove unhealthy replicas from current_endpoints
        logger.info('SkyPilotInfraProvider._terminate_replicas called with '
                    f'unhealthy_replicas={unhealthy_replicas}')
        for endpoint_url in unhealthy_replicas:
            ip_to_name_map = self._get_ip_clusname_map()
            if endpoint_url not in ip_to_name_map:
                logger.warning(
                    f'Unable to find cluster name for endpoint {endpoint_url}. '
                    'Skipping.')
                continue
            name = ip_to_name_map[endpoint_url]
            if endpoint_url in unhealthy_replicas:
                logger.info(f'Deleting SkyPilot cluster {name}')
                self._teardown_cluster(name)

    def terminate(self) -> Optional[str]:
        # For correctly show serve status
        self.healthy_replicas.clear()
        self.unhealthy_replicas = self._get_replica_ips()
        self._terminate_refresh_process_pool()
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
                logger.info(f'Interrupted launch process for cluster {name}'
                            'and deleted the cluster.')
        replica_ips = self._get_replica_ips()
        self._terminate_replicas(replica_ips)
        msg = []
        for name, p in self.down_process_pool.items():
            p.join()
            logger.info(f'Down process for cluster {name} finished.')
            if p.exitcode != 0:
                msg.append(f'Down process for cluster {name} exited abnormally'
                           f' with code {p.exitcode}. Please login to the '
                           'controller and make sure the cluster is released.')
        if not msg:
            return None
        return '\n'.join(msg)

    def _replica_fetcher(self) -> None:
        while not self.replica_fetcher_stop_event.is_set():
            logger.info('Running replica fetcher.')
            try:
                self.probe_all_endpoints()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # replica fetcher running.
                logger.error(f'Error in replica fetcher: {e}')
            time.sleep(_ENDPOINT_PROBE_INTERVAL)

    def start_replica_fetcher(self) -> None:
        self.replica_fetcher_stop_event = threading.Event()
        self.replica_fetcher_thread = threading.Thread(
            target=self._replica_fetcher)
        self.replica_fetcher_thread.start()

    def terminate_replica_fetcher(self) -> None:
        self.replica_fetcher_stop_event.set()
        self.replica_fetcher_thread.join()

    def probe_all_endpoints(self) -> None:
        replica_ips = self._get_replica_ips() - self.failed_replicas

        def probe_endpoint(replica_ip: str) -> Optional[str]:
            try:
                msg = ''
                readiness_url = f'http://{replica_ip}{self.readiness_path}'
                if self.post_data is not None:
                    msg += 'Post'
                    response = requests.post(readiness_url,
                                             json=self.post_data,
                                             timeout=3)
                else:
                    msg += 'Get'
                    response = requests.get(readiness_url, timeout=3)
                msg += (f' request to {replica_ip} returned status code '
                        f'{response.status_code}')
                if response.status_code == 200:
                    msg += '.'
                else:
                    msg += f' and response {response.text}.'
                logger.info(msg)
                if response.status_code == 200:
                    logger.info(f'Replica {replica_ip} is available.')
                    return replica_ip
            except requests.exceptions.RequestException as e:
                logger.info(e)
                logger.info(f'Replica {replica_ip} is not available.')
                pass
            return None

        with futures.ThreadPoolExecutor() as executor:
            probe_futures = [
                executor.submit(probe_endpoint, replica_ip)
                for replica_ip in replica_ips
            ]
            healthy_replicas = set()
            for future in futures.as_completed(probe_futures):
                ip = future.result()
                if ip is not None:
                    healthy_replicas.add(ip)

        logger.info(f'Healthy replicas: {healthy_replicas}')
        self.healthy_replicas = healthy_replicas
        unhealthy_replicas = replica_ips - healthy_replicas
        logger.info(f'Unhealthy replicas: {unhealthy_replicas}')
        self.unhealthy_replicas = unhealthy_replicas

        for replica in healthy_replicas:
            self.continuous_unhealthy_counter[replica] = 0

        replicas_to_terminate = set()
        for replica in unhealthy_replicas:
            if replica not in self.first_unhealthy_time:
                self.first_unhealthy_time[replica] = time.time()
            self.continuous_unhealthy_counter[replica] += 1
            # coldstart time limitation is `self.readiness_timeout`.
            first_unhealthy_time = self.first_unhealthy_time[replica]
            if time.time() - first_unhealthy_time > self.readiness_timeout:
                continuous_unhealthy_times = self.continuous_unhealthy_counter[
                    replica]
                if (continuous_unhealthy_times >
                        _REPLICA_UNHEALTHY_THRESHOLD_COUNTER):
                    logger.info(f'Terminating replica {replica}.')
                    replicas_to_terminate.add(replica)
                else:
                    logger.info(f'Replica {replica} is unhealthy but '
                                'within unhealthy threshold. Skipping.')
            else:
                logger.info(f'Replica {replica} is unhealthy but within '
                            'readiness timeout. Skipping.')

        self._terminate_replicas(replicas_to_terminate)
