"""InfraProvider: handles the creation and deletion of endpoint replicas."""
from concurrent import futures
import enum
import logging
import os
import random
import requests
import signal
import subprocess
import threading
import time
from typing import List, Dict, Set, Optional, Any, Union, Tuple

from sky import backends
from sky import core
from sky import status_lib
from sky.serve import serve_utils
from sky.skylet import job_lib
from sky import global_user_state

logger = logging.getLogger(__name__)

_JOB_STATUS_FETCH_INTERVAL = 30
_PROCESS_POOL_REFRESH_INTERVAL = 20
_ENDPOINT_PROBE_INTERVAL = 10
# TODO(tian): Maybe let user determine this threshold
_CONSECUTIVE_FAILURE_THRESHOLD_TIMEOUT = 180
_CONSECUTIVE_FAILURE_THRESHOLD_COUNT = (
    _CONSECUTIVE_FAILURE_THRESHOLD_TIMEOUT // _ENDPOINT_PROBE_INTERVAL)


class ProcessStatus(enum.Enum):
    """Process status."""

    # The process is running
    RUNNING = 'RUNNING'

    # The process is finished and success
    SUCCESS = 'SUCCESS'

    # The process is finished and failed
    FAILED = 'FAILED'


class ReplicaStatusProperty:
    """Some properties that determine replica status."""

    def __init__(self) -> None:
        # Process status of sky.launch
        # Initial value is RUNNING since each `ReplicaInfo` is created
        # when `sky.launch` is called.
        self.sky_launch_status: ProcessStatus = ProcessStatus.RUNNING
        # User job status in [FAILED, FAILED_SETUP]
        self.user_app_failed: bool = False
        # Latest readiness probe result
        self.service_ready_now: bool = False
        # Whether the service has been ready at least once
        self.service_once_ready: bool = False
        # Process status of sky.down. None means sky.down is not called yet.
        self.sky_down_status: Optional[ProcessStatus] = None

    def should_cleanup_after_teardown(self) -> bool:
        if self.sky_launch_status != ProcessStatus.SUCCESS:
            return False
        if self.sky_down_status != ProcessStatus.SUCCESS:
            return False
        if self.user_app_failed:
            return False
        return self.service_once_ready

    def should_track_status(self) -> bool:
        if self.sky_launch_status != ProcessStatus.SUCCESS:
            return False
        if self.sky_down_status is not None:
            return False
        if self.user_app_failed:
            return False
        return True

    def to_replica_status(self) -> status_lib.ReplicaStatus:
        if self.sky_launch_status == ProcessStatus.RUNNING:
            # Still launching
            return status_lib.ReplicaStatus.PROVISIONING
        if self.sky_launch_status == ProcessStatus.FAILED:
            # sky.launch failed
            return status_lib.ReplicaStatus.FAILED
        if self.sky_down_status is not None:
            if self.sky_down_status == ProcessStatus.RUNNING:
                # sky.down is running
                return status_lib.ReplicaStatus.SHUTTING_DOWN
            if self.sky_down_status == ProcessStatus.FAILED:
                # sky.down failed
                return status_lib.ReplicaStatus.FAILED
            if self.user_app_failed:
                # Failed on user setup/run
                return status_lib.ReplicaStatus.FAILED_DELETED
            if not self.service_once_ready:
                # Readiness timeout exceeded
                return status_lib.ReplicaStatus.FAILED_DELETED
            if not self.service_ready_now:
                # Max continuous failure exceeded
                return status_lib.ReplicaStatus.FAILED_DELETED
            # This indicate it is a scale_down with correct teardown.
            # Should have been cleaned from the replica_info.
            return status_lib.ReplicaStatus.UNKNOWN
        if self.service_ready_now:
            # Service is ready
            return status_lib.ReplicaStatus.READY
        if self.user_app_failed:
            # Failed on user setup/run
            return status_lib.ReplicaStatus.FAILED
        if self.service_once_ready:
            # Service was ready before but not now
            return status_lib.ReplicaStatus.NOT_READY
        else:
            # No readiness probe passed and sky.launch finished
            return status_lib.ReplicaStatus.STARTING


class ReplicaInfo:
    """Replica info for each replica."""

    def __init__(self, replica_id: int, cluster_name: str) -> None:
        self.replica_id: int = replica_id
        self.cluster_name: str = cluster_name
        self.first_not_ready_time: Optional[float] = None
        self.consecutive_failure_cnt: int = 0
        self.status_property: ReplicaStatusProperty = ReplicaStatusProperty()

    @property
    def handle(self) -> Optional[backends.CloudVmRayResourceHandle]:
        cluster_record = global_user_state.get_cluster_from_name(
            self.cluster_name)
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

    @property
    def status(self) -> status_lib.ReplicaStatus:
        return self.status_property.to_replica_status()

    def to_info_dict(self) -> Dict[str, Any]:
        return {
            'replica_id': self.replica_id,
            'name': self.cluster_name,
            'status': self.status,
            'handle': self.handle,
        }


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


class SkyPilotInfraProvider(InfraProvider):
    """Infra provider for SkyPilot clusters."""

    def __init__(self, task_yaml_path: str, service_name: str, *args,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.task_yaml_path: str = task_yaml_path
        self.service_name: str = service_name
        self.next_replica_id: int = 1
        self.launch_process_pool: Dict[str, subprocess.Popen] = dict()
        self.down_process_pool: Dict[str, subprocess.Popen] = dict()

        self._start_process_pool_refresher()
        self._start_job_status_fetcher()

    def _refresh_process_pool(self) -> None:
        for cluster_name, p in list(self.launch_process_pool.items()):
            if p.poll() is not None:
                # TODO(tian): Try-catch in process, and have an enum return
                # value to indicate which type of failure happened.
                # Currently we only have user code failure since the
                # retry_until_up flag is set to True, but it will be helpful
                # when we enable user choose whether to retry or not.
                logger.info(f'Launch process for {cluster_name} finished.')
                del self.launch_process_pool[cluster_name]
                info = self.replica_info[cluster_name]
                if p.returncode != 0:
                    logger.info(f'Launch process for {cluster_name} exited '
                                f'abnormally with code {p.returncode}.')
                    info.status_property.sky_launch_status = (
                        ProcessStatus.FAILED)
                else:
                    info.status_property.sky_launch_status = (
                        ProcessStatus.SUCCESS)
        for cluster_name, p in list(self.down_process_pool.items()):
            if p.poll() is not None:
                logger.info(f'Down process for {cluster_name} finished.')
                del self.down_process_pool[cluster_name]
                info = self.replica_info[cluster_name]
                if p.returncode != 0:
                    logger.info(f'Down process for {cluster_name} exited '
                                f'abnormally with code {p.returncode}.')
                    info.status_property.sky_down_status = (
                        ProcessStatus.FAILED)
                else:
                    info.status_property.sky_down_status = (
                        ProcessStatus.SUCCESS)
                # Failed replica still count as a replica. In our current
                # design, we want to fail early if user code have any error.
                # This will prevent infinite loop of teardown and
                # re-provision.
                if info.status_property.should_cleanup_after_teardown():
                    # This means the cluster is deleted due to
                    # a scale down. Delete the replica info
                    # so it won't count as a replica.
                    del self.replica_info[cluster_name]

    def _process_pool_refresher(self) -> None:
        while not self.process_pool_refresher_stop_event.is_set():
            logger.info('Refreshing process pool.')
            try:
                self._refresh_process_pool()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # process pool refresher running.
                logger.error(f'Error in process pool refresher: {e}')
            time.sleep(_PROCESS_POOL_REFRESH_INTERVAL)

    def _start_process_pool_refresher(self) -> None:
        self.process_pool_refresher_stop_event = threading.Event()
        self.process_pool_refresher_thread = threading.Thread(
            target=self._process_pool_refresher)
        self.process_pool_refresher_thread.start()

    def _fetch_job_status(self) -> None:
        for cluster_name, info in self.replica_info.items():
            if not info.status_property.should_track_status():
                continue
            # Only fetch job 1, which stands for user task job
            job_statuses = core.job_status(cluster_name, [1])
            job_status = job_statuses['1']
            if job_status in [
                    job_lib.JobStatus.FAILED, job_lib.JobStatus.FAILED_SETUP
            ]:
                info.status_property.user_app_failed = True

    def _job_status_fetcher(self) -> None:
        while not self.job_status_fetcher_stop_event.is_set():
            logger.info('Refreshing job status.')
            try:
                self._fetch_job_status()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # job status fetcher running.
                logger.error(f'Error in job status fetcher: {e}')
            time.sleep(_JOB_STATUS_FETCH_INTERVAL)

    def _start_job_status_fetcher(self) -> None:
        self.job_status_fetcher_stop_event = threading.Event()
        self.job_status_fetcher_thread = threading.Thread(
            target=self._job_status_fetcher)
        self.job_status_fetcher_thread.start()

    def _terminate_daemon_threads(self) -> None:
        self.replica_prober_stop_event.set()
        self.job_status_fetcher_stop_event.set()
        self.process_pool_refresher_stop_event.set()
        self.replica_prober_thread.join()
        self.job_status_fetcher_thread.join()
        self.process_pool_refresher_thread.join()

    def get_replica_info(self) -> List[Dict[str, Any]]:
        return [info.to_info_dict() for info in self.replica_info.values()]

    def total_replica_num(self) -> int:
        return len(self.replica_info)

    def get_ready_replicas(self) -> Set[str]:
        ready_replicas = set()
        for info in self.replica_info.values():
            if info.status == status_lib.ReplicaStatus.READY:
                assert info.ip is not None
                ready_replicas.add(info.ip)
        return ready_replicas

    def _launch_cluster(self, replica_id: int) -> None:
        cluster_name = serve_utils.generate_replica_cluster_name(
            self.service_name, replica_id)
        if cluster_name in self.launch_process_pool:
            logger.warning(f'Launch process for cluster {cluster_name} '
                           'already exists. Skipping.')
            return
        logger.info(f'Creating SkyPilot cluster {cluster_name}')
        cmd = ['sky', 'launch', self.task_yaml_path, '-c', cluster_name, '-y']
        cmd.extend(['--detach-setup', '--detach-run', '--retry-until-up'])
        fn = serve_utils.generate_replica_launch_log_file_name(cluster_name)
        with open(fn, 'w') as f:
            # pylint: disable=consider-using-with
            p = subprocess.Popen(cmd,
                                 stdin=subprocess.DEVNULL,
                                 stdout=f,
                                 stderr=f)
        self.launch_process_pool[cluster_name] = p
        assert cluster_name not in self.replica_info
        self.replica_info[cluster_name] = ReplicaInfo(replica_id, cluster_name)

    def _scale_up(self, n: int) -> None:
        # Launch n new clusters
        for _ in range(0, n):
            self._launch_cluster(self.next_replica_id)
            self.next_replica_id += 1

    def scale_up(self, n: int) -> None:
        self._scale_up(n)

    def _teardown_cluster(self, cluster_name: str) -> None:
        if cluster_name in self.down_process_pool:
            logger.warning(f'Down process for cluster {cluster_name} already '
                           'exists. Skipping.')
            return
        cmd = ['sky', 'down', cluster_name, '--purge', '-y']
        fn = serve_utils.generate_replica_down_log_file_name(cluster_name)
        with open(fn, 'w') as f:
            # pylint: disable=consider-using-with
            p = subprocess.Popen(cmd,
                                 stdin=subprocess.DEVNULL,
                                 stdout=f,
                                 stderr=f)
        self.down_process_pool[cluster_name] = p
        info = self.replica_info[cluster_name]
        info.status_property.sky_down_status = ProcessStatus.RUNNING

    def _scale_down(self, n: int) -> None:
        # Rendomly delete n clusters
        all_ready_replicas = self.get_ready_replicas()
        num_replicas = len(all_ready_replicas)
        if num_replicas > 0:
            if n > num_replicas:
                logger.warning(
                    f'Trying to delete {n} clusters, but only {num_replicas} '
                    'clusters exist. Deleting all clusters.')
                n = num_replicas
            cluster_to_terminate = random.sample(all_ready_replicas, n)
            for cluster_name in cluster_to_terminate:
                logger.info(f'Deleting SkyPilot cluster {cluster_name}')
                self._teardown_cluster(cluster_name)

    def scale_down(self, n: int) -> None:
        self._scale_down(n)

    def terminate(self) -> Optional[str]:
        logger.info('Terminating infra provider daemon threads...')
        self._terminate_daemon_threads()
        logger.info('Terminating all clusters...')
        for name, p in self.launch_process_pool.items():
            # Use keyboard interrupt here since sky.launch has great
            # handling for it
            # Edge case: sky.launched finished after the
            # process_pool_refresher terminates
            if p.poll() is None:
                assert p.pid is not None
                os.killpg(os.getpgid(p.pid), signal.SIGINT)
                p.wait()
                self._teardown_cluster(name)
                logger.info(f'Interrupted launch process for cluster {name} '
                            'and deleted the cluster.')
                info = self.replica_info[name]
                # Set to success here for correctly display as shutting down
                info.status_property.sky_launch_status = ProcessStatus.SUCCESS
        for name, info in self.replica_info.items():
            # Skip those already deleted and those are deleting
            if info.status not in [
                    status_lib.ReplicaStatus.FAILED_DELETED,
                    status_lib.ReplicaStatus.SHUTTING_DOWN
            ]:
                self._teardown_cluster(name)
        msg = []
        for name, p in self.down_process_pool.items():
            p.wait()
            logger.info(f'Down process for cluster {name} finished.')
            if p.returncode != 0:
                logger.warning(f'Down process for cluster {name} exited '
                               f'anormally with code {p.returncode}.')
                msg.append(f'Down process for cluster {name} exited abnormally'
                           f' with code {p.returncode}. Please login to the '
                           'controller and make sure the cluster is released.')
        if not msg:
            return None
        return '\n'.join(msg)

    def _replica_prober(self) -> None:
        while not self.replica_prober_stop_event.is_set():
            logger.info('Running replica prober.')
            try:
                self._probe_all_replicas()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # replica prober running.
                logger.error(f'Error in replica prober: {e}')
            time.sleep(_ENDPOINT_PROBE_INTERVAL)

    def start_replica_prober(self) -> None:
        self.replica_prober_stop_event = threading.Event()
        self.replica_prober_thread = threading.Thread(
            target=self._replica_prober)
        self.replica_prober_thread.start()

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
                if not info.status_property.should_track_status():
                    continue
                probe_futures.append(executor.submit(_probe_replica, info))

        for future in futures.as_completed(probe_futures):
            cluster_name, res = future.result()
            info = self.replica_info[cluster_name]
            info.status_property.service_ready_now = res
            if res:
                if not info.status_property.service_once_ready:
                    info.status_property.service_once_ready = True
                continue
            if info.first_not_ready_time is None:
                info.first_not_ready_time = time.time()
            if info.status_property.service_once_ready:
                info.consecutive_failure_cnt += 1
                if (info.consecutive_failure_cnt >
                        _CONSECUTIVE_FAILURE_THRESHOLD_COUNT):
                    logger.info(f'Replica {cluster_name} is consecutively '
                                'not ready for too long and exceeding '
                                'conservative failure threshold. '
                                'Terminating.')
                    self._teardown_cluster(cluster_name)
                else:
                    current_unready_time = (info.consecutive_failure_cnt *
                                            _ENDPOINT_PROBE_INTERVAL)
                    logger.info(f'Replica {cluster_name} is not ready but '
                                'within consecutive failure threshold '
                                f'({current_unready_time}s / '
                                f'{_CONSECUTIVE_FAILURE_THRESHOLD_TIMEOUT}s). '
                                'Skipping.')
            else:
                if time.time(
                ) - info.first_not_ready_time > self.readiness_timeout:
                    logger.info(f'Replica {cluster_name} is not ready and '
                                'exceeding readiness timeout. Terminating.')
                    self._teardown_cluster(cluster_name)
                else:
                    logger.info(
                        f'Replica {cluster_name} is not ready but within '
                        'readiness timeout. Skipping.')
