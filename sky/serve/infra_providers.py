"""InfraProvider: handles the creation and deletion of endpoint replicas."""
from concurrent import futures
import dataclasses
import enum
import functools
import logging
import os
import signal
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import psutil
import requests

from sky import backends
from sky import global_user_state
from sky.backends import backend_utils
from sky.serve import constants as serve_constants
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import constants
from sky.skylet import job_lib
from sky.utils import env_options

logger = logging.getLogger(__name__)

_JOB_STATUS_FETCH_INTERVAL = 30
_PROCESS_POOL_REFRESH_INTERVAL = 20
_ENDPOINT_PROBE_INTERVAL = 10
# TODO(tian): Maybe let user determine this threshold
_CONSECUTIVE_FAILURE_THRESHOLD_TIMEOUT = 180


def _interrupt_process_and_children(pid: int) -> None:
    parent_process = psutil.Process(pid)
    for child_process in parent_process.children(recursive=True):
        try:
            child_process.send_signal(signal.SIGINT)
        except psutil.NoSuchProcess:
            pass
    try:
        parent_process.send_signal(signal.SIGINT)
    except psutil.NoSuchProcess:
        pass


def with_lock(func):

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return func(self, *args, **kwargs)

    return wrapper


class ProcessStatus(enum.Enum):
    """Process status."""

    # The process is running
    RUNNING = 'RUNNING'

    # The process is finished and succeeded
    SUCCEEDED = 'SUCCEEDED'

    # The process failed
    FAILED = 'FAILED'


@dataclasses.dataclass
class ReplicaStatusProperty:
    """Some properties that determine replica status."""
    # Process status of sky.launch
    # Initial value is RUNNING since each `ReplicaInfo` is created
    # when `sky.launch` is called.
    sky_launch_status: ProcessStatus = ProcessStatus.RUNNING
    # User job status in [FAILED, FAILED_SETUP]
    user_app_failed: bool = False
    # Latest readiness probe result
    service_ready_now: bool = False
    # Whether the service has been ready at least once
    # If service was not ready before, we count how long it takes to startup
    # and compare it with the initial delay seconds; otherwise, we count how
    # many consecutive failures it has.
    service_once_ready: bool = False
    # Process status of sky.down. None means sky.down is not called yet.
    sky_down_status: Optional[ProcessStatus] = None

    def is_scale_down_succeeded(self) -> bool:
        if self.sky_launch_status != ProcessStatus.SUCCEEDED:
            return False
        if self.sky_down_status != ProcessStatus.SUCCEEDED:
            return False
        if self.user_app_failed:
            return False
        if not self.service_ready_now:
            return False
        return self.service_once_ready

    def should_track_status(self) -> bool:
        if self.sky_launch_status != ProcessStatus.SUCCEEDED:
            return False
        if self.sky_down_status is not None:
            return False
        if self.user_app_failed:
            return False
        return True

    def to_replica_status(self) -> serve_state.ReplicaStatus:
        if self.sky_launch_status == ProcessStatus.RUNNING:
            # Still launching
            return serve_state.ReplicaStatus.PROVISIONING
        if self.sky_down_status is not None:
            if self.sky_down_status == ProcessStatus.RUNNING:
                # sky.down is running
                return serve_state.ReplicaStatus.SHUTTING_DOWN
            if self.sky_down_status == ProcessStatus.FAILED:
                # sky.down failed
                return serve_state.ReplicaStatus.FAILED_CLEANUP
            if self.user_app_failed:
                # Failed on user setup/run
                return serve_state.ReplicaStatus.FAILED
            if not self.service_once_ready:
                # initial delay seconds exceeded
                return serve_state.ReplicaStatus.FAILED
            if not self.service_ready_now:
                # Max continuous failure exceeded
                return serve_state.ReplicaStatus.FAILED
            if self.sky_launch_status == ProcessStatus.FAILED:
                # sky.launch failed
                return serve_state.ReplicaStatus.FAILED
            # This indicate it is a scale_down with correct teardown.
            # Should have been cleaned from the replica table.
            return serve_state.ReplicaStatus.UNKNOWN
        if self.sky_launch_status == ProcessStatus.FAILED:
            # sky.launch failed
            # Down process should have been started.
            # If not started, this means some bug prevent sky.down from
            # executing. It is also a potential resource leak, so we mark
            # it as FAILED_CLEANUP.
            return serve_state.ReplicaStatus.FAILED_CLEANUP
        if self.service_ready_now:
            # Service is ready
            return serve_state.ReplicaStatus.READY
        if self.user_app_failed:
            # Failed on user setup/run
            # Same as above
            return serve_state.ReplicaStatus.FAILED_CLEANUP
        if self.service_once_ready:
            # Service was ready before but not now
            return serve_state.ReplicaStatus.NOT_READY
        else:
            # No readiness probe passed and sky.launch finished
            return serve_state.ReplicaStatus.STARTING


