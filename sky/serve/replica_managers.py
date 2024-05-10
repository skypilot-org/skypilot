"""ReplicaManager: handles the creation and deletion of endpoint replicas."""
import dataclasses
import enum
import functools
import multiprocessing
from multiprocessing import pool as mp_pool
import os
import threading
import time
import traceback
import typing
from typing import Any, Dict, List, Optional, Tuple

import colorama
import psutil
import requests

import sky
from sky import backends
from sky import core
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import status_lib
from sky.backends import backend_utils
from sky.serve import constants as serve_constants
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.serve import service
from sky.skylet import constants
from sky.skylet import job_lib
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import env_options
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.serve import service_spec

logger = sky_logging.init_logger(__name__)

_JOB_STATUS_FETCH_INTERVAL = 30
_PROCESS_POOL_REFRESH_INTERVAL = 20
# TODO(tian): Maybe let user determine this threshold
_CONSECUTIVE_FAILURE_THRESHOLD_TIMEOUT = 180
_RETRY_INIT_GAP_SECONDS = 60
_DEFAULT_DRAIN_SECONDS = 120

# Since sky.launch is very resource demanding, we limit the number of
# concurrent sky.launch process to avoid overloading the machine.
_MAX_NUM_LAUNCH = psutil.cpu_count() * 2


