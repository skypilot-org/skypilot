"""InfraProvider: handles the creation and deletion of endpoint replicas."""
from concurrent import futures
import copy
import logging
import multiprocessing
import os
import random
import requests
import signal
import threading
import time
from typing import List, Dict, Set, Optional, Any, Union, Tuple

import sky
from sky import backends
from sky import core
from sky import status_lib
from sky.skylet import job_lib
from sky import global_user_state as gus

logger = logging.getLogger(__name__)

_JOB_STATUS_FETCH_INTERVAL = 30
_PROCESS_POOL_REFRESH_INTERVAL = 20
_ENDPOINT_PROBE_INTERVAL = 10
# TODO(tian): Maybe let user determine this threshold
_CONSECUTIVE_FAILURE_THRESHOLD = 180 // _ENDPOINT_PROBE_INTERVAL


class ReplicaInfo:
    """Replica info for each replica."""

    def __init__(self, cluster_name: str,
                 status: status_lib.ReplicaStatus) -> None:
        self.cluster_name: str = cluster_name
        self.status: status_lib.ReplicaStatus = status
        self.first_not_ready_time: Optional[float] = None
        self.consecutive_failure_cnt: int = 0

    def trancision_with_expected(self, expected_status: Union[
        status_lib.ReplicaStatus, List[status_lib.ReplicaStatus]],
                                 new_status: status_lib.ReplicaStatus) -> bool:
        if isinstance(expected_status, status_lib.ReplicaStatus):
            expected_status = [expected_status]
        if self.status not in expected_status:
            logger.info(f'Expected status {expected_status}, but got '
                        f'status {self.status}. Refuse to change to '
                        f'status {new_status}.')
            return False
        self.status = new_status
        return True

    @property
    def handle(self) -> Optional[backends.CloudVmRayResourceHandle]:
        cluster_record = gus.get_cluster_from_name(self.cluster_name)
        if cluster_record is None:
            return None
        handle = cluster_record['handle']
        assert isinstance(handle, backends.CloudVmRayResourceHandle)
        return handle

    @property
    def ip(self) -> Optional[str]:
        handle = self.handle
        if handle is None:
            return None
        return handle.head_ip

    def to_info_dict(self) -> Optional[Dict[str, Any]]:
        record = copy.deepcopy(self.__dict__)
        record['handle'] = self.handle
        if record['handle'] is None:
            # This means the cluster is already deleted
            # but the status is not updated yet.
            return None
        return record