# TODO(tian): Maybe rename it to Replica
class ReplicaInfo:
    """Replica info for each replica."""

    def __init__(self, replica_id: int, cluster_name: str) -> None:
        self.replica_id: int = replica_id
        self.cluster_name: str = cluster_name
        self.first_not_ready_time: Optional[float] = None
        self.consecutive_failure_times: List[float] = []
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
    def status(self) -> serve_state.ReplicaStatus:
        replica_status = self.status_property.to_replica_status()
        if replica_status == serve_state.ReplicaStatus.UNKNOWN:
            logger.error('Detecting UNKNOWN replica status for cluster '
                         f'{self.cluster_name}')
        return replica_status

    def to_info_dict(self, with_handle: bool) -> Dict[str, Any]:
        info_dict = {
            'replica_id': self.replica_id,
            'name': self.cluster_name,
            'status': self.status,
        }
        if with_handle:
            info_dict['handle'] = self.handle
        return info_dict

    def probe(
        self, readiness_suffix: str, post_data: Optional[Union[str, Dict[str,
                                                                         Any]]]
    ) -> Tuple['ReplicaInfo', bool]:
        replica_ip = self.ip
        try:
            msg = ''
            # TODO(tian): Support HTTPS in the future.
            readiness_path = f'http://{replica_ip}{readiness_suffix}'
            if post_data is not None:
                msg += 'Post'
                response = requests.post(
                    readiness_path,
                    json=post_data,
                    timeout=serve_constants.READINESS_PROBE_TIMEOUT)
            else:
                msg += 'Get'
                response = requests.get(
                    readiness_path,
                    timeout=serve_constants.READINESS_PROBE_TIMEOUT)
            msg += (f' request to {replica_ip} returned status code '
                    f'{response.status_code}')
            if response.status_code == 200:
                msg += '.'
            else:
                msg += f' and response {response.text}.'
            logger.info(msg)
            if response.status_code == 200:
                logger.info(f'Replica {replica_ip} is ready.')
                return self, True
        except requests.exceptions.RequestException as e:
            logger.info(e)
            logger.info(f'Replica {replica_ip} is not ready.')
            pass
        return self, False


class InfraProvider:
    """Each infra provider manages one service."""

    def __init__(
            self,
            service_name: str,
            readiness_suffix: str,
            initial_delay_seconds: int,
            post_data: Optional[Union[str, Dict[str, Any]]] = None) -> None:
        self.lock = threading.Lock()
        self.next_replica_id: int = 1
        self.service_name: str = service_name
        self.readiness_suffix: str = readiness_suffix
        self.initial_delay_seconds: int = initial_delay_seconds
        self.post_data: Optional[Union[str, Dict[str, Any]]] = post_data
        self.uptime: Optional[float] = None
        logger.info(f'Readiness probe suffix: {self.readiness_suffix}')
        logger.info(f'Initial delay seconds: {self.initial_delay_seconds}')
        logger.info(f'Post data: {self.post_data} ({type(self.post_data)})')

    def get_replica_info(self, verbose: bool) -> List[Dict[str, Any]]:
        # Get replica info for all replicas
        raise NotImplementedError

    def total_replica_num(self, count_failed_replica: bool) -> int:
        # Returns the total number of replicas
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