# TODO(tian): Combine this with
# sky/spot/recovery_strategy.py::StrategyExecutor::launch
def launch_cluster(replica_id: int,
                   task_yaml_path: str,
                   cluster_name: str,
                   resources_override: Optional[Dict[str, Any]] = None,
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
        config = common_utils.read_yaml(os.path.expanduser(task_yaml_path))
        task = sky.Task.from_yaml_config(config)
        if resources_override is not None:
            resources = task.resources
            overrided_resources = [
                r.copy(**resources_override) for r in resources
            ]
            task.set_resources(type(resources)(overrided_resources))
        task.update_envs({serve_constants.REPLICA_ID_ENV_VAR: str(replica_id)})

        logger.info(f'Launching replica (id: {replica_id}) cluster '
                    f'{cluster_name} with resources: {task.resources}')
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


# TODO(tian): Combine this with
# sky/spot/recovery_strategy.py::terminate_cluster
def terminate_cluster(cluster_name: str,
                      replica_drain_delay_seconds: int = 0,
                      max_retry: int = 3) -> None:
    """Terminate the sky serve replica cluster."""
    time.sleep(replica_drain_delay_seconds)
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


def _get_resources_ports(task_yaml: str) -> str:
    """Get the resources ports used by the task."""
    task = sky.Task.from_yaml(task_yaml)
    # Already checked all ports are the same in sky.serve.core.up
    assert len(task.resources) >= 1, task
    task_resources = list(task.resources)[0]
    # Already checked the resources have and only have one port
    # before upload the task yaml.
    return task_resources.ports[0]


def _should_use_spot(task_yaml: str,
                     resource_override: Optional[Dict[str, Any]]) -> bool:
    """Get whether the task should use spot."""
    if resource_override is not None:
        use_spot_override = resource_override.get('use_spot')
        if use_spot_override is not None:
            assert isinstance(use_spot_override, bool)
            return use_spot_override
    task = sky.Task.from_yaml(task_yaml)
    spot_use_resources = [
        resources for resources in task.resources if resources.use_spot
    ]
    # Either resources all use spot or none use spot.
    assert len(spot_use_resources) in [0, len(task.resources)]
    return len(spot_use_resources) == len(task.resources)


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

    # The process is interrupted
    INTERRUPTED = 'INTERRUPTED'

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
    # None means sky.launch is not called yet.
    sky_launch_status: Optional[ProcessStatus] = None
    user_app_failed: bool = False
    service_ready_now: bool = False
    # None means readiness probe is not succeeded yet;
    # -1 means the initial delay seconds is exceeded.
    first_ready_time: Optional[float] = None
    # None means sky.down is not called yet.
    sky_down_status: Optional[ProcessStatus] = None
    # Whether the termination is caused by autoscaler's decision
    is_scale_down: bool = False
    # The replica's spot instance was preempted.
    preempted: bool = False

    def remove_terminated_replica(self) -> bool:
        """Whether to remove the replica record from the replica table.

        If not, the replica will stay in the replica table permanently to
        notify the user that something is wrong with the user code / setup.
        """
        return self.is_scale_down

    def unrecoverable_failure(self) -> bool:
        """Whether the replica fails and cannot be recovered.

        Autoscaler should stop scaling if any of the replica has unrecoverable
        failure, e.g., the user app fails before the service endpoint being
        ready for the current version.
        """
        replica_status = self.to_replica_status()
        logger.info(
            'Check replica unrecorverable: first_ready_time '
            f'{self.first_ready_time}, user_app_failed {self.user_app_failed}, '
            f'status {replica_status}')
        if replica_status not in serve_state.ReplicaStatus.terminal_statuses():
            return False
        if self.first_ready_time is not None:
            if self.first_ready_time >= 0:
                # If the service is ever up, we assume there is no bug in the
                # user code and the scale down is successful, thus enabling the
                # controller to remove the replica from the replica table and
                # auto restart the replica.
                # For replica with a failed sky.launch, it is likely due to some
                # misconfigured resources, so we don't want to auto restart it.
                # For replica with a failed sky.down, we cannot restart it since
                # otherwise we will have a resource leak.
                return False
            else:
                # If the initial delay exceeded, it is likely the service is not
                # recoverable.
                return True
        if self.user_app_failed:
            return True
        # TODO(zhwu): launch failures not related to resource unavailability
        # should be considered as unrecoverable failure. (refer to
        # `spot.recovery_strategy.StrategyExecutor::_launch`)
        return False

    def should_track_service_status(self) -> bool:
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
            return False
        return True

    def to_replica_status(self) -> serve_state.ReplicaStatus:
        """Convert status property to human-readable replica status."""
        if self.sky_launch_status is None:
            # Pending to launch
            return serve_state.ReplicaStatus.PENDING
        if self.sky_launch_status == ProcessStatus.RUNNING:
            if self.sky_down_status == ProcessStatus.FAILED:
                return serve_state.ReplicaStatus.FAILED_CLEANUP
            if self.sky_down_status == ProcessStatus.SUCCEEDED:
                # This indicate it is a scale_down with correct teardown.
                # Should have been cleaned from the replica table.
                return serve_state.ReplicaStatus.UNKNOWN
            # Still launching
            return serve_state.ReplicaStatus.PROVISIONING
        if self.sky_launch_status == ProcessStatus.INTERRUPTED:
            # sky.down is running and a scale down interrupted sky.launch
            return serve_state.ReplicaStatus.SHUTTING_DOWN
        if self.sky_down_status is not None:
            if self.preempted:
                # Replica (spot) is preempted
                return serve_state.ReplicaStatus.PREEMPTED
            if self.sky_down_status == ProcessStatus.RUNNING:
                # sky.down is running
                return serve_state.ReplicaStatus.SHUTTING_DOWN
            if self.sky_launch_status == ProcessStatus.INTERRUPTED:
                return serve_state.ReplicaStatus.SHUTTING_DOWN
            if self.sky_down_status == ProcessStatus.FAILED:
                # sky.down failed
                return serve_state.ReplicaStatus.FAILED_CLEANUP
            if self.user_app_failed:
                # Failed on user setup/run
                return serve_state.ReplicaStatus.FAILED
            if self.sky_launch_status == ProcessStatus.FAILED:
                # sky.launch failed
                return serve_state.ReplicaStatus.FAILED_PROVISION
            if self.first_ready_time is None:
                # readiness probe is not executed yet, but a scale down is
                # triggered.
                return serve_state.ReplicaStatus.SHUTTING_DOWN
            if self.first_ready_time == -1:
                # initial delay seconds exceeded
                return serve_state.ReplicaStatus.FAILED_INITIAL_DELAY
            if not self.service_ready_now:
                # Max continuous failure exceeded
                return serve_state.ReplicaStatus.FAILED_PROBING
            # This indicate it is a scale_down with correct teardown.
            # Should have been cleaned from the replica table.
            return serve_state.ReplicaStatus.UNKNOWN
        if self.sky_launch_status == ProcessStatus.FAILED:
            # sky.launch failed
            # The down process has not been started if it reaches here,
            # due to the `if self.sky_down_status is not None`` check above.
            # However, it should have been started by _refresh_process_pool.
            # If not started, this means some bug prevent sky.down from
            # executing. It is also a potential resource leak, so we mark
            # it as FAILED_CLEANUP.
            return serve_state.ReplicaStatus.FAILED_CLEANUP
        if self.user_app_failed:
            # Failed on user setup/run
            # Same as above, the down process should have been started.
            return serve_state.ReplicaStatus.FAILED_CLEANUP
        if self.service_ready_now:
            # Service is ready
            return serve_state.ReplicaStatus.READY
        if self.first_ready_time is not None and self.first_ready_time >= 0.0:
            # Service was ready before but not now
            return serve_state.ReplicaStatus.NOT_READY
        else:
            # No readiness probe passed and sky.launch finished
            return serve_state.ReplicaStatus.STARTING


class ReplicaInfo:
    """Replica info for each replica."""

    _VERSION = 0

    def __init__(self, replica_id: int, cluster_name: str, replica_port: str,
                 is_spot: bool, version: int) -> None:
        self._version = self._VERSION
        self.replica_id: int = replica_id
        self.cluster_name: str = cluster_name
        self.version: int = version
        self.replica_port: str = replica_port
        self.first_not_ready_time: Optional[float] = None
        self.consecutive_failure_times: List[float] = []
        self.status_property: ReplicaStatusProperty = ReplicaStatusProperty()
        self.is_spot: bool = is_spot

    def handle(
        self,
        cluster_record: Optional[Dict[str, Any]] = None
    ) -> Optional[backends.CloudVmRayResourceHandle]:
        """Get the handle of the cluster.

        Args:
            cluster_record: The cluster record in the cluster table. If not
                provided, will fetch the cluster record from the cluster table
                based on the cluster name.
        """
        if cluster_record is None:
            cluster_record = global_user_state.get_cluster_from_name(
                self.cluster_name)
        if cluster_record is None:
            return None
        handle = cluster_record['handle']
        assert isinstance(handle, backends.CloudVmRayResourceHandle)
        return handle

    @property
    def is_terminal(self) -> bool:
        return self.status in serve_state.ReplicaStatus.terminal_statuses()

    @property
    def is_ready(self) -> bool:
        return self.status == serve_state.ReplicaStatus.READY

    @property
    def url(self) -> Optional[str]:
        handle = self.handle()
        if handle is None:
            return None
        replica_port_int = int(self.replica_port)
        try:
            endpoint_dict = core.endpoints(handle.cluster_name,
                                           replica_port_int)
        except exceptions.ClusterNotUpError:
            return None
        endpoint = endpoint_dict.get(replica_port_int, None)
        if not endpoint:
            return None
        assert isinstance(endpoint, str), endpoint
        # If replica doesn't start with http or https, add http://
        if not endpoint.startswith('http'):
            endpoint = 'http://' + endpoint
        return endpoint

    @property
    def status(self) -> serve_state.ReplicaStatus:
        replica_status = self.status_property.to_replica_status()
        if replica_status == serve_state.ReplicaStatus.UNKNOWN:
            logger.error('Detecting UNKNOWN replica status for '
                         f'replica {self.replica_id}.')
        return replica_status

    def to_info_dict(self, with_handle: bool) -> Dict[str, Any]:
        cluster_record = global_user_state.get_cluster_from_name(
            self.cluster_name)
        info_dict = {
            'replica_id': self.replica_id,
            'name': self.cluster_name,
            'status': self.status,
            'version': self.version,
            'endpoint': self.url,
            'is_spot': self.is_spot,
            'launched_at': (cluster_record['launched_at']
                            if cluster_record is not None else None),
        }
        if with_handle:
            info_dict['handle'] = self.handle(cluster_record)
        return info_dict

    def __repr__(self) -> str:
        info_dict = self.to_info_dict(
            with_handle=env_options.Options.SHOW_DEBUG_INFO.get())
        handle_str = ''
        if 'handle' in info_dict:
            handle_str = f', handle={info_dict["handle"]}'
        info = (f'ReplicaInfo(replica_id={self.replica_id}, '
                f'cluster_name={self.cluster_name}, '
                f'version={self.version}, '
                f'replica_port={self.replica_port}, '
                f'is_spot={self.is_spot}, '
                f'status={self.status}, '
                f'launched_at={info_dict["launched_at"]}{handle_str})')
        return info

    def probe(
        self,
        readiness_path: str,
        post_data: Optional[Dict[str, Any]],
    ) -> Tuple['ReplicaInfo', bool, float]:
        """Probe the readiness of the replica.

        Returns:
            Tuple of (self, is_ready, probe_time).
        """
        replica_identity = f'replica {self.replica_id} with url {self.url}'
        # TODO(tian): This requiring the clock on each replica to be aligned,
        # which may not be true when the GCP VMs have run for a long time. We
        # should have a better way to do this. See #2539 for more information.
        probe_time = time.time()
        try:
            msg = ''
            # TODO(tian): Support HTTPS in the future.
            url = self.url
            if url is None:
                logger.info(f'Error when probing {replica_identity}: '
                            'Cannot get the endpoint.')
                return self, False, probe_time
            readiness_path = (f'{url}{readiness_path}')
            logger.info(f'Probing {replica_identity} with {readiness_path}.')
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
                log_method = logger.info
            else:
                msg += f' and response {response.text}.'
                msg = f'{colorama.Fore.YELLOW}{msg}{colorama.Style.RESET_ALL}'
                log_method = logger.error
            log_method(msg)
            if response.status_code == 200:
                logger.debug(f'{replica_identity.capitalize()} is ready.')
                return self, True, probe_time
        except requests.exceptions.RequestException as e:
            logger.error(
                f'{colorama.Fore.YELLOW}Error when probing {replica_identity}:'
                f' {common_utils.format_exception(e)}.'
                f'{colorama.Style.RESET_ALL}')
        return self, False, probe_time

    def __setstate__(self, state):
        """Set state from pickled state, for backward compatibility."""
        version = state.pop('_version', None)
        # Handle old version(s) here.
        if version is None:
            version = -1

        if version < 0:
            # It will be handled with RequestRateAutoscaler.
            # Treated similar to on-demand instances.
            self.is_spot = False

        self.__dict__.update(state)


class ReplicaManager:
    """Each replica manager monitors one service."""

    def __init__(self, service_name: str,
                 spec: 'service_spec.SkyServiceSpec') -> None:
        self.lock = threading.Lock()
        self._next_replica_id: int = 1
        self._service_name: str = service_name
        self._uptime: Optional[float] = None
        self._update_mode = serve_utils.DEFAULT_UPDATE_MODE
        logger.info(f'Readiness probe path: {spec.readiness_path}\n'
                    f'Initial delay seconds: {spec.initial_delay_seconds}\n'
                    f'Post data: {spec.post_data}')

        # Newest version among the currently provisioned and launched replicas
        self.latest_version: int = serve_constants.INITIAL_VERSION
        # Oldest version among the currently provisioned and launched replicas
        self.least_recent_version: int = serve_constants.INITIAL_VERSION
        serve_state.add_or_update_version(self._service_name,
                                          self.latest_version, spec)

    def scale_up(self,
                 resources_override: Optional[Dict[str, Any]] = None) -> None:
        """Scale up the service by 1 replica with resources_override.
        resources_override is of the same format with resources section
        in skypilot task yaml
        """
        raise NotImplementedError

    def scale_down(self, replica_id: int) -> None:
        """Scale down replica with replica_id."""
        raise NotImplementedError

    def update_version(self, version: int, spec: 'service_spec.SkyServiceSpec',
                       update_mode: serve_utils.UpdateMode) -> None:
        raise NotImplementedError

    def get_active_replica_urls(self) -> List[str]:
        """Get the urls of the active replicas."""
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
        # TODO(tian): Store launch/down pid in the replica table, to make the
        # manager more persistent. Current blocker is that we need to manually
        # poll the Process (by join or is_launch), otherwise, it will never
        # finish and become a zombie process. Probably we could use
        # psutil.Process(p.pid).status() == psutil.STATUS_ZOMBIE to check
        # such cases.
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

    def _launch_replica(
        self,
        replica_id: int,
        resources_override: Optional[Dict[str, Any]] = None,
    ) -> None:
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
            target=ux_utils.RedirectOutputForProcess(
                launch_cluster,
                log_file_name,
            ).run,
            args=(replica_id, self._task_yaml_path, cluster_name,
                  resources_override),
        )
        replica_port = _get_resources_ports(self._task_yaml_path)
        use_spot = _should_use_spot(self._task_yaml_path, resources_override)

        info = ReplicaInfo(replica_id, cluster_name, replica_port, use_spot,
                           self.latest_version)
        serve_state.add_or_update_replica(self._service_name, replica_id, info)
        # Don't start right now; we will start it later in _refresh_process_pool
        # to avoid too many sky.launch running at the same time.
        self._launch_process_pool[replica_id] = p

    def scale_up(self,
                 resources_override: Optional[Dict[str, Any]] = None) -> None:
        self._launch_replica(self._next_replica_id, resources_override)
        self._next_replica_id += 1

    def _terminate_replica(self,
                           replica_id: int,
                           sync_down_logs: bool,
                           replica_drain_delay_seconds: int,
                           is_scale_down: bool = False) -> None:

        if replica_id in self._launch_process_pool:
            info = serve_state.get_replica_info_from_id(self._service_name,
                                                        replica_id)
            assert info is not None
            info.status_property.sky_launch_status = ProcessStatus.INTERRUPTED
            serve_state.add_or_update_replica(self._service_name, replica_id,
                                              info)
            launch_process = self._launch_process_pool[replica_id]
            if launch_process.is_alive():
                assert launch_process.pid is not None
                launch_process.terminate()
                launch_process.join()
            logger.info(f'Interrupted launch process for replica {replica_id} '
                        'and deleted the cluster.')
            del self._launch_process_pool[replica_id]

        if replica_id in self._down_process_pool:
            logger.warning(f'Terminate process for replica {replica_id} '
                           'already exists. Skipping.')
            return

        log_file_name = serve_utils.generate_replica_log_file_name(
            self._service_name, replica_id)

        def _download_and_stream_logs(info: ReplicaInfo):
            launch_log_file_name = (
                serve_utils.generate_replica_launch_log_file_name(
                    self._service_name, replica_id))
            # Write launch log to replica log file
            with open(log_file_name, 'w',
                      encoding='utf-8') as replica_log_file, open(
                          launch_log_file_name, 'r',
                          encoding='utf-8') as launch_file:
                replica_log_file.write(launch_file.read())
            os.remove(launch_log_file_name)

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
            job_log_file_name = (
                controller_utils.download_and_stream_latest_job_log(
                    backend, handle, replica_job_logs_dir))
            if job_log_file_name is not None:
                logger.info(f'\n== End of logs (Replica: {replica_id}) ==')
                with open(log_file_name, 'a',
                          encoding='utf-8') as replica_log_file, open(
                              job_log_file_name, 'r',
                              encoding='utf-8') as job_file:
                    replica_log_file.write(job_file.read())
            else:
                with open(log_file_name, 'a',
                          encoding='utf-8') as replica_log_file:
                    replica_log_file.write(
                        f'Failed to sync down job logs from replica'
                        f' {replica_id}.\n')

        logger.info(f'Terminating replica {replica_id}...')
        info = serve_state.get_replica_info_from_id(self._service_name,
                                                    replica_id)
        assert info is not None

        if sync_down_logs:
            _download_and_stream_logs(info)

        logger.info(f'preempted: {info.status_property.preempted}, '
                    f'replica_id: {replica_id}')
        p = multiprocessing.Process(
            target=ux_utils.RedirectOutputForProcess(terminate_cluster,
                                                     log_file_name, 'a').run,
            args=(info.cluster_name, replica_drain_delay_seconds),
        )
        info.status_property.sky_down_status = ProcessStatus.RUNNING
        info.status_property.is_scale_down = is_scale_down
        serve_state.add_or_update_replica(self._service_name, replica_id, info)
        p.start()
        self._down_process_pool[replica_id] = p

    def scale_down(self, replica_id: int) -> None:
        self._terminate_replica(
            replica_id,
            sync_down_logs=False,
            replica_drain_delay_seconds=_DEFAULT_DRAIN_SECONDS,
            is_scale_down=True)

    def _handle_preemption(self, info: ReplicaInfo) -> bool:
        """Handle preemption of the replica if any error happened.

        Returns:
            bool: Whether the replica is preempted.
        """
        if not info.is_spot:
            return False

        # Get cluster handle first for zone information. The following
        # backend_utils.refresh_cluster_status_handle might delete the
        # cluster record from the cluster table.
        handle = global_user_state.get_handle_from_cluster_name(
            info.cluster_name)
        if handle is None:
            logger.error(f'Cannot find cluster {info.cluster_name} for '
                         f'replica {info.replica_id} in the cluster table. '
                         'Skipping preemption handling.')
            return False
        assert isinstance(handle, backends.CloudVmRayResourceHandle)
        # Pull the actual cluster status from the cloud provider to
        # determine whether the cluster is preempted.
        cluster_status, _ = backend_utils.refresh_cluster_status_handle(
            info.cluster_name,
            force_refresh_statuses=set(status_lib.ClusterStatus))

        if cluster_status == status_lib.ClusterStatus.UP:
            return False
        # The cluster is (partially) preempted. It can be down, INIT or STOPPED,
        # based on the interruption behavior of the cloud.
        cluster_status_str = ('' if cluster_status is None else
                              f' (status: {cluster_status.value})')
        logger.info(
            f'Replica {info.replica_id} is preempted{cluster_status_str}.')
        info.status_property.preempted = True
        serve_state.add_or_update_replica(self._service_name, info.replica_id,
                                          info)
        self._terminate_replica(info.replica_id,
                                sync_down_logs=False,
                                replica_drain_delay_seconds=0,
                                is_scale_down=True)
        return True

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
                info = serve_state.get_replica_info_from_id(
                    self._service_name, replica_id)
                assert info is not None, replica_id
                error_in_sky_launch = False
                if info.status == serve_state.ReplicaStatus.PENDING:
                    # sky.launch not started yet
                    if (serve_state.total_number_provisioning_replicas() <
                            _MAX_NUM_LAUNCH):
                        p.start()
                        info.status_property.sky_launch_status = (
                            ProcessStatus.RUNNING)
                else:
                    # sky.launch finished
                    # TODO(tian): Try-catch in process, and have an enum return
                    # value to indicate which type of failure happened.
                    # Currently we only have user code failure since the
                    # retry_until_up flag is set to True, but it will be helpful
                    # when we enable user choose whether to retry or not.
                    logger.info(
                        f'Launch process for replica {replica_id} finished.')
                    del self._launch_process_pool[replica_id]
                    if p.exitcode != 0:
                        logger.warning(
                            f'Launch process for replica {replica_id} '
                            f'exited abnormally with code {p.exitcode}.'
                            ' Terminating...')
                        info.status_property.sky_launch_status = (
                            ProcessStatus.FAILED)
                        error_in_sky_launch = True
                    else:
                        info.status_property.sky_launch_status = (
                            ProcessStatus.SUCCEEDED)
                serve_state.add_or_update_replica(self._service_name,
                                                  replica_id, info)
                if error_in_sky_launch:
                    # Teardown after update replica info since
                    # _terminate_replica will update the replica info too.
                    self._terminate_replica(replica_id,
                                            sync_down_logs=True,
                                            replica_drain_delay_seconds=0)
        for replica_id, p in list(self._down_process_pool.items()):
            if not p.is_alive():
                logger.info(
                    f'Terminate process for replica {replica_id} finished.')
                del self._down_process_pool[replica_id]
                info = serve_state.get_replica_info_from_id(
                    self._service_name, replica_id)
                assert info is not None, replica_id
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
                removal_reason = None
                if info.status_property.is_scale_down:
                    # This means the cluster is deleted due to an autoscaler
                    # decision or the cluster is recovering from preemption.
                    # Delete the replica info so it won't count as a replica.
                    if info.status_property.preempted:
                        removal_reason = 'for preemption recovery'
                    else:
                        removal_reason = 'normally'
                # Don't keep failed record for version mismatch replicas,
                # since user should fixed the error before update.
                elif info.version != self.latest_version:
                    removal_reason = 'for version outdated'
                else:
                    logger.info(f'Termination of replica {replica_id} '
                                'finished. Replica info is kept since some '
                                'failure detected.')
                    serve_state.add_or_update_replica(self._service_name,
                                                      replica_id, info)
                if removal_reason is not None:
                    serve_state.remove_replica(self._service_name, replica_id)
                    logger.info(f'Replica {replica_id} removed from the '
                                f'replica table {removal_reason}.')

        # Clean old version
        replica_infos = serve_state.get_replica_infos(self._service_name)
        current_least_recent_version = min([
            info.version for info in replica_infos
        ]) if replica_infos else self.least_recent_version
        if self.least_recent_version < current_least_recent_version:
            for version in range(self.least_recent_version,
                                 current_least_recent_version):
                task_yaml = serve_utils.generate_task_yaml_file_name(
                    self._service_name, version)
                # Delete old version metadata.
                serve_state.delete_version(self._service_name, version)
                # Delete storage buckets of older versions.
                service.cleanup_storage(task_yaml)
            # newest version will be cleaned in serve down
            self.least_recent_version = current_least_recent_version

    def _process_pool_refresher(self) -> None:
        """Periodically refresh the launch/down process pool."""
        while True:
            logger.debug('Refreshing process pool.')
            try:
                self._refresh_process_pool()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # process pool refresher running.
                logger.error('Error in process pool refresher: '
                             f'{common_utils.format_exception(e)}')
                with ux_utils.enable_traceback():
                    logger.error(f'  Traceback: {traceback.format_exc()}')
            time.sleep(_PROCESS_POOL_REFRESH_INTERVAL)

    @with_lock
    def _fetch_job_status(self) -> None:
        """Fetch the service job status of all replicas.

        This function will monitor the job status of all replicas
        to make sure the service is running correctly. If any of the
        replicas failed, it will terminate the replica.

        It is still needed even if we already keep probing the replicas,
        since the replica job might launch the API server in the background
        (using &), and the readiness probe will not detect the worker failure.
        """
        infos = serve_state.get_replica_infos(self._service_name)
        for info in infos:
            if not info.status_property.should_track_service_status():
                continue
            # We use backend API to avoid usage collection in the
            # core.job_status.
            backend = backends.CloudVmRayBackend()
            handle = info.handle()
            assert handle is not None, info
            # Use None to fetch latest job, which stands for user task job
            try:
                job_statuses = backend.get_job_status(handle,
                                                      None,
                                                      stream_logs=False)
            except exceptions.CommandError:
                # If the job status fetch failed, it is likely that the
                # cluster is preempted.
                is_preempted = self._handle_preemption(info)
                if is_preempted:
                    continue
                # Re-raise the exception if it is not preempted.
                raise
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
                self._terminate_replica(info.replica_id,
                                        sync_down_logs=True,
                                        replica_drain_delay_seconds=0)

    def _job_status_fetcher(self) -> None:
        """Periodically fetch the service job status of all replicas."""
        while True:
            logger.debug('Refreshing job status.')
            try:
                self._fetch_job_status()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # job status fetcher running.
                logger.error('Error in job status fetcher: '
                             f'{common_utils.format_exception(e)}')
                with ux_utils.enable_traceback():
                    logger.error(f'  Traceback: {traceback.format_exc()}')
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
        with mp_pool.ThreadPool() as pool:
            infos = serve_state.get_replica_infos(self._service_name)
            for info in infos:
                if not info.status_property.should_track_service_status():
                    continue
                replica_to_probe.append(
                    f'replica_{info.replica_id}(url={info.url})')
                probe_futures.append(
                    pool.apply_async(
                        info.probe,
                        (self._get_readiness_path(
                            info.version), self._get_post_data(info.version)),
                    ),)
            logger.info(f'Replicas to probe: {", ".join(replica_to_probe)}')

            # Since futures.as_completed will return futures in the order of
            # completion, we need the info.probe function to return the info
            # object as well, so that we could update the info object in the
            # same order.
            for future in probe_futures:
                future_result: Tuple[ReplicaInfo, bool, float] = future.get()
                info, probe_succeeded, probe_time = future_result
                info.status_property.service_ready_now = probe_succeeded
                should_teardown = False
                if probe_succeeded:
                    if self._uptime is None:
                        self._uptime = probe_time
                        logger.info(
                            f'Replica {info.replica_id} is the first ready '
                            f'replica. Setting uptime to {self._uptime}.')
                        serve_state.set_service_uptime(self._service_name,
                                                       int(self._uptime))
                    info.consecutive_failure_times.clear()
                    if info.status_property.first_ready_time is None:
                        info.status_property.first_ready_time = probe_time
                else:
                    # TODO(tian): This might take a lot of time. Shouldn't
                    # blocking probe to other replicas.
                    is_preempted = self._handle_preemption(info)
                    if is_preempted:
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
                        initial_delay_seconds = self._get_initial_delay_seconds(
                            info.version)
                        current_delay_seconds = (probe_time -
                                                 info.first_not_ready_time)
                        if current_delay_seconds > initial_delay_seconds:
                            logger.info(
                                f'Replica {info.replica_id} is not ready and '
                                'exceeding initial delay seconds. Terminating '
                                'the replica...')
                            should_teardown = True
                            info.status_property.first_ready_time = -1.0
                        else:
                            current_delay_seconds = int(current_delay_seconds)
                            logger.info(f'Replica {info.replica_id} is not '
                                        'ready but within initial delay '
                                        f'seconds ({current_delay_seconds}s '
                                        f'/ {initial_delay_seconds}s). '
                                        'Skipping.')
                serve_state.add_or_update_replica(self._service_name,
                                                  info.replica_id, info)
                if should_teardown:
                    self._terminate_replica(info.replica_id,
                                            sync_down_logs=True,
                                            replica_drain_delay_seconds=0)

    def _replica_prober(self) -> None:
        """Periodically probe replicas."""
        while True:
            logger.debug('Running replica prober.')
            try:
                self._probe_all_replicas()
                replica_infos = serve_state.get_replica_infos(
                    self._service_name)
                # TODO(zhwu): when there are multiple load balancers, we need
                # to make sure the active_versions are the union of all
                # versions of all load balancers.
                serve_utils.set_service_status_and_active_versions_from_replica(
                    self._service_name, replica_infos, self._update_mode)

            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # replica prober running.
                logger.error('Error in replica prober: '
                             f'{common_utils.format_exception(e)}')
                with ux_utils.enable_traceback():
                    logger.error(f'  Traceback: {traceback.format_exc()}')
            # TODO(MaoZiming): Probe cloud for early preemption warning.
            time.sleep(serve_constants.ENDPOINT_PROBE_INTERVAL_SECONDS)

    def get_active_replica_urls(self) -> List[str]:
        """Get the urls of all active replicas."""
        record = serve_state.get_service_from_name(self._service_name)
        assert record is not None, (f'{self._service_name} not found on '
                                    'controller records.')
        ready_replica_urls = []
        active_versions = set(record['active_versions'])
        for info in serve_state.get_replica_infos(self._service_name):
            if (info.status == serve_state.ReplicaStatus.READY and
                    info.version in active_versions):
                assert info.url is not None, info
                ready_replica_urls.append(info.url)
        return ready_replica_urls

    ###########################################
    # SkyServe Update and replica versioning. #
    ###########################################

    def update_version(self, version: int, spec: 'service_spec.SkyServiceSpec',
                       update_mode: serve_utils.UpdateMode) -> None:
        if version <= self.latest_version:
            logger.error(f'Invalid version: {version}, '
                         f'latest version: {self.latest_version}')
            return
        task_yaml_path = serve_utils.generate_task_yaml_file_name(
            self._service_name, version)
        serve_state.add_or_update_version(self._service_name, version, spec)
        self.latest_version = version
        self._task_yaml_path = task_yaml_path
        self._update_mode = update_mode

        # Reuse all replicas that have the same config as the new version
        # (except for the `service` field) by directly setting the version to be
        # the latest version. This can significantly improve the speed
        # for updating an existing service with only config changes to the
        # service specs, e.g. scale down the service.
        new_config = common_utils.read_yaml(os.path.expanduser(task_yaml_path))
        # Always create new replicas and scale down old ones when file_mounts
        # are not empty.
        if new_config.get('file_mounts', None) != {}:
            return
        for key in ['service']:
            new_config.pop(key)
        replica_infos = serve_state.get_replica_infos(self._service_name)
        for info in replica_infos:
            if info.version < version and not info.is_terminal:
                # Assume user does not change the yaml file on the controller.
                old_task_yaml_path = serve_utils.generate_task_yaml_file_name(
                    self._service_name, info.version)
                old_config = common_utils.read_yaml(
                    os.path.expanduser(old_task_yaml_path))
                for key in ['service']:
                    old_config.pop(key)
                # Bump replica version if all fields except for service are
                # the same. File mounts should both be empty, as update always
                # create new buckets if they are not empty.
                if (old_config == new_config and
                        old_config.get('file_mounts', None) == {}):
                    logger.info(
                        f'Updating replica {info.replica_id} to version '
                        f'{version}. Replica {info.replica_id}\'s config '
                        f'{old_config} is the same as '
                        f'latest version\'s {new_config}.')
                    info.version = version
                    serve_state.add_or_update_replica(self._service_name,
                                                      info.replica_id, info)

    def _get_version_spec(self, version: int) -> 'service_spec.SkyServiceSpec':
        spec = serve_state.get_spec(self._service_name, version)
        if spec is None:
            raise ValueError(f'Version {version} not found.')
        return spec

    def _get_readiness_path(self, version: int) -> str:
        return self._get_version_spec(version).readiness_path

    def _get_post_data(self, version: int) -> Optional[Dict[str, Any]]:
        return self._get_version_spec(version).post_data

    def _get_initial_delay_seconds(self, version: int) -> int:
        return self._get_version_spec(version).initial_delay_seconds
