"""User interface with the SkyServe."""
import base64
import enum
import os
import pickle
import re
import shlex
import shutil
import signal
import threading
import time
import typing
from typing import (Any, Callable, Dict, Generic, Iterator, List, Optional, Set,
                    TextIO, Tuple, TypeVar)

import colorama
import requests

from sky import backends
from sky import global_user_state
from sky import status_lib
from sky.data import storage as storage_lib
from sky.serve import constants
from sky.serve import serve_state
from sky.skylet import job_lib
from sky.utils import common_utils
from sky.utils import subprocess_utils

if typing.TYPE_CHECKING:
    import fastapi

    import sky

_CONTROLLER_URL = 'http://localhost:{CONTROLLER_PORT}'
_SKYPILOT_PROVISION_LOG_PATTERN = r'.*tail -n100 -f (.*provision\.log).*'
_SKYPILOT_LOG_PATTERN = r'.*tail -n100 -f (.*\.log).*'
_FAILED_TO_FIND_REPLICA_MSG = (
    f'{colorama.Fore.RED}Failed to find replica '
    '{replica_id}. Please use `sky serve status [SERVICE_ID]`'
    f' to check all valid replica id.{colorama.Style.RESET_ALL}')


class ServiceComponent(enum.Enum):
    CONTROLLER = 'controller'
    LOAD_BALANCER = 'load_balancer'
    REPLICA = 'replica'


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


class RequestInformation:
    """Base class for request information."""

    def add(self, request: 'fastapi.Request') -> None:
        """Add a request to the request information."""
        raise NotImplementedError

    def get(self) -> List[Any]:
        """Get all current request information."""
        raise NotImplementedError

    def clear(self) -> None:
        """Clear all current request information."""
        raise NotImplementedError

    def __repr__(self) -> str:
        raise NotImplementedError


class RequestTimestamp(RequestInformation):
    """RequestTimestamp: Request information that stores request timestamps."""

    def __init__(self) -> None:
        self.timestamps: List[float] = []

    def add(self, request: 'fastapi.Request') -> None:
        """Add a request to the request information."""
        del request  # unused
        self.timestamps.append(time.time())

    def get(self) -> List[float]:
        """Get all current request information."""
        return self.timestamps

    def clear(self) -> None:
        """Clear all current request information."""
        self.timestamps = []

    def __repr__(self) -> str:
        return f'RequestTimestamp(timestamps={self.timestamps})'


def kill_children_and_self_processes() -> None:
    subprocess_utils.kill_children_processes()
    os.kill(os.getpid(), signal.SIGKILL)


def _get_existing_controller_names() -> Set[str]:
    """Get existing sky serve controller names.

    There is two possible indicators for a controller:
      1. It is in the cluster database, which means it is already created;
      2. It is in the service database, which means it will be created
         later in the future. This usually happens when multiple `sky serve up`
         are running simultaneously.

    Returns:
        A set of existing sky serve controller names.
    """
    controller_in_service_db = {
        record['controller_name']
        for record in global_user_state.get_services()
    }
    controller_in_cluster_db = {
        record['name']
        for record in global_user_state.get_clusters()
        if record['name'].startswith(constants.CONTROLLER_PREFIX)
    }
    return controller_in_service_db | controller_in_cluster_db


def generate_controller_cluster_name(existing_controllers: Set[str]) -> str:
    index = 0
    while True:
        controller_name = (f'{constants.CONTROLLER_PREFIX}'
                           f'{common_utils.get_user_hash()}-{index}')
        if controller_name not in existing_controllers:
            return controller_name
        index += 1


def generate_controller_yaml_file_name(service_name: str) -> str:
    service_name = service_name.replace('-', '_')
    prefix = os.path.expanduser(constants.SERVE_PREFIX)
    return os.path.join(prefix, f'{service_name}_controller.yaml')


def generate_remote_service_dir_name(service_name: str) -> str:
    service_name = service_name.replace('-', '_')
    return os.path.join(constants.SERVE_PREFIX, service_name)


def generate_remote_task_yaml_file_name(service_name: str) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    # Don't expand here since it is used for remote machine.
    return os.path.join(dir_name, 'task.yaml')


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


def get_replica_id_from_cluster_name(cluster_name: str) -> int:
    return int(cluster_name.split('-')[-1])


def gen_ports_for_serve_process(controller_name: str) -> Tuple[int, int]:
    services = global_user_state.get_services_from_controller_name(
        controller_name)
    existing_controller_ports, existing_load_balancer_ports = set(), set()
    for service in services:
        service_handle: ServiceHandle = service['handle']
        existing_controller_ports.add(service_handle.controller_port)
        existing_load_balancer_ports.add(service_handle.load_balancer_port)
    controller_port = constants.CONTROLLER_PORT_START
    while controller_port in existing_controller_ports:
        controller_port += 1
    load_balancer_port = constants.LOAD_BALANCER_PORT_START
    while load_balancer_port in existing_load_balancer_ports:
        load_balancer_port += 1
    return controller_port, load_balancer_port


