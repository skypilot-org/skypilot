"""ReplicaManager: handles the creation and deletion of endpoint replicas."""
from concurrent import futures
import dataclasses
import enum
import functools
import multiprocessing
import os
import threading
import time
import traceback
import typing
from typing import Any, Dict, List, Optional, Tuple

import requests

import sky
from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import status_lib
from sky.serve import constants as serve_constants
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import constants
from sky.skylet import job_lib
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import controller_utils

if typing.TYPE_CHECKING:
    from sky.serve import service_spec

logger = sky_logging.init_logger(__name__)

_JOB_STATUS_FETCH_INTERVAL = 30
_PROCESS_POOL_REFRESH_INTERVAL = 20
# TODO(tian): Maybe let user determine this threshold
_CONSECUTIVE_FAILURE_THRESHOLD_TIMEOUT = 180
_RETRY_INIT_GAP_SECONDS = 60


def launch_cluster(task_yaml_path: str,
                   cluster_name: str,
                   max_retry: int = 3) -> None:
    """Launch a sky serve replica cluster.

    This function will not wait for the job starts running. It will return
    immediately after the job is submitted.

    Raises:
        RuntimeError: If failed to launch the cluster after max_retry retries,
            or some error happened before provisioning and will happen again
            if retry.
    """
    try:
        task = sky.Task.from_yaml(task_yaml_path)
    except Exception as e:  # pylint: disable=broad-except
        logger.error('Failed to construct task object from yaml file with '
                     f'error {common_utils.format_exception(e)}')
        raise RuntimeError(
            f'Failed to launch the sky serve replica cluster {cluster_name} '
            'due to failing to initialize sky.Task from yaml file.') from e
    retry_cnt = 0
    backoff = common_utils.Backoff(_RETRY_INIT_GAP_SECONDS)
    while True:
        retry_cnt += 1
        try:
            usage_lib.messages.usage.set_internal()
            sky.launch(task,
                       cluster_name,
                       detach_setup=True,
                       detach_run=True,
                       retry_until_up=True,
                       _is_launched_by_sky_serve_controller=True)
            logger.info(f'Replica cluster {cluster_name} launched.')
        except (exceptions.InvalidClusterNameError,
                exceptions.NoCloudAccessError,
                exceptions.ResourcesMismatchError) as e:
            logger.error('Failure happened before provisioning. '
                         f'{common_utils.format_exception(e)}')
            raise RuntimeError('Failed to launch the sky serve replica '
                               f'cluster {cluster_name}.') from e
        except exceptions.ResourcesUnavailableError as e:
            if not any(
                    isinstance(err, exceptions.ResourcesUnavailableError)
                    for err in e.failover_history):
                raise RuntimeError('Failed to launch the sky serve replica '
                                   f'cluster {cluster_name}.') from e
            logger.info('Failed to launch the sky serve replica cluster with '
                        f'error: {common_utils.format_exception(e)})')
        except Exception as e:  # pylint: disable=broad-except
            logger.info('Failed to launch the sky serve replica cluster with '
                        f'error: {common_utils.format_exception(e)})')
            with ux_utils.enable_traceback():
                logger.info(f'  Traceback: {traceback.format_exc()}')
        else:  # No exception, the launch succeeds.
            return

        terminate_cluster(cluster_name)
        if retry_cnt >= max_retry:
            raise RuntimeError('Failed to launch the sky serve replica cluster '
                               f'{cluster_name} after {max_retry} retries.')
        gap_seconds = backoff.current_backoff()
        logger.info('Retrying to launch the sky serve replica cluster '
                    f'in {gap_seconds:.1f} seconds.')
        time.sleep(gap_seconds)


