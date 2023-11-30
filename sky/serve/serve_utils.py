"""User interface with the SkyServe."""
import base64
import enum
import os
import pathlib
import pickle
import re
import shlex
import shutil
import threading
import time
import typing
from typing import (Any, Callable, Dict, Generic, Iterator, List, Optional,
                    TextIO, Type, TypeVar)
import uuid

import colorama
import filelock
import psutil

from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import status_lib
from sky.serve import constants
from sky.serve import serve_state
from sky.skylet import job_lib
from sky.utils import common_utils
from sky.utils import log_utils
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import fastapi

SKY_SERVE_CONTROLLER_NAME: str = (
    f'sky-serve-controller-{common_utils.get_user_hash()}')
_SYSTEM_MEMORY_GB = psutil.virtual_memory().total // (1024**3)
NUM_SERVICE_THRESHOLD = _SYSTEM_MEMORY_GB // constants.SERVICES_MEMORY_USAGE_GB

_SKYPILOT_PROVISION_LOG_PATTERN = r'.*tail -n100 -f (.*provision\.log).*'
_SKYPILOT_LOG_PATTERN = r'.*tail -n100 -f (.*\.log).*'
# TODO(tian): Find all existing replica id and print here.
_FAILED_TO_FIND_REPLICA_MSG = (
    f'{colorama.Fore.RED}Failed to find replica '
    '{replica_id}. Please use `sky serve status [SERVICE_NAME]`'
    f' to check all valid replica id.{colorama.Style.RESET_ALL}')
# Max number of replicas to show in `sky serve status` by default.
# If user wants to see all replicas, use `sky serve status --all`.
_REPLICA_TRUNC_NUM = 10


class ServiceComponent(enum.Enum):
    CONTROLLER = 'controller'
    LOAD_BALANCER = 'load_balancer'
    REPLICA = 'replica'


class UserSignal(enum.Enum):
    """User signal to send to controller.

    User can send signal to controller by writing to a file. The controller
    will read the file and handle the signal.
    """
    # Stop the controller, load balancer and all replicas.
    TERMINATE = 'terminate'

    # TODO(tian): Add more signals, such as pause.

    def error_type(self) -> Type[Exception]:
        """Get the error corresponding to the signal."""
        return _SIGNAL_TO_ERROR[self]


_SIGNAL_TO_ERROR = {
    UserSignal.TERMINATE: exceptions.ServeUserTerminatedError,
}

KeyType = TypeVar('KeyType')
ValueType = TypeVar('ValueType')


# Google style guide: Do not rely on the atomicity of built-in types.
# Our launch and down process pool will be used by multiple threads,
# therefore we need to use a thread-safe dict.
# see https://google.github.io/styleguide/pyguide.html#218-threading
class ThreadSafeDict(Generic[KeyType, ValueType]):
    """A thread-safe dict."""

    def __init__(self, *args, **kwargs) -> None:
        self._dict: Dict[KeyType, ValueType] = dict(*args, **kwargs)
        self._lock = threading.Lock()

    def __getitem__(self, key: KeyType) -> ValueType:
        with self._lock:
            return self._dict.__getitem__(key)

    def __setitem__(self, key: KeyType, value: ValueType) -> None:
        with self._lock:
            return self._dict.__setitem__(key, value)

    def __delitem__(self, key: KeyType) -> None:
        with self._lock:
            return self._dict.__delitem__(key)

    def __len__(self) -> int:
        with self._lock:
            return self._dict.__len__()

    def __contains__(self, key: KeyType) -> bool:
        with self._lock:
            return self._dict.__contains__(key)

    def items(self):
        with self._lock:
            return self._dict.items()

    def values(self):
        with self._lock:
            return self._dict.values()