def _get_service_slot_on_controller(controller_name: str) -> int:
    """Get the number of slots to run services on the controller.

    A controller only have limited available slots for a new services.
    Max number of slots on a controller is determined by the memory of
    the controller, since ray job and our skypilot code is very memory
    demanding (~1GB/service).

    Args:
        controller_name: The name of the controller.

    Returns:
        Number of slots on the controller.
    """
    memory_requirements = 0.
    controller_record = global_user_state.get_cluster_from_name(controller_name)
    if controller_record is not None:
        # If controller is already created, use its launched resources.
        handle = controller_record['handle']
        assert isinstance(handle, backends.CloudVmRayResourceHandle)
        # Determine max number of services on this controller.
        controller_cloud = handle.launched_resources.cloud
        _, memory_requirements = (
            controller_cloud.get_vcpus_mem_from_instance_type(
                handle.launched_resources.instance_type))
    else:
        # Corner case: Multiple `sky serve up` are running simultaneously
        # and the controller is not created yet. We created a resources
        # for each initializing controller, and use the minimal memory
        # requirement among them, since any of them could be the first to
        # launch the controller.
        service_records = (global_user_state.get_services_from_controller_name(
            controller_name))
        for service_record in service_records:
            r = service_record['handle'].requested_controller_resources
            # Remove the '+' in memory requirement.
            memory_requirements = min(memory_requirements,
                                      float(r.memory.strip('+')))
    # Determine max number of services on this controller.
    max_services_num = int(memory_requirements /
                           constants.SERVICES_MEMORY_USAGE_GB)
    # Get current number of services on this controller.
    services_num_on_controller = len(
        global_user_state.get_services_from_controller_name(controller_name))
    return max_services_num - services_num_on_controller


def get_available_controller_name() -> Tuple[str, bool]:
    """Get available controller name to use.

    Only consider controllers that have available slots for services.
    If multiple controllers are available, choose the one with most number of
    services to decrease the number of controllers.
    This function needs to be called within a lock, to avoid concurrency issue
    from `existing_controllers` being staled, also, to avoid multiple
    `sky serve up` select the same last slot on a controller.

    Returns:
        A tuple of controller name and a boolean value indicating whether the
        controller name is newly generated.
    """
    # Get all existing controllers.
    existing_controllers = _get_existing_controller_names()
    controller2slots = dict()
    # Get a mapping from controller name to number of services on it.
    for controller_name in existing_controllers:
        num_slots = _get_service_slot_on_controller(controller_name)
        # Only consider controllers that have available slots for services.
        if num_slots > 0:
            controller2slots[controller_name] = num_slots
    if not controller2slots:
        return generate_controller_cluster_name(existing_controllers), True
    # If multiple controllers are available, choose the one with least number of
    # slots, i.e. most number of services.
    return min(controller2slots.keys(),
               key=lambda k: controller2slots[k]), False


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
        controller_status = job_lib.get_status(record['controller_job_id'])
        if controller_status is None or controller_status.is_terminal():
            # If controller job is not running, set it as controller failed.
            serve_state.set_service_status(
                record['name'], serve_state.ServiceStatus.CONTROLLER_FAILED)