def terminate_cluster(cluster_name: str, max_retry: int = 3) -> None:
    """Terminate the sky serve replica cluster."""
    retry_cnt = 0
    backoff = common_utils.Backoff()
    while True:
        retry_cnt += 1
        try:
            usage_lib.messages.usage.set_internal()
            sky.down(cluster_name)
            return
        except ValueError:
            # The cluster is already terminated.
            logger.info(
                f'Replica cluster {cluster_name} is already terminated.')
            return
        except Exception as e:  # pylint: disable=broad-except
            if retry_cnt >= max_retry:
                raise RuntimeError('Failed to terminate the sky serve replica '
                                   f'cluster {cluster_name}.') from e
            gap_seconds = backoff.current_backoff()
            logger.error(
                'Failed to terminate the sky serve replica cluster '
                f'{cluster_name}. Retrying after {gap_seconds} seconds.'
                f'Details: {common_utils.format_exception(e)}')
            logger.error(f'  Traceback: {traceback.format_exc()}')
            time.sleep(gap_seconds)


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
    """Some properties that determine replica status.

    Attributes:
        sky_launch_status: Process status of sky.launch.
        user_app_failed: Whether the service job failed.
        service_ready_now: Latest readiness probe result.
        first_ready_time: The first time the service is ready.
        sky_down_status: Process status of sky.down.
    """
    # Initial value is RUNNING since each `ReplicaInfo` is created
    # when `sky.launch` is called.
    sky_launch_status: ProcessStatus = ProcessStatus.RUNNING
    user_app_failed: bool = False
    service_ready_now: bool = False
    # None means readiness probe is not passed yet.
    first_ready_time: Optional[float] = None
    # None means sky.down is not called yet.
    sky_down_status: Optional[ProcessStatus] = None
    # The replica's spot instance was preempted.
    preempted: bool = False

    def is_scale_down_succeeded(self, initial_delay_seconds: int,
                                auto_restart: bool) -> bool:
        """Whether to remove the replica record from the replica table.

        If not, the replica will stay in the replica table permanently to
        notify the user that something is wrong with the user code / setup.
        """
        if self.sky_launch_status != ProcessStatus.SUCCEEDED:
            return False
        if self.sky_down_status != ProcessStatus.SUCCEEDED:
            return False
        if (auto_restart and self.first_ready_time is not None and
                time.time() - self.first_ready_time > initial_delay_seconds):
            # If the service is up for more than `initial_delay_seconds`,
            # we assume there is no bug in the user code and the scale down
            # is successful, thus enabling the controller to remove the
            # replica from the replica table and auto restart the replica.
            # Here we assume that initial_delay_seconds is larger than
            # consecutive_failure_threshold_seconds, so if a replica is not
            # teardown for initial_delay_seconds, it is safe to assume that
            # it is UP for initial_delay_seconds.
            # For replica with a failed sky.launch, it is likely due to some
            # misconfigured resources, so we don't want to auto restart it.
            # For replica with a failed sky.down, we cannot restart it since
            # otherwise we will have a resource leak.
            return True
        if self.user_app_failed:
            return False
        if self.preempted:
            return True
        if not self.service_ready_now:
            return False
        return self.first_ready_time is not None

    def should_track_status(self) -> bool:
        """Should we track the status of the replica.

        This includes:
            (1) Job status;
            (2) Readiness probe.
        """
        if self.sky_launch_status != ProcessStatus.SUCCEEDED:
            return False
        if self.sky_down_status is not None:
            return False
        if self.user_app_failed:
            return False
        if self.preempted:
            return True
        return True

    def to_replica_status(self) -> serve_state.ReplicaStatus:
        """Convert status property to human-readable replica status."""
        if self.sky_launch_status == ProcessStatus.RUNNING:
            # Still launching
            return serve_state.ReplicaStatus.PROVISIONING
        if self.sky_down_status is not None:
            if self.preempted:
                # Replica (spot) is preempted
                return serve_state.ReplicaStatus.PREEMPTED
            if self.sky_down_status == ProcessStatus.RUNNING:
                # sky.down is running
                return serve_state.ReplicaStatus.SHUTTING_DOWN
            if self.sky_down_status == ProcessStatus.FAILED:
                # sky.down failed
                return serve_state.ReplicaStatus.FAILED_CLEANUP
            if self.user_app_failed:
                # Failed on user setup/run
                return serve_state.ReplicaStatus.FAILED
            if self.first_ready_time is None:
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
        if self.first_ready_time is not None:
            # Service was ready before but not now
            return serve_state.ReplicaStatus.NOT_READY
        else:
            # No readiness probe passed and sky.launch finished
            return serve_state.ReplicaStatus.STARTING


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
            logger.error('Detecting UNKNOWN replica status for '
                         f'replica {self.replica_id}.')
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
        self,
        readiness_route: str,
        post_data: Optional[Dict[str, Any]],
    ) -> Tuple['ReplicaInfo', bool, float]:
        """Probe the readiness of the replica.

        Returns:
            Tuple of (self, is_ready, probe_time).
        """
        replica_identity = f'replica {self.replica_id} with ip {self.ip}'
        probe_time = time.time()
        try:
            msg = ''
            # TODO(tian): Support HTTPS in the future.
            readiness_path = f'http://{self.ip}{readiness_route}'
            if post_data is not None:
                msg += 'POST'
                response = requests.post(
                    readiness_path,
                    json=post_data,
                    timeout=serve_constants.READINESS_PROBE_TIMEOUT_SECONDS)
            else:
                msg += 'GET'
                response = requests.get(
                    readiness_path,
                    timeout=serve_constants.READINESS_PROBE_TIMEOUT_SECONDS)
            msg += (f' request to {replica_identity} returned status '
                    f'code {response.status_code}')
            if response.status_code == 200:
                msg += '.'
            else:
                msg += f' and response {response.text}.'
            logger.info(msg)
            if response.status_code == 200:
                logger.debug(f'{replica_identity.capitalize()} is ready.')
                return self, True, probe_time
        except requests.exceptions.RequestException as e:
            logger.info(e)
            logger.info(f'{replica_identity.capitalize()} is not ready.')
            pass
        return self, False, probe_time