class RequestsAggregator:
    """Base class for request aggregator."""

    def add(self, request: 'fastapi.Request') -> None:
        """Add a request to the request aggregator."""
        raise NotImplementedError

    def clear(self) -> None:
        """Clear all current request aggregator."""
        raise NotImplementedError

    def to_dict(self) -> Dict[str, Any]:
        """Convert the aggregator to a dict."""
        raise NotImplementedError

    def __repr__(self) -> str:
        raise NotImplementedError


class RequestTimestamp(RequestsAggregator):
    """RequestTimestamp: Aggregates request timestamps.

    This is useful for QPS-based autoscaling.
    """

    def __init__(self) -> None:
        self.timestamps: List[float] = []

    def add(self, request: 'fastapi.Request') -> None:
        """Add a request to the request aggregator."""
        del request  # unused
        self.timestamps.append(time.time())

    def clear(self) -> None:
        """Clear all current request aggregator."""
        self.timestamps = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert the aggregator to a dict."""
        return {'timestamps': self.timestamps}

    def __repr__(self) -> str:
        return f'RequestTimestamp(timestamps={self.timestamps})'


def generate_service_name():
    return f'sky-service-{uuid.uuid4().hex[:4]}'


def generate_remote_service_dir_name(service_name: str) -> str:
    service_name = service_name.replace('-', '_')
    return os.path.join(constants.SKYSERVE_METADATA_DIR, service_name)


def generate_remote_tmp_task_yaml_file_name(service_name: str) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    # Don't expand here since it is used for remote machine.
    return os.path.join(dir_name, 'task.yaml.tmp')


def generate_task_yaml_file_name(service_name: str) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    dir_name = os.path.expanduser(dir_name)
    return os.path.join(dir_name, 'task.yaml')


def generate_remote_config_yaml_file_name(service_name: str) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    # Don't expand here since it is used for remote machine.
    return os.path.join(dir_name, 'config.yaml')


def generate_remote_controller_log_file_name(service_name: str) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    # Don't expand here since it is used for remote machine.
    return os.path.join(dir_name, 'controller.log')


def generate_remote_load_balancer_log_file_name(service_name: str) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    # Don't expand here since it is used for remote machine.
    return os.path.join(dir_name, 'load_balancer.log')


def generate_replica_launch_log_file_name(service_name: str,
                                          replica_id: int) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    dir_name = os.path.expanduser(dir_name)
    return os.path.join(dir_name, f'replica_{replica_id}_launch.log')


def generate_replica_down_log_file_name(service_name: str,
                                        replica_id: int) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    dir_name = os.path.expanduser(dir_name)
    return os.path.join(dir_name, f'replica_{replica_id}_down.log')


def generate_replica_local_log_file_name(service_name: str,
                                         replica_id: int) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    dir_name = os.path.expanduser(dir_name)
    return os.path.join(dir_name, f'replica_{replica_id}_local.log')


def generate_replica_cluster_name(service_name: str, replica_id: int) -> str:
    return f'{service_name}-{replica_id}'


def set_service_status_from_replica_statuses(
        service_name: str,
        replica_statuses: List[serve_state.ReplicaStatus]) -> None:
    record = serve_state.get_service_from_name(service_name)
    if record is None:
        raise ValueError(f'Service {service_name!r} does not exist. '
                         'Cannot refresh service status.')
    if record['status'] == serve_state.ServiceStatus.SHUTTING_DOWN:
        # When the service is shutting down, there is a period of time which the
        # controller still responds to the request, and the replica is not
        # terminated, the service status will still be READY, but we don't want
        # change service status to READY.
        return
    serve_state.set_service_status(
        service_name,
        serve_state.ServiceStatus.from_replica_statuses(replica_statuses))


def update_service_status() -> None:
    services = serve_state.get_services()
    for record in services:
        if record['status'] == serve_state.ServiceStatus.SHUTTING_DOWN:
            # Skip services that is shutting down.
            continue
        controller_job_id = record['controller_job_id']
        assert controller_job_id is not None
        controller_status = job_lib.get_status(controller_job_id)
        if controller_status is None or controller_status.is_terminal():
            # If controller job is not running, set it as controller failed.
            serve_state.set_service_status(
                record['name'], serve_state.ServiceStatus.CONTROLLER_FAILED)


def _get_service_status(
        service_name: str,
        with_replica_info: bool = True) -> Optional[Dict[str, Any]]:
    """Get the status dict of the service.

    Args:
        service_name: The name of the service.
        with_replica_info: Whether to include the information of all replicas.

    Returns:
        A dictionary describing the status of the service if the service exists.
        Otherwise, return None.
    """
    record = serve_state.get_service_from_name(service_name)
    if record is None:
        return None
    if with_replica_info:
        record['replica_info'] = [
            info.to_info_dict(with_handle=True)
            for info in serve_state.get_replica_infos(service_name)
        ]
    return record


def get_service_status_encoded(service_names: Optional[List[str]]) -> str:
    service_statuses = []
    if service_names is None:
        # Get all service names
        service_names = serve_state.get_glob_service_names(None)
    for service_name in service_names:
        service_status = _get_service_status(service_name)
        if service_status is None:
            continue
        service_statuses.append({
            k: base64.b64encode(pickle.dumps(v)).decode('utf-8')
            for k, v in service_status.items()
        })
    return common_utils.encode_payload(service_statuses)


def load_service_status(payload: str) -> List[Dict[str, Any]]:
    service_statuses_encoded = common_utils.decode_payload(payload)
    service_statuses = []
    for service_status in service_statuses_encoded:
        service_statuses.append({
            k: pickle.loads(base64.b64decode(v))
            for k, v in service_status.items()
        })
    return service_statuses


def _terminate_failed_services(
        service_name: str,
        service_status: serve_state.ServiceStatus) -> Optional[str]:
    """Terminate service in failed status.

    Services included in ServiceStatus.failed_statuses() do not have an
    active controller process, so we can't send a file terminate signal
    to the controller. Instead, we manually cleanup database record for
    the service and alert the user about a potential resource leak.

    Returns:
        A message indicating potential resource leak (if any). If no
        resource leak is detected, return None.
    """
    remaining_replica_clusters = []
    # The controller should have already attempted to terminate those
    # replicas, so we don't need to try again here.
    for replica_info in serve_state.get_replica_infos(service_name):
        # TODO(tian): Refresh latest status of the cluster.
        if global_user_state.get_cluster_from_name(
                replica_info.cluster_name) is not None:
            remaining_replica_clusters.append(f'{replica_info.cluster_name!r}')
        serve_state.remove_replica(service_name, replica_info.replica_id)

    service_dir = os.path.expanduser(
        generate_remote_service_dir_name(service_name))
    shutil.rmtree(service_dir)
    serve_state.remove_service(service_name)

    if not remaining_replica_clusters:
        return None
    remaining_identity = ', '.join(remaining_replica_clusters)
    return (f'{colorama.Fore.YELLOW}terminate service {service_name!r} with '
            f'failed status ({service_status}). This may indicate a resource '
            'leak. Please check the following SkyPilot clusters on the '
            f'controller: {remaining_identity}{colorama.Style.RESET_ALL}')


def terminate_services(service_names: Optional[List[str]], purge: bool) -> str:
    service_names = serve_state.get_glob_service_names(service_names)
    terminated_service_names = []
    messages = []
    for service_name in service_names:
        service_status = _get_service_status(service_name,
                                             with_replica_info=False)
        assert service_status is not None
        if service_status['status'] == serve_state.ServiceStatus.SHUTTING_DOWN:
            # Already scheduled to be terminated.
            continue
        if (service_status['status']
                in serve_state.ServiceStatus.failed_statuses()):
            if purge:
                message = _terminate_failed_services(service_name,
                                                     service_status['status'])
                if message is not None:
                    messages.append(message)
            else:
                messages.append(
                    f'{colorama.Fore.YELLOW}Service {service_name!r} is in '
                    f'failed status ({service_status["status"]}). Skipping '
                    'its termination as it could lead to a resource leak. '
                    f'(Use `sky serve down {service_name} --purge` to '
                    'forcefully terminate the service.)'
                    f'{colorama.Style.RESET_ALL}')
                # Don't add to terminated_service_names since it's not
                # actually terminated.
                continue
        else:
            # Send the terminate signal to controller.
            signal_file = pathlib.Path(
                constants.SIGNAL_FILE_PATH.format(service_name))
            # Filelock is needed to prevent race condition between signal
            # check/removal and signal writing.
            with filelock.FileLock(str(signal_file) + '.lock'):
                with signal_file.open(mode='w') as f:
                    f.write(UserSignal.TERMINATE.value)
                    f.flush()
        terminated_service_names.append(f'{service_name!r}')
    if len(terminated_service_names) == 0:
        messages.append('No service to terminate.')
    else:
        identity_str = f'Service {terminated_service_names[0]} is'
        if len(terminated_service_names) > 1:
            terminated_service_names_str = ', '.join(terminated_service_names)
            identity_str = f'Services {terminated_service_names_str} are'
        messages.append(f'{identity_str} scheduled to be terminated.')
    return '\n'.join(messages)


def wait_service_initialization(service_name: str, job_id: int) -> str:
    """Util function to call at the end of `sky.serve.up()`.

    This function will:
        (1) Check the name duplication by job id of the controller. If
            the job id is not the same as the database record, this
            means another service is already taken that name. See
            sky/serve/api.py::up for more details.
        (2) Wait for the load balancer port to be assigned and return.

    Returns:
        Encoded load balancer port assigned to the service.
    """
    start_time = time.time()
    while True:
        record = serve_state.get_service_from_name(service_name)
        if record is not None:
            if job_id != record['controller_job_id']:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'The service {service_name!r} is already running. '
                        'Please specify a different name for your service. '
                        'To update an existing service, run: `sky serve down` '
                        'and then `sky serve up` again (in-place update will '
                        'be supported in the future).')
            lb_port = record['load_balancer_port']
            if lb_port is not None:
                return common_utils.encode_payload(lb_port)
        elif len(serve_state.get_services()) >= NUM_SERVICE_THRESHOLD:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Max number of services reached. '
                                   'To spin up more services, please '
                                   'tear down some existing services.')
        if time.time() - start_time > constants.INITIALIZATION_TIMEOUT_SECONDS:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Initialization of service {service_name!r} timeout.')
        time.sleep(1)


def load_service_initialization_result(payload: str) -> int:
    return common_utils.decode_payload(payload)


def check_service_status_healthy(service_name: str) -> Optional[str]:
    service_record = serve_state.get_service_from_name(service_name)
    if service_record is None:
        return f'Service {service_name!r} does not exist.'
    if service_record['status'] == serve_state.ServiceStatus.CONTROLLER_INIT:
        return (f'Service {service_name!r} is still initializing its '
                'controller. Please try again later.')
    return None


def _follow_replica_logs(
        file: TextIO,
        cluster_name: str,
        *,
        finish_stream: Callable[[], bool],
        exit_if_stream_end: bool = False,
        no_new_content_timeout: Optional[int] = None) -> Iterator[str]:
    line = ''
    log_file = None
    no_new_content_cnt = 0

    def cluster_is_up() -> bool:
        cluster_record = global_user_state.get_cluster_from_name(cluster_name)
        if cluster_record is None:
            return False
        return cluster_record['status'] == status_lib.ClusterStatus.UP

    while True:
        tmp = file.readline()
        if tmp is not None and tmp != '':
            no_new_content_cnt = 0
            line += tmp
            if '\n' in line or '\r' in line:
                # Tailing detailed progress for user. All logs in skypilot is
                # of format `To view detailed progress: tail -n100 -f *.log`.
                x = re.match(_SKYPILOT_PROVISION_LOG_PATTERN, line)
                if x is not None:
                    log_file = os.path.expanduser(x.group(1))
                elif re.match(_SKYPILOT_LOG_PATTERN, line) is None:
                    # Not print other logs (file sync logs) since we lack
                    # utility to determine when these log files are finished
                    # writing.
                    # TODO(tian): Not skip these logs since there are small
                    # chance that error will happen in file sync. Need to find
                    # a better way to do this.
                    yield line
                    # Output next line first since it indicates the process is
                    # starting. For our launching logs, it's always:
                    # Launching on <cloud> <region> (<zone>)
                    if log_file is not None:
                        with open(log_file, 'r', newline='') as f:
                            # We still exit if more than 10 seconds without new
                            # content to avoid any internal bug that causes
                            # the launch failed and cluster status remains INIT.
                            for l in _follow_replica_logs(
                                    f,
                                    cluster_name,
                                    finish_stream=cluster_is_up,
                                    exit_if_stream_end=exit_if_stream_end,
                                    no_new_content_timeout=10):
                                yield l
                        log_file = None
                line = ''
        else:
            if exit_if_stream_end or finish_stream():
                break
            if no_new_content_timeout is not None:
                if no_new_content_cnt >= no_new_content_timeout:
                    break
                no_new_content_cnt += 1
            time.sleep(1)


def stream_replica_logs(service_name: str,
                        replica_id: int,
                        follow: bool,
                        skip_local_log_file_check: bool = False) -> str:
    msg = check_service_status_healthy(service_name)
    if msg is not None:
        return msg
    print(f'{colorama.Fore.YELLOW}Start streaming logs for launching process '
          f'of replica {replica_id}.{colorama.Style.RESET_ALL}')
    local_log_file_name = generate_replica_local_log_file_name(
        service_name, replica_id)

    if not skip_local_log_file_check and os.path.exists(local_log_file_name):
        # When sync down, we set skip_local_log_file_check to False so it won't
        # detect the just created local log file. Otherwise, it indicates the
        # replica is already been terminated. All logs should be in the local
        # log file and we don't need to stream logs for it.
        with open(local_log_file_name, 'r') as f:
            print(f.read(), flush=True)
        return ''

    replica_cluster_name = generate_replica_cluster_name(
        service_name, replica_id)
    handle = global_user_state.get_handle_from_cluster_name(
        replica_cluster_name)
    if handle is None:
        return _FAILED_TO_FIND_REPLICA_MSG.format(replica_id=replica_id)
    assert isinstance(handle, backends.CloudVmRayResourceHandle), handle

    launch_log_file_name = generate_replica_launch_log_file_name(
        service_name, replica_id)
    if not os.path.exists(launch_log_file_name):
        return (f'{colorama.Fore.RED}Replica {replica_id} doesn\'t exist.'
                f'{colorama.Style.RESET_ALL}')

    def _get_replica_status() -> serve_state.ReplicaStatus:
        replica_info = serve_state.get_replica_infos(service_name)
        for info in replica_info:
            if info.replica_id == replica_id:
                return info.status
        raise ValueError(
            _FAILED_TO_FIND_REPLICA_MSG.format(replica_id=replica_id))

    finish_stream = (
        lambda: _get_replica_status() != serve_state.ReplicaStatus.PROVISIONING)
    with open(launch_log_file_name, 'r', newline='') as f:
        for line in _follow_replica_logs(f,
                                         replica_cluster_name,
                                         finish_stream=finish_stream,
                                         exit_if_stream_end=not follow):
            print(line, end='', flush=True)
    if (not follow and
            _get_replica_status() == serve_state.ReplicaStatus.PROVISIONING):
        # Early exit if not following the logs.
        return ''

    # Notify user here to make sure user won't think the log is finished.
    print(f'{colorama.Fore.YELLOW}Start streaming logs for task job '
          f'of replica {replica_id}...{colorama.Style.RESET_ALL}')

    backend = backends.CloudVmRayBackend()
    # Always tail the latest logs, which represent user setup & run.
    returncode = backend.tail_logs(handle, job_id=None, follow=follow)
    if returncode != 0:
        return (f'{colorama.Fore.RED}Failed to stream logs for replica '
                f'{replica_id}.{colorama.Style.RESET_ALL}')
    return ''


def _follow_logs(file: TextIO, *, finish_stream: Callable[[], bool],
                 exit_if_stream_end: bool) -> Iterator[str]:
    line = ''
    while True:
        tmp = file.readline()
        if tmp is not None and tmp != '':
            line += tmp
            if '\n' in line or '\r' in line:
                yield line
                line = ''
        else:
            if exit_if_stream_end or finish_stream():
                break
            time.sleep(1)


def stream_serve_process_logs(service_name: str, stream_controller: bool,
                              follow: bool) -> str:
    msg = check_service_status_healthy(service_name)
    if msg is not None:
        return msg
    if stream_controller:
        log_file = generate_remote_controller_log_file_name(service_name)
    else:
        log_file = generate_remote_load_balancer_log_file_name(service_name)

    def _service_is_terminal() -> bool:
        record = serve_state.get_service_from_name(service_name)
        if record is None:
            return True
        return record['status'] in serve_state.ServiceStatus.failed_statuses()

    with open(os.path.expanduser(log_file), 'r', newline='') as f:
        for line in _follow_logs(f,
                                 finish_stream=_service_is_terminal,
                                 exit_if_stream_end=not follow):
            print(line, end='', flush=True)
    return ''


# ================== Table Formatter for `sky serve status` ==================


def _get_replicas(service_record: Dict[str, Any]) -> str:
    ready_replica_num, total_replica_num = 0, 0
    for info in service_record['replica_info']:
        if info['status'] == serve_state.ReplicaStatus.READY:
            ready_replica_num += 1
        # If auto restart enabled, not count FAILED replicas here.
        if (not service_record['auto_restart'] or
                info['status'] != serve_state.ReplicaStatus.FAILED):
            total_replica_num += 1
    return f'{ready_replica_num}/{total_replica_num}'


def get_endpoint(service_record: Dict[str, Any]) -> str:
    # Don't use backend_utils.is_controller_up since it is too slow.
    handle = global_user_state.get_handle_from_cluster_name(
        SKY_SERVE_CONTROLLER_NAME)
    assert isinstance(handle, backends.CloudVmRayResourceHandle)
    if handle is None or handle.head_ip is None:
        return '-'
    load_balancer_port = service_record['load_balancer_port']
    if load_balancer_port is None:
        return '-'
    return f'{handle.head_ip}:{load_balancer_port}'


def format_service_table(service_records: List[Dict[str, Any]],
                         show_all: bool) -> str:
    if not service_records:
        return 'No existing services.'

    service_columns = ['NAME', 'UPTIME', 'STATUS', 'REPLICAS', 'ENDPOINT']
    if show_all:
        service_columns.extend(['POLICY', 'REQUESTED_RESOURCES'])
    service_table = log_utils.create_table(service_columns)

    replica_infos = []
    for record in service_records:
        for replica in record['replica_info']:
            replica['service_name'] = record['name']
            replica_infos.append(replica)

        service_name = record['name']
        uptime = log_utils.readable_time_duration(record['uptime'],
                                                  absolute=True)
        service_status = record['status']
        status_str = service_status.colored_str()
        replicas = _get_replicas(record)
        endpoint = get_endpoint(record)
        policy = record['policy']
        requested_resources = record['requested_resources']

        service_values = [
            service_name,
            uptime,
            status_str,
            replicas,
            endpoint,
        ]
        if show_all:
            service_values.extend([policy, requested_resources])
        service_table.add_row(service_values)

    replica_table = _format_replica_table(replica_infos, show_all)
    return (f'{service_table}\n'
            f'\n{colorama.Fore.CYAN}{colorama.Style.BRIGHT}'
            f'Service Replicas{colorama.Style.RESET_ALL}\n'
            f'{replica_table}')


def _format_replica_table(replica_records: List[Dict[str, Any]],
                          show_all: bool) -> str:
    if not replica_records:
        return 'No existing replicas.'

    replica_columns = [
        'SERVICE_NAME', 'ID', 'IP', 'LAUNCHED', 'RESOURCES', 'STATUS', 'REGION'
    ]
    if show_all:
        replica_columns.append('ZONE')
    replica_table = log_utils.create_table(replica_columns)

    truncate_hint = ''
    if not show_all:
        if len(replica_records) > _REPLICA_TRUNC_NUM:
            truncate_hint = '\n... (use --all to show all replicas)'
        replica_records = replica_records[:_REPLICA_TRUNC_NUM]

    for record in replica_records:
        service_name = record['service_name']
        replica_id = record['replica_id']
        replica_ip = '-'
        launched_at = log_utils.readable_time_duration(record['launched_at'])
        resources_str = '-'
        replica_status = record['status']
        status_str = replica_status.colored_str()
        region = '-'
        zone = '-'

        replica_handle: 'backends.CloudVmRayResourceHandle' = record['handle']
        if replica_handle is not None:
            if replica_handle.head_ip is not None:
                replica_ip = replica_handle.head_ip
            resources_str = resources_utils.get_readable_resources_repr(
                replica_handle, simplify=not show_all)
            if replica_handle.launched_resources.region is not None:
                region = replica_handle.launched_resources.region
            if replica_handle.launched_resources.zone is not None:
                zone = replica_handle.launched_resources.zone

        replica_values = [
            service_name,
            replica_id,
            replica_ip,
            launched_at,
            resources_str,
            status_str,
            region,
        ]
        if show_all:
            replica_values.append(zone)
        replica_table.add_row(replica_values)

    return f'{replica_table}{truncate_hint}'


# =========================== CodeGen for Sky Serve ===========================


# TODO(tian): Use REST API instead of SSH in the future. This codegen pattern
# is to reuse the authentication of ssh. If we want to use REST API, we need
# to implement some authentication mechanism.
class ServeCodeGen:
    """Code generator for SkyServe.

    Usage:
      >> code = ServeCodeGen.get_service_status(service_name)
    """
    _PREFIX = [
        'from sky.serve import serve_state',
        'from sky.serve import serve_utils',
    ]

    @classmethod
    def get_service_status(cls, service_names: Optional[List[str]]) -> str:
        code = [
            f'msg = serve_utils.get_service_status_encoded({service_names!r})',
            'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def terminate_services(cls, service_names: Optional[List[str]],
                           purge: bool) -> str:
        code = [
            f'msg = serve_utils.terminate_services({service_names!r}, '
            f'purge={purge})', 'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def wait_service_initialization(cls, service_name: str, job_id: int) -> str:
        code = [
            'msg = serve_utils.wait_service_initialization('
            f'{service_name!r}, {job_id})', 'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def stream_replica_logs(cls,
                            service_name: str,
                            replica_id: int,
                            follow: bool,
                            skip_local_log_file_check: bool = False) -> str:
        code = [
            'msg = serve_utils.stream_replica_logs('
            f'{service_name!r}, {replica_id!r}, follow={follow}, '
            f'skip_local_log_file_check={skip_local_log_file_check})',
            'print(msg, flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def stream_serve_process_logs(cls, service_name: str,
                                  stream_controller: bool, follow: bool) -> str:
        code = [
            f'msg = serve_utils.stream_serve_process_logs({service_name!r}, '
            f'{stream_controller}, follow={follow})', 'print(msg, flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        generated_code = '; '.join(code)
        return f'python3 -u -c {shlex.quote(generated_code)}'