class ServiceHandle(object):
    """A pickle-able tuple of:

    - (required) Service name.
    - (required) Service autoscaling policy description str.
    - (required) Service requested resources.
    - (required) Service requested controller resources.
    - (required) Whether the service have auto restart enabled.
    - (required) Controller port.
    - (required) LoadBalancer port.
    - (optional) Service endpoint IP.
    - (optional) Controller and LoadBalancer job id.
    - (optional) Ephemeral storage generated for the service.

    This class is only used as a cache for information fetched from controller.
    """
    _VERSION = 0

    def __init__(
        self,
        *,
        service_name: str,
        requested_controller_resources: 'sky.Resources',
        controller_port: int,
        load_balancer_port: int,
        endpoint_ip: Optional[str] = None,
        ephemeral_storage: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self._version = self._VERSION
        self.service_name = service_name
        self.requested_controller_resources = requested_controller_resources
        self.controller_port = controller_port
        self.load_balancer_port = load_balancer_port
        self.endpoint_ip = endpoint_ip
        self.ephemeral_storage = ephemeral_storage

    def __repr__(self) -> str:
        return ('ServiceHandle('
                f'\n\tservice_name={self.service_name},'
                '\n\trequested_controller_resources='
                f'{self.requested_controller_resources},'
                f'\n\tcontroller_port={self.controller_port},'
                f'\n\tload_balancer_port={self.load_balancer_port},'
                f'\n\tendpoint_ip={self.endpoint_ip},'
                f'\n\tephemeral_storage={self.ephemeral_storage})')

    def cleanup_ephemeral_storage(self) -> None:
        if self.ephemeral_storage is None:
            return
        for storage_config in self.ephemeral_storage:
            storage = storage_lib.Storage.from_yaml_config(storage_config)
            storage.delete(silent=True)

    def __setstate__(self, state):
        self._version = self._VERSION
        self.__dict__.update(state)


def _get_controller_port_from_service_name(service_name: str) -> int:
    record = serve_state.get_service_from_name(service_name)
    if record is None:
        raise ValueError(f'Service {service_name!r} does not exist.')
    return record['controller_port']


def _get_latest_info(service_name: str, decode: bool = True) -> Dict[str, Any]:
    controller_port = _get_controller_port_from_service_name(service_name)
    resp = requests.get(
        _CONTROLLER_URL.format(CONTROLLER_PORT=controller_port) +
        '/controller/get_latest_info')
    resp.raise_for_status()
    if not decode:
        return resp.json()
    return {
        k: pickle.loads(base64.b64decode(v)) for k, v in resp.json().items()
    }


def get_latest_info(service_name: str) -> str:
    return common_utils.encode_payload(
        _get_latest_info(service_name, decode=False))


def load_latest_info(payload: str) -> Dict[str, Any]:
    latest_info = common_utils.decode_payload(payload)
    latest_info = {
        k: pickle.loads(base64.b64decode(v)) for k, v in latest_info.items()
    }
    return latest_info


def terminate_service(service_name: str) -> str:
    controller_port = _get_controller_port_from_service_name(service_name)
    resp = requests.post(
        _CONTROLLER_URL.format(CONTROLLER_PORT=controller_port) +
        '/controller/terminate')
    resp = base64.b64encode(pickle.dumps(resp)).decode('utf-8')
    return common_utils.encode_payload(resp)


def load_terminate_service_result(payload: str) -> Any:
    terminate_resp = common_utils.decode_payload(payload)
    terminate_resp = pickle.loads(base64.b64decode(terminate_resp))
    return terminate_resp


def check_service_status_healthy(service_name: str) -> Optional[str]:
    service_record = serve_state.get_service_from_name(service_name)
    if service_record is None:
        return f'Service {service_name!r} does not exist.'
    if service_record['status'] == serve_state.ServiceStatus.CONTROLLER_INIT:
        return (f'Service {service_name!r} is still initializing its '
                'controller. Please try again later.')
    if service_record['status'] == serve_state.ServiceStatus.CONTROLLER_FAILED:
        return (f'Service {service_name!r}\'s controller failed. '
                'Cannot tail logs.')
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
    controller_port = _get_controller_port_from_service_name(service_name)
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
        resp = requests.get(
            _CONTROLLER_URL.format(CONTROLLER_PORT=controller_port) +
            '/controller/get_latest_info')
        if resp.status_code != 200:
            raise ValueError(
                f'{colorama.Fore.RED}Failed to get replica info for service '
                f'{service_name}.{colorama.Style.RESET_ALL}')
        replica_info = resp.json()['replica_info']
        replica_info = pickle.loads(base64.b64decode(replica_info))
        target_info: Optional[Dict[str, Any]] = None
        for info in replica_info:
            if info['replica_id'] == replica_id:
                target_info = info
                break
        if target_info is None:
            raise ValueError(
                _FAILED_TO_FIND_REPLICA_MSG.format(replica_id=replica_id))
        return target_info['status']

    finish_stream = (
        lambda: _get_replica_status() != serve_state.ReplicaStatus.PROVISIONING)
    with open(launch_log_file_name, 'r', newline='') as f:
        for line in _follow_replica_logs(f,
                                         replica_cluster_name,
                                         finish_stream=finish_stream,
                                         exit_if_stream_end=not follow):
            print(line, end='', flush=True)
    if not follow and _get_replica_status(
    ) == serve_state.ReplicaStatus.PROVISIONING:
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


def cleanup_service_utility_files(service_name: str) -> None:
    """Cleanup utility files for a service."""
    dir_name = generate_remote_service_dir_name(service_name)
    dir_name = os.path.expanduser(dir_name)
    if os.path.exists(dir_name):
        shutil.rmtree(dir_name)


class ServeCodeGen:
    """Code generator for SkyServe.

    Usage:
      >> code = ServeCodeGen.get_latest_info(service_name)
    """
    _PREFIX = [
        'from sky.serve import serve_state',
        'from sky.serve import serve_utils',
    ]

    @classmethod
    def add_service(cls, job_id: int, service_handle: ServiceHandle) -> str:
        code = [
            f'serve_state.add_service({job_id}, '
            f'{service_handle.service_name!r}, '
            f'{service_handle.controller_port})',
        ]
        return cls._build(code)

    @classmethod
    def get_latest_info(cls, service_name: str) -> str:
        code = [
            f'msg = serve_utils.get_latest_info({service_name!r})',
            'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def terminate_service(cls, service_name: str) -> str:
        code = [
            f'msg = serve_utils.terminate_service({service_name!r})',
            'print(msg, end="", flush=True)'
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

    # TODO(tian): Move this into termination of controller
    @classmethod
    def cleanup_service(cls, service_name: str) -> str:
        code = [
            f'serve_utils.cleanup_service_utility_files({service_name!r})',
            f'serve_state.remove_service({service_name!r})',
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        generated_code = '; '.join(code)
        return f'python3 -u -c {shlex.quote(generated_code)}'