class SkyPilotInfraProvider(InfraProvider):
    """Infra provider for SkyPilot clusters."""

    def __init__(self, task_yaml_path: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.task_yaml_path: str = task_yaml_path
        self.launch_process_pool: serve_utils.ThreadSafeDict[
            int, subprocess.Popen] = serve_utils.ThreadSafeDict()
        self.down_process_pool: serve_utils.ThreadSafeDict[
            int, subprocess.Popen] = serve_utils.ThreadSafeDict()

        self._start_process_pool_refresher()
        self._start_job_status_fetcher()
        self._start_replica_prober()

    # This process periodically checks all sky.launch and sky.down process
    # on the fly. If any of them finished, it will update the status of
    # the corresponding replica.
    @with_lock
    def _refresh_process_pool(self) -> None:
        for replica_id, p in list(self.launch_process_pool.items()):
            if p.poll() is not None:
                # TODO(tian): Try-catch in process, and have an enum return
                # value to indicate which type of failure happened.
                # Currently we only have user code failure since the
                # retry_until_up flag is set to True, but it will be helpful
                # when we enable user choose whether to retry or not.
                logger.info(
                    f'Launch process for replica {replica_id} finished.')
                del self.launch_process_pool[replica_id]
                info = serve_state.get_replica_info_from_id(
                    self.service_name, replica_id)
                assert info is not None
                if p.returncode != 0:
                    logger.warning(
                        f'Launch process for replica {replica_id} exited '
                        f'abnormally with code {p.returncode}. Terminating...')
                    info.status_property.sky_launch_status = (
                        ProcessStatus.FAILED)
                    self._teardown_replica(replica_id, sync_down_logs=True)
                else:
                    info.status_property.sky_launch_status = (
                        ProcessStatus.SUCCEEDED)
                serve_state.add_or_update_replica(self.service_name, replica_id,
                                                  info)
        for replica_id, p in list(self.down_process_pool.items()):
            if p.poll() is not None:
                logger.info(f'Down process for replica {replica_id} finished.')
                del self.down_process_pool[replica_id]
                info = serve_state.get_replica_info_from_id(
                    self.service_name, replica_id)
                assert info is not None
                if p.returncode != 0:
                    logger.error(
                        f'Down process for replica {replica_id} exited '
                        f'abnormally with code {p.returncode}.')
                    info.status_property.sky_down_status = (
                        ProcessStatus.FAILED)
                else:
                    info.status_property.sky_down_status = (
                        ProcessStatus.SUCCEEDED)
                # Failed replica still count as a replica. In our current
                # design, we want to fail early if user code have any error.
                # This will prevent infinite loop of teardown and
                # re-provision.
                if info.status_property.is_scale_down_succeeded():
                    # This means the cluster is deleted due to
                    # a scale down. Delete the replica info
                    # so it won't count as a replica.
                    logger.info(f'Replica {replica_id} removed from the '
                                'replica table normally.')
                    serve_state.remove_replica(self.service_name, replica_id)
                else:
                    logger.info(f'Termination of replica {replica_id} '
                                'finished. Replica info is kept since some '
                                'failure detected.')
                    serve_state.add_or_update_replica(self.service_name,
                                                      replica_id, info)

    # TODO(tian): Maybe use decorator?
    def _process_pool_refresher(self) -> None:
        while True:
            logger.info('Refreshing process pool.')
            try:
                self._refresh_process_pool()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # process pool refresher running.
                logger.error(f'Error in process pool refresher: {e}')
            for _ in range(_PROCESS_POOL_REFRESH_INTERVAL):
                if self.process_pool_refresher_stop_event.is_set():
                    logger.info('Process pool refresher terminated.')
                    return
                time.sleep(1)

    def _start_process_pool_refresher(self) -> None:
        self.process_pool_refresher_stop_event = threading.Event()
        self.process_pool_refresher_thread = threading.Thread(
            target=self._process_pool_refresher)
        self.process_pool_refresher_thread.start()

    @with_lock
    def _fetch_job_status(self) -> None:
        infos = serve_state.get_replica_infos(self.service_name)
        for info in infos:
            if not info.status_property.should_track_status():
                continue
            # We use backend API to avoid usage collection in the
            # core.job_status.
            backend = backends.CloudVmRayBackend()
            handle = info.handle
            assert handle is not None, info
            # Use None to fetch latest job, which stands for user task job
            job_statuses = backend.get_job_status(handle,
                                                  None,
                                                  stream_logs=False)
            job_status = list(job_statuses.values())[0]
            if job_status in [
                    job_lib.JobStatus.FAILED, job_lib.JobStatus.FAILED_SETUP
            ]:
                info.status_property.user_app_failed = True
                serve_state.add_or_update_replica(self.service_name,
                                                  info.replica_id, info)
                logger.warning(
                    f'User APP for replica {info.replica_id} FAILED. '
                    'Start streaming logs...')
                replica_job_logs_dir = os.path.join(
                    constants.SKY_LOGS_DIRECTORY, 'replica_jobs')
                backend_utils.download_and_stream_latest_job_log(
                    backend,
                    handle,
                    replica_job_logs_dir,
                    log_position_hint='replica cluster',
                    log_finish_hint=f'Replica: {info.replica_id}')
                logger.info('Terminating...')
                self._teardown_replica(info.replica_id, sync_down_logs=True)

    def _job_status_fetcher(self) -> None:
        while True:
            logger.info('Refreshing job status.')
            try:
                self._fetch_job_status()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # job status fetcher running.
                logger.error(f'Error in job status fetcher: {e}')
            for _ in range(_JOB_STATUS_FETCH_INTERVAL):
                if self.job_status_fetcher_stop_event.is_set():
                    logger.info('Job status fetcher terminated.')
                    return
                time.sleep(1)

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

    def get_replica_info(self, verbose: bool) -> List[Dict[str, Any]]:
        return [
            info.to_info_dict(with_handle=verbose)
            for info in serve_state.get_replica_infos(self.service_name)
        ]

    def total_replica_num(self, count_failed_replica: bool) -> int:
        infos = serve_state.get_replica_infos(self.service_name)
        if count_failed_replica:
            return len(infos)
        return len(
            [i for i in infos if i.status != serve_state.ReplicaStatus.FAILED])

    def get_ready_replicas(self) -> Set[str]:
        ready_replicas = set()
        infos = serve_state.get_replica_infos(self.service_name)
        for info in infos:
            if info.status == serve_state.ReplicaStatus.READY:
                assert info.ip is not None
                ready_replicas.add(info.ip)
        return ready_replicas

    def _launch_replica(self, replica_id: int) -> None:
        cluster_name = serve_utils.generate_replica_cluster_name(
            self.service_name, replica_id)
        if replica_id in self.launch_process_pool:
            logger.warning(f'Launch process for replica {replica_id} '
                           'already exists. Skipping.')
            return
        logger.info(f'Creating replica {replica_id}')
        # TODO(tian): We should do usage_lib.messages.usage.set_internal()
        # after we change to python API.
        cmd = ['sky', 'launch', self.task_yaml_path, '-c', cluster_name, '-y']
        cmd.extend(['--detach-setup', '--detach-run', '--retry-until-up'])
        log_file_name = serve_utils.generate_replica_launch_log_file_name(
            self.service_name, replica_id)
        with open(log_file_name, 'w') as f:
            # pylint: disable=consider-using-with
            p = subprocess.Popen(cmd,
                                 stdin=subprocess.DEVNULL,
                                 stdout=f,
                                 stderr=f)
        self.launch_process_pool[replica_id] = p
        info = ReplicaInfo(replica_id, cluster_name)
        serve_state.add_or_update_replica(self.service_name, replica_id, info)

    def scale_up(self, n: int) -> None:
        # Launch n new replicas
        for _ in range(n):
            self._launch_replica(self.next_replica_id)
            self.next_replica_id += 1

    def _teardown_replica(self,
                          replica_id: int,
                          sync_down_logs: bool = True) -> None:
        if replica_id in self.down_process_pool:
            logger.warning(f'Down process for replica {replica_id} already '
                           'exists. Skipping.')
            return

        if sync_down_logs:
            logger.info(f'Syncing down logs for replica {replica_id}...')
            # TODO(tian): Maybe use
            # backend_utils.download_and_stream_latest_job_log here
            code = serve_utils.ServeCodeGen.stream_replica_logs(
                self.service_name,
                replica_id,
                follow=False,
                skip_local_log_file_check=True)
            local_log_file_name = (
                serve_utils.generate_replica_local_log_file_name(
                    self.service_name, replica_id))
            with open(local_log_file_name, 'w') as f:
                try:
                    subprocess.run(code, shell=True, check=True, stdout=f)
                except Exception as e:  # pylint: disable=broad-except
                    # No matter what error happens, we should teardown the
                    # cluster.
                    msg = ('Error in syncing down logs for replica '
                           f'{replica_id}: {e}')
                    logger.error(msg)
                    print(msg, file=f)

        logger.info(f'Deleting replica {replica_id}')
        info = serve_state.get_replica_info_from_id(self.service_name,
                                                    replica_id)
        assert info is not None
        cmd = ['sky', 'down', info.cluster_name, '-y']
        log_file_name = serve_utils.generate_replica_down_log_file_name(
            self.service_name, replica_id)
        with open(log_file_name, 'w') as f:
            # pylint: disable=consider-using-with
            p = subprocess.Popen(cmd,
                                 stdin=subprocess.DEVNULL,
                                 stdout=f,
                                 stderr=f)
        self.down_process_pool[replica_id] = p
        info.status_property.sky_down_status = ProcessStatus.RUNNING
        serve_state.add_or_update_replica(self.service_name, replica_id, info)

    def scale_down(self, n: int) -> None:
        # Terminate n replicas
        # TODO(tian): Policy to choose replica to scale down.
        infos = serve_state.get_replica_infos(self.service_name)
        for i in range(n):
            self._teardown_replica(infos[i].replica_id)

    # TODO(tian): Maybe just kill all threads and cleanup using db record
    def terminate(self) -> Optional[str]:
        logger.info('Terminating infra provider daemon threads...')
        self._terminate_daemon_threads()
        logger.info('Terminating all clusters...')
        for replica_id, p in self.launch_process_pool.items():
            # Use keyboard interrupt here since sky.launch has great
            # handling for it
            # Edge case: sky.launched finished after the
            # process_pool_refresher terminates
            if p.poll() is None:
                assert p.pid is not None
                # Interrupt the launch process and its children. We use SIGINT
                # here since sky.launch has great handling for it.
                _interrupt_process_and_children(p.pid)
                p.wait()
                logger.info(
                    f'Interrupted launch process for replica {replica_id} '
                    'and deleted the cluster.')
                self._teardown_replica(replica_id, sync_down_logs=False)
                info = serve_state.get_replica_info_from_id(
                    self.service_name, replica_id)
                assert info is not None
                # Set to success here for correctly display as shutting down
                info.status_property.sky_launch_status = ProcessStatus.SUCCEEDED
                serve_state.add_or_update_replica(self.service_name, replica_id,
                                                  info)
        msg = []
        infos = serve_state.get_replica_infos(self.service_name)
        # TODO(tian): Move all cleanup to the control process
        for info in infos:
            if info.status in [
                    serve_state.ReplicaStatus.FAILED_CLEANUP,
                    serve_state.ReplicaStatus.UNKNOWN,
            ]:
                msg.append(f'Replica with status {info.status} found. Please '
                           'manually check the cloud console to make sure no '
                           'resource leak.')
            # Skip those already deleted and those are deleting
            if info.status not in [
                    serve_state.ReplicaStatus.FAILED,
                    serve_state.ReplicaStatus.SHUTTING_DOWN
            ]:
                self._teardown_replica(info.replica_id, sync_down_logs=False)
        for replica_id, p in self.down_process_pool.items():
            p.wait()
            logger.info(f'Down process for replica {replica_id} finished.')
            if p.returncode != 0:
                logger.warning(f'Down process for replica {replica_id} exited '
                               f'abnormally with code {p.returncode}.')
                msg.append(
                    f'Down process for replica {replica_id} exited abnormally'
                    f' with code {p.returncode}. Please login to the '
                    'controller and make sure the replica is released.')
            else:
                serve_state.remove_replica(self.service_name, replica_id)
        if not msg:
            return None
        return '\n'.join(msg)

    def _replica_prober(self) -> None:
        while True:
            logger.info('Running replica prober.')
            try:
                self._probe_all_replicas()
                serve_utils.set_service_status_from_replica_info(
                    self.service_name, self.get_replica_info(verbose=True))
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # replica prober running.
                logger.error(f'Error in replica prober: {e}')
            for _ in range(_ENDPOINT_PROBE_INTERVAL):
                if self.replica_prober_stop_event.is_set():
                    logger.info('Replica prober terminated.')
                    return
                time.sleep(1)

    def _start_replica_prober(self) -> None:
        self.replica_prober_stop_event = threading.Event()
        self.replica_prober_thread = threading.Thread(
            target=self._replica_prober)
        self.replica_prober_thread.start()

    @with_lock
    def _probe_all_replicas(self) -> None:
        replica_info = self.get_replica_info(
            verbose=env_options.Options.SHOW_DEBUG_INFO.get())
        logger.info(f'All replica info: {replica_info}')

        probe_futures = []
        replica_to_probe = []
        with futures.ThreadPoolExecutor() as executor:
            infos = serve_state.get_replica_infos(self.service_name)
            for info in infos:
                if not info.status_property.should_track_status():
                    continue
                replica_to_probe.append((info.cluster_name, info.ip))
                probe_futures.append(
                    executor.submit(info.probe, self.readiness_suffix,
                                    self.post_data))
        logger.info(f'Replicas to probe: {replica_to_probe}')

        for future in futures.as_completed(probe_futures):
            future_result: Tuple[ReplicaInfo, bool] = future.result()
            info, probe_succeeded = future_result
            info.status_property.service_ready_now = probe_succeeded
            should_teardown = False
            if probe_succeeded:
                if self.uptime is None:
                    self.uptime = time.time()
                    logger.info(f'Replica {info.replica_id} is the first '
                                'ready replica. Setting uptime to '
                                f'{self.uptime}.')
                    serve_state.set_service_uptime(self.service_name,
                                                   int(self.uptime))
                info.consecutive_failure_times.clear()
                if not info.status_property.service_once_ready:
                    info.status_property.service_once_ready = True
            else:
                current_time = time.time()
                if info.first_not_ready_time is None:
                    info.first_not_ready_time = current_time
                if info.status_property.service_once_ready:
                    info.consecutive_failure_times.append(current_time)
                    consecutive_failure_time = (
                        info.consecutive_failure_times[-1] -
                        info.consecutive_failure_times[0])
                    if (consecutive_failure_time >=
                            _CONSECUTIVE_FAILURE_THRESHOLD_TIMEOUT):
                        logger.info(
                            f'Replica {info.replica_id} is  not ready for too '
                            'long and exceeding consecutive failure '
                            'threshold. Terminating the replica...')
                        should_teardown = True
                    else:
                        logger.info(
                            f'Replica {info.replica_id} is not ready but '
                            'within consecutive failure threshold '
                            f'({consecutive_failure_time}s / '
                            f'{_CONSECUTIVE_FAILURE_THRESHOLD_TIMEOUT}s). '
                            'Skipping.')
                else:
                    current_delay_seconds = (current_time -
                                             info.first_not_ready_time)
                    if current_delay_seconds > self.initial_delay_seconds:
                        logger.info(
                            f'Replica {info.replica_id} is not ready and '
                            'exceeding initial delay seconds. Terminating '
                            'the replica...')
                        should_teardown = True
                    else:
                        current_delay_seconds = int(current_delay_seconds)
                        logger.info(
                            f'Replica {info.replica_id} is not ready but within'
                            f' initial delay seconds ({current_delay_seconds}s '
                            f'/ {self.initial_delay_seconds}s). Skipping.')
            serve_state.add_or_update_replica(self.service_name,
                                              info.replica_id, info)
            if should_teardown:
                self._teardown_replica(info.replica_id)