class InfraProvider:
    """Each infra provider manages one service."""

    def __init__(
            self,
            readiness_path: str,
            readiness_timeout: int,
            post_data: Optional[Union[str, Dict[str, Any]]] = None) -> None:
        # TODO(tian): make this thread safe
        self.replica_info: Dict[str, ReplicaInfo] = dict()
        self.readiness_path: str = readiness_path
        self.readiness_timeout: int = readiness_timeout
        self.post_data: Optional[Union[str, Dict[str, Any]]] = post_data
        logger.info(f'Readiness probe path: {self.readiness_path}')
        logger.info(f'Post data: {self.post_data} ({type(self.post_data)})')

    def get_replica_info(self) -> List[Dict[str, Any]]:
        # Get replica info for all replicas
        raise NotImplementedError

    def total_replica_num(self) -> int:
        # Returns the total number of replicas, including those under
        # provisioning and deletion
        raise NotImplementedError

    def get_ready_replicas(self) -> Set[str]:
        # Returns the endpoints of all ready replicas
        raise NotImplementedError

    def scale_up(self, n: int) -> None:
        raise NotImplementedError

    def scale_down(self, n: int) -> None:
        # TODO - Scale down must also pass in a list of replicas to
        # delete or the number of replicas to delete
        raise NotImplementedError

    def terminate(self) -> Optional[str]:
        # Terminate service
        raise NotImplementedError

    def start_replica_prober(self) -> None:
        # Start the replica fetcher thread
        raise NotImplementedError

    def terminate_replica_prober(self) -> None:
        # Terminate the replica fetcher thread
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
        self._start_fetch_job_status()

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
                        info = self.replica_info[cluster_name]
                        if p.exitcode != 0:
                            logger.info(
                                f'{op} process for {cluster_name} exited '
                                f'abnormally with code {p.exitcode}.')
                            info.trancision_with_expected(
                                status_lib.ReplicaStatus.PROVISIONING,
                                status_lib.ReplicaStatus.FAILED)
                        else:
                            if op == 'Launch':
                                info.trancision_with_expected(
                                    status_lib.ReplicaStatus.PROVISIONING,
                                    status_lib.ReplicaStatus.STARTING)
                            else:
                                # TODO(tian): after we introduce SHUTTING_DOWN,
                                # we should change back to FAILED
                                # (Maybe FAILED_DOWN?)
                                if (info.status !=
                                        status_lib.ReplicaStatus.FAILED):
                                    del self.replica_info[cluster_name]
            time.sleep(_PROCESS_POOL_REFRESH_INTERVAL)

    def _start_refresh_process_pool(self) -> None:
        self.refresh_process_pool_stop_event = threading.Event()
        self.refresh_process_pool_thread = threading.Thread(
            target=self._refresh_process_pool)
        self.refresh_process_pool_thread.start()

    def _terminate_refresh_process_pool(self) -> None:
        self.refresh_process_pool_stop_event.set()
        self.refresh_process_pool_thread.join()

    def _fetch_job_status(self) -> None:
        while not self.fetch_job_status_stop_event.is_set():
            logger.info('Refreshing job status.')
            for cluster_name, replica_info in self.replica_info.items():
                if not replica_info.status in [
                        status_lib.ReplicaStatus.STARTING,
                        status_lib.ReplicaStatus.READY
                ]:
                    continue
                # Only fetch job 1, which stands for user task job
                job_statuses = core.job_status(cluster_name, [1])
                job_status = job_statuses['1']
                if job_status in [
                        job_lib.JobStatus.FAILED, job_lib.JobStatus.FAILED_SETUP
                ]:
                    replica_info.trancision_with_expected([
                        status_lib.ReplicaStatus.STARTING,
                        status_lib.ReplicaStatus.READY
                    ], status_lib.ReplicaStatus.FAILED)
            time.sleep(_JOB_STATUS_FETCH_INTERVAL)

    def _start_fetch_job_status(self) -> None:
        self.fetch_job_status_stop_event = threading.Event()
        self.fetch_job_status_thread = threading.Thread(
            target=self._fetch_job_status)
        self.fetch_job_status_thread.start()

    def _terminate_fetch_job_status(self) -> None:
        self.fetch_job_status_stop_event.set()
        self.fetch_job_status_thread.join()

    def get_replica_info(self) -> List[Dict[str, Any]]:
        infos = []
        for info in self.replica_info.values():
            info_dict = info.to_info_dict()
            if info_dict is not None:
                infos.append(info_dict)
        return infos

    def total_replica_num(self) -> int:
        return len(self.replica_info)

    def get_ready_replicas(self) -> Set[str]:
        ready_replicas = set()
        for info in self.replica_info.values():
            if info.status == status_lib.ReplicaStatus.READY:
                assert info.ip is not None
                ready_replicas.add(info.ip)
        return ready_replicas

    def _launch_cluster(self, cluster_name: str, task: sky.Task) -> None:
        if cluster_name in self.launch_process_pool:
            logger.warning(f'Launch process for cluster {cluster_name} '
                           'already exists. Skipping.')
            return
        p = multiprocessing.Process(target=sky.launch,
                                    args=(task,),
                                    kwargs={
                                        'cluster_name': cluster_name,
                                        'detach_setup': True,
                                        'detach_run': True,
                                        'retry_until_up': True
                                    })
        self.launch_process_pool[cluster_name] = p
        p.start()
        assert cluster_name not in self.replica_info
        self.replica_info[cluster_name] = ReplicaInfo(
            cluster_name, status_lib.ReplicaStatus.PROVISIONING)

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
        if cluster_name in self.down_process_pool:
            logger.warning(f'Down process for cluster {cluster_name} already '
                           'exists. Skipping.')
            return
        p = multiprocessing.Process(target=sky.down,
                                    args=(cluster_name,),
                                    kwargs={'purge': True})
        self.down_process_pool[cluster_name] = p
        p.start()

    def _scale_down(self, n: int) -> None:
        # Rendomly delete n clusters
        num_replicas = len(self.replica_info)
        if num_replicas > 0:
            if n > num_replicas:
                logger.warning(
                    f'Trying to delete {n} clusters, but only {num_replicas} '
                    'clusters exist. Deleting all clusters.')
                n = num_replicas
            cluster_to_terminate = random.sample(self.replica_info.keys(), n)
            for cluster_name in cluster_to_terminate:
                logger.info(f'Deleting SkyPilot cluster {cluster_name}')
                self._teardown_cluster(cluster_name)

    def scale_down(self, n: int) -> None:
        self._scale_down(n)

    def terminate(self) -> Optional[str]:
        self._terminate_refresh_process_pool()
        self._terminate_fetch_job_status()
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
                logger.info(f'Interrupted launch process for cluster {name} '
                            'and deleted the cluster.')
        for cluster_name in self.replica_info:
            self._teardown_cluster(cluster_name)
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

    def _replica_prober(self) -> None:
        while not self.replica_prober_stop_event.is_set():
            logger.info('Running replica fetcher.')
            try:
                self._probe_all_replicas()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # replica fetcher running.
                logger.error(f'Error in replica fetcher: {e}')
            time.sleep(_ENDPOINT_PROBE_INTERVAL)

    def start_replica_prober(self) -> None:
        self.replica_prober_stop_event = threading.Event()
        self.replica_prober_thread = threading.Thread(
            target=self._replica_prober)
        self.replica_prober_thread.start()

    def terminate_replica_prober(self) -> None:
        self.replica_prober_stop_event.set()
        self.replica_prober_thread.join()

    def _probe_all_replicas(self) -> None:
        logger.info(f'All replica info: {self.get_replica_info()}')

        def _probe_replica(info: ReplicaInfo) -> Tuple[str, bool]:
            replica_ip = info.ip
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
                    logger.info(f'Replica {replica_ip} is ready.')
                    return info.cluster_name, True
            except requests.exceptions.RequestException as e:
                logger.info(e)
                logger.info(f'Replica {replica_ip} is not ready.')
                pass
            return info.cluster_name, False

        probe_futures = []
        with futures.ThreadPoolExecutor() as executor:
            for cluster_name, info in self.replica_info.items():
                # Optimize: only probe replicas in STARTING or READY status
                # since probe to other replicas will fail anyway.
                if info.status not in [
                        status_lib.ReplicaStatus.STARTING,
                        status_lib.ReplicaStatus.READY
                ]:
                    continue
                probe_futures.append(executor.submit(_probe_replica, info))

        for future in futures.as_completed(probe_futures):
            cluster_name, res = future.result()
            if res:
                info = self.replica_info[cluster_name]
                if info.status == status_lib.ReplicaStatus.STARTING:
                    info.trancision_with_expected(
                        status_lib.ReplicaStatus.STARTING,
                        status_lib.ReplicaStatus.READY)
                continue
            info = self.replica_info[cluster_name]
            if info.first_not_ready_time is None:
                info.first_not_ready_time = time.time()
            if time.time() - info.first_not_ready_time > self.readiness_timeout:
                is_starting = info.trancision_with_expected(
                    status_lib.ReplicaStatus.STARTING,
                    status_lib.ReplicaStatus.FAILED)
                if is_starting:
                    logger.info(f'Replica {cluster_name} is not ready and '
                                'exceeded readiness timeout. Terminating.')
                    self._teardown_cluster(cluster_name)
                else:
                    info.consecutive_failure_cnt += 1
                    if (info.consecutive_failure_cnt >
                            _CONSECUTIVE_FAILURE_THRESHOLD):
                        info.trancision_with_expected(
                            status_lib.ReplicaStatus.READY,
                            status_lib.ReplicaStatus.FAILED)
                        logger.info(f'Terminating replica {cluster_name}.')
                        self._teardown_cluster(cluster_name)
                    else:
                        # TODO(tian): Change to NOT_READY?
                        logger.info(f'Replica {cluster_name} is not ready but '
                                    'within consecutive failure threshold. '
                                    'Skipping.')
            else:
                logger.info(f'Replica {cluster_name} is not ready but within '
                            'readiness timeout. Skipping.')