class ReplicaManager:
    """Each replica manager monitors one service."""

    def __init__(self, service_name: str,
                 spec: 'service_spec.SkyServiceSpec') -> None:
        self.lock = threading.Lock()
        self._next_replica_id: int = 1
        self._service_name: str = service_name
        self._auto_restart = spec.auto_restart
        self._readiness_route: str = spec.readiness_route
        self._initial_delay_seconds: int = spec.initial_delay_seconds
        self._post_data: Optional[Dict[str, Any]] = spec.post_data
        self._uptime: Optional[float] = None
        logger.info(f'Readiness probe suffix: {self._readiness_route}')
        logger.info(f'Initial delay seconds: {self._initial_delay_seconds}')
        logger.info(f'Post data: {self._post_data}')

    def get_ready_replica_ips(self) -> List[str]:
        """Get all ready replica's IP addresses."""
        raise NotImplementedError

    def scale_up(self, n: int) -> None:
        """Scale up the service by n replicas."""
        raise NotImplementedError

    def scale_down(self, replica_ids: List[int]) -> None:
        """Scale down all replicas in replica_ids."""
        raise NotImplementedError


class SkyPilotReplicaManager(ReplicaManager):
    """Replica Manager for SkyPilot clusters.

    It will run three daemon to monitor the status of the replicas:
        (1) _process_pool_refresher: Refresh the launch/down process pool
            to monitor the progress of the launch/down process.
        (2) _job_status_fetcher: Fetch the job status of the service to
            monitor the status of the service jobs.
        (3) _replica_prober: Do readiness probe to the replicas to monitor
            whether it is still responding to requests.
    """

    def __init__(self, service_name: str, spec: 'service_spec.SkyServiceSpec',
                 task_yaml_path: str) -> None:
        super().__init__(service_name, spec)
        self._task_yaml_path = task_yaml_path
        self._launch_process_pool: serve_utils.ThreadSafeDict[
            int, multiprocessing.Process] = serve_utils.ThreadSafeDict()
        self._down_process_pool: serve_utils.ThreadSafeDict[
            int, multiprocessing.Process] = serve_utils.ThreadSafeDict()

        threading.Thread(target=self._process_pool_refresher).start()
        threading.Thread(target=self._job_status_fetcher).start()
        threading.Thread(target=self._replica_prober).start()

    ################################
    # Replica management functions #
    ################################

    def get_ready_replica_ips(self) -> List[str]:
        ready_replicas = []
        infos = serve_state.get_replica_infos(self._service_name)
        for info in infos:
            if info.status == serve_state.ReplicaStatus.READY:
                assert info.ip is not None
                ready_replicas.append(info.ip)
        return ready_replicas

    def _launch_replica(self, replica_id: int) -> None:
        if replica_id in self._launch_process_pool:
            logger.warning(f'Launch process for replica {replica_id} '
                           'already exists. Skipping.')
            return
        logger.info(f'Launching replica {replica_id}...')
        cluster_name = serve_utils.generate_replica_cluster_name(
            self._service_name, replica_id)
        log_file_name = serve_utils.generate_replica_launch_log_file_name(
            self._service_name, replica_id)
        p = multiprocessing.Process(
            target=serve_utils.RedirectOutputTo(
                launch_cluster,
                log_file_name,
            ).run,
            args=(self._task_yaml_path, cluster_name),
        )
        p.start()
        self._launch_process_pool[replica_id] = p
        info = ReplicaInfo(replica_id, cluster_name)
        serve_state.add_or_update_replica(self._service_name, replica_id, info)

    def scale_up(self, n: int) -> None:
        for _ in range(n):
            self._launch_replica(self._next_replica_id)
            self._next_replica_id += 1

    def _terminate_replica(self, replica_id: int, sync_down_logs: bool) -> None:
        if replica_id in self._down_process_pool:
            logger.warning(f'Terminate process for replica {replica_id} '
                           'already exists. Skipping.')
            return

        def _download_and_stream_logs(info: ReplicaInfo):
            launch_log_file_name = (
                serve_utils.generate_replica_launch_log_file_name(
                    self._service_name, replica_id))
            local_log_file_name = (
                serve_utils.generate_replica_local_log_file_name(
                    self._service_name, replica_id))
            # Write launch log to local log file
            with open(local_log_file_name,
                      'w') as local_file, open(launch_log_file_name,
                                               'r') as launch_file:
                local_file.write(launch_file.read())
            logger.info(f'Syncing down logs for replica {replica_id}...')
            backend = backends.CloudVmRayBackend()
            handle = global_user_state.get_handle_from_cluster_name(
                info.cluster_name)
            if handle is None:
                logger.error(f'Cannot find cluster {info.cluster_name} for '
                             f'replica {replica_id} in the cluster table. '
                             'Skipping syncing down job logs.')
                return
            assert isinstance(handle, backends.CloudVmRayResourceHandle)
            replica_job_logs_dir = os.path.join(constants.SKY_LOGS_DIRECTORY,
                                                'replica_jobs')
            log_file = controller_utils.download_and_stream_latest_job_log(
                backend, handle, replica_job_logs_dir)
            if log_file is not None:
                logger.info(f'\n== End of logs (Replica: {replica_id}) ==')
                with open(local_log_file_name,
                          'a') as local_file, open(log_file, 'r') as job_file:
                    local_file.write(job_file.read())

        logger.info(f'Terminating replica {replica_id}...')
        info = serve_state.get_replica_info_from_id(self._service_name,
                                                    replica_id)
        assert info is not None

        if sync_down_logs:
            _download_and_stream_logs(info)

        logger.info(f'preempted: {info.status_property.preempted}, '
                    f'replica_id: {replica_id}')
        log_file_name = serve_utils.generate_replica_down_log_file_name(
            self._service_name, replica_id)
        p = multiprocessing.Process(
            target=serve_utils.RedirectOutputTo(
                terminate_cluster,
                log_file_name,
            ).run,
            args=(info.cluster_name,),
        )
        p.start()
        self._down_process_pool[replica_id] = p
        info.status_property.sky_down_status = ProcessStatus.RUNNING
        serve_state.add_or_update_replica(self._service_name, replica_id, info)

    def scale_down(self, replica_ids: List[int]) -> None:
        for replica_id in replica_ids:
            self._terminate_replica(replica_id, sync_down_logs=False)

    def _recover_from_preemption(self, replica_id: int) -> None:
        logger.info(f'Beginning recovery for preempted replica {replica_id}.')
        # TODO(MaoZiming): Support spot recovery policies
        info = serve_state.get_replica_info_from_id(self._service_name,
                                                    replica_id)
        assert info is not None
        info.status_property.preempted = True
        serve_state.add_or_update_replica(self._service_name, replica_id, info)
        self._terminate_replica(replica_id, sync_down_logs=False)

    #################################
    # ReplicaManager Daemon Threads #
    #################################

    @with_lock
    def _refresh_process_pool(self) -> None:
        """Refresh the launch/down process pool.

        This function will checks all sky.launch and sky.down process on
        the fly. If any of them finished, it will update the status of the
        corresponding replica.
        """
        for replica_id, p in list(self._launch_process_pool.items()):
            if not p.is_alive():
                # TODO(tian): Try-catch in process, and have an enum return
                # value to indicate which type of failure happened.
                # Currently we only have user code failure since the
                # retry_until_up flag is set to True, but it will be helpful
                # when we enable user choose whether to retry or not.
                logger.info(
                    f'Launch process for replica {replica_id} finished.')
                del self._launch_process_pool[replica_id]
                info = serve_state.get_replica_info_from_id(
                    self._service_name, replica_id)
                assert info is not None
                if p.exitcode != 0:
                    logger.warning(
                        f'Launch process for replica {replica_id} exited '
                        f'abnormally with code {p.exitcode}. Terminating...')
                    info.status_property.sky_launch_status = (
                        ProcessStatus.FAILED)
                    self._terminate_replica(replica_id, sync_down_logs=True)
                else:
                    info.status_property.sky_launch_status = (
                        ProcessStatus.SUCCEEDED)
                serve_state.add_or_update_replica(self._service_name,
                                                  replica_id, info)
        for replica_id, p in list(self._down_process_pool.items()):
            if not p.is_alive():
                logger.info(
                    f'Terminate process for replica {replica_id} finished.')
                del self._down_process_pool[replica_id]
                info = serve_state.get_replica_info_from_id(
                    self._service_name, replica_id)
                assert info is not None
                if p.exitcode != 0:
                    logger.error(f'Down process for replica {replica_id} '
                                 f'exited abnormally with code {p.exitcode}.')
                    info.status_property.sky_down_status = (
                        ProcessStatus.FAILED)
                else:
                    info.status_property.sky_down_status = (
                        ProcessStatus.SUCCEEDED)
                # Failed replica still count as a replica. In our current
                # design, we want to fail early if user code have any error.
                # This will prevent infinite loop of teardown and
                # re-provision. However, there is a special case that if the
                # replica is UP for longer than initial_delay_seconds, we
                # assume it is just some random failure and we should restart
                # the replica. Please refer to the implementation of
                # `is_scale_down_succeeded` for more details.
                # TODO(tian): Currently, restart replicas that failed within
                # initial_delay_seconds is not supported. We should add it
                # later when we support `sky serve update`.
                if info.status_property.is_scale_down_succeeded(
                        self._initial_delay_seconds, self._auto_restart):
                    # This means the cluster is deleted due to
                    # a scale down or the cluster is recovering
                    # from preemption. Delete the replica info
                    # so it won't count as a replica.
                    serve_state.remove_replica(self._service_name, replica_id)
                    if info.status_property.preempted:
                        removal_reason = 'for preemption recovery'
                    else:
                        removal_reason = 'normally'
                    logger.info(f'Replica {replica_id} removed from the '
                                f'replica table {removal_reason}.')
                else:
                    logger.info(f'Termination of replica {replica_id} '
                                'finished. Replica info is kept since some '
                                'failure detected.')
                    serve_state.add_or_update_replica(self._service_name,
                                                      replica_id, info)

    def _process_pool_refresher(self) -> None:
        """Periodically refresh the launch/down process pool."""
        while True:
            logger.debug('Refreshing process pool.')
            try:
                self._refresh_process_pool()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # process pool refresher running.
                logger.error(f'Error in process pool refresher: {e}')
            time.sleep(_PROCESS_POOL_REFRESH_INTERVAL)

    @with_lock
    def _fetch_job_status(self) -> None:
        """Fetch the service job status of all replicas.

        This function will monitor the job status of all replicas
        to make sure the service is running correctly. If any of the
        replicas failed, it will terminate the replica.
        """
        infos = serve_state.get_replica_infos(self._service_name)
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
                serve_state.add_or_update_replica(self._service_name,
                                                  info.replica_id, info)
                logger.warning(
                    f'Service job for replica {info.replica_id} FAILED. '
                    'Terminating...')
                self._terminate_replica(info.replica_id, sync_down_logs=True)

    def _job_status_fetcher(self) -> None:
        """Periodically fetch the service job status of all replicas."""
        while True:
            logger.debug('Refreshing job status.')
            try:
                self._fetch_job_status()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # job status fetcher running.
                logger.error(f'Error in job status fetcher: {e}')
            time.sleep(_JOB_STATUS_FETCH_INTERVAL)

    @with_lock
    def _probe_all_replicas(self) -> None:
        """Readiness probe replicas.

        This function will probe all replicas to make sure the service is
        ready. It will keep track of:
            (1) the initial delay for each replica;
            (2) the consecutive failure times.
        The replica will be terminated if any of the thresholds exceeded.
        """
        probe_futures = []
        replica_to_probe = []
        with futures.ThreadPoolExecutor() as executor:
            infos = serve_state.get_replica_infos(self._service_name)
            for info in infos:
                if not info.status_property.should_track_status():
                    continue
                replica_to_probe.append(
                    f'replica_{info.replica_id}(ip={info.ip})')
                probe_futures.append(
                    executor.submit(
                        info.probe,
                        self._readiness_route,
                        self._post_data,
                    ))
        logger.info(f'Replicas to probe: {", ".join(replica_to_probe)}')

        # Since futures.as_completed will return futures in the order of
        # completion, we need the info.probe function to return the info
        # object as well, so that we could update the info object in the
        # same order.
        for future in futures.as_completed(probe_futures):
            future_result: Tuple[ReplicaInfo, bool, float] = future.result()
            info, probe_succeeded, probe_time = future_result
            info.status_property.service_ready_now = probe_succeeded
            should_teardown = False
            if probe_succeeded:
                if self._uptime is None:
                    self._uptime = probe_time
                    logger.info(f'Replica {info.replica_id} is the first ready '
                                f'replica. Setting uptime to {self._uptime}.')
                    serve_state.set_service_uptime(self._service_name,
                                                   int(self._uptime))
                info.consecutive_failure_times.clear()
                if info.status_property.first_ready_time is None:
                    info.status_property.first_ready_time = probe_time
            else:
                handle = info.handle
                if handle is None:
                    logger.error('Cannot find handle for '
                                 f'replica {info.replica_id}.')
                elif handle.launched_resources is None:
                    logger.error('Cannot find launched_resources in handle'
                                 f' for replica {info.replica_id}.')
                elif handle.launched_resources.use_spot:
                    # Pull the actual cluster status
                    # from the cloud provider to
                    # determine whether the cluster is preempted.
                    (cluster_status,
                     _) = backends.backend_utils.refresh_cluster_status_handle(
                         info.cluster_name,
                         force_refresh_statuses=set(status_lib.ClusterStatus))

                    if cluster_status != status_lib.ClusterStatus.UP:
                        # The cluster is (partially) preempted.
                        # It can be down, INIT or STOPPED, based on the
                        # interruption behavior of the cloud.
                        # Spot recovery is needed.
                        cluster_status_str = (
                            '' if cluster_status is None else
                            f' (status: {cluster_status.value})')
                        logger.info(f'Replica {info.replica_id} '
                                    f'is preempted{cluster_status_str}.')
                        self._recover_from_preemption(info.replica_id)

                        continue

                if info.first_not_ready_time is None:
                    info.first_not_ready_time = probe_time
                if info.status_property.first_ready_time is not None:
                    info.consecutive_failure_times.append(probe_time)
                    consecutive_failure_time = (
                        info.consecutive_failure_times[-1] -
                        info.consecutive_failure_times[0])
                    if (consecutive_failure_time >=
                            _CONSECUTIVE_FAILURE_THRESHOLD_TIMEOUT):
                        logger.info(
                            f'Replica {info.replica_id} is not ready for '
                            'too long and exceeding consecutive failure '
                            'threshold. Terminating the replica...')
                        should_teardown = True
                    else:
                        logger.info(
                            f'Replica {info.replica_id} is not ready '
                            'but within consecutive failure threshold '
                            f'({consecutive_failure_time}s / '
                            f'{_CONSECUTIVE_FAILURE_THRESHOLD_TIMEOUT}s). '
                            'Skipping.')
                else:
                    current_delay_seconds = (probe_time -
                                             info.first_not_ready_time)
                    if current_delay_seconds > self._initial_delay_seconds:
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
                            f'/ {self._initial_delay_seconds}s). Skipping.')
            serve_state.add_or_update_replica(self._service_name,
                                              info.replica_id, info)
            if should_teardown:
                self._terminate_replica(info.replica_id, sync_down_logs=True)

    def _replica_prober(self) -> None:
        """Periodically probe replicas."""
        while True:
            logger.debug('Running replica prober.')
            try:
                self._probe_all_replicas()
                replica_statuses = [
                    info.status for info in serve_state.get_replica_infos(
                        self._service_name)
                ]
                serve_utils.set_service_status_from_replica_statuses(
                    self._service_name, replica_statuses)
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # replica prober running.
                logger.error(f'Error in replica prober: {e}')
            time.sleep(serve_constants.ENDPOINT_PROBE_INTERVAL_SECONDS)
