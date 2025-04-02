"""User interface with the SkyServe."""
import base64
import collections
import dataclasses
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
from typing import (Any, Callable, DefaultDict, Dict, Generic, Iterator, List,
                    Optional, TextIO, Type, TypeVar)
import uuid

import colorama
import filelock

from sky import backends
from sky import exceptions
from sky import global_user_state
from sky.adaptors import common as adaptors_common
from sky.serve import constants
from sky.serve import serve_state
from sky.serve import spot_placer
from sky.skylet import constants as skylet_constants
from sky.skylet import job_lib
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import log_utils
from sky.utils import message_utils
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import fastapi
    import psutil
    import requests

    import sky
    from sky.serve import replica_managers
else:
    psutil = adaptors_common.LazyImport('psutil')
    requests = adaptors_common.LazyImport('requests')


@annotations.lru_cache(scope='request')
def get_num_service_threshold():
    """Get number of services threshold, calculating it only when needed."""
    system_memory_gb = psutil.virtual_memory().total // (1024**3)
    return system_memory_gb // constants.CONTROLLER_MEMORY_USAGE_GB


_CONTROLLER_URL = 'http://localhost:{CONTROLLER_PORT}'

# NOTE(dev): We assume log paths are either in ~/sky_logs/... or ~/.sky/...
# and always appear after a space. Be careful when changing UX as this
# assumption is used to expand some log files while ignoring others.
_SKYPILOT_LOG_DIRS = r'~/(sky_logs|\.sky)'
_SKYPILOT_PROVISION_LOG_PATTERN = (
    fr'.* ({_SKYPILOT_LOG_DIRS}/.*provision\.log)')
_SKYPILOT_LOG_PATTERN = fr'.* ({_SKYPILOT_LOG_DIRS}/.*\.log)'

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


class UpdateMode(enum.Enum):
    """Update mode for updating a service."""
    ROLLING = 'rolling'
    BLUE_GREEN = 'blue_green'


@dataclasses.dataclass
class TLSCredential:
    """TLS credential for the service."""
    keyfile: str
    certfile: str

    def dump_uvicorn_kwargs(self) -> Dict[str, str]:
        return {
            'ssl_keyfile': os.path.expanduser(self.keyfile),
            'ssl_certfile': os.path.expanduser(self.certfile),
        }


DEFAULT_UPDATE_MODE = UpdateMode.ROLLING

_SIGNAL_TO_ERROR = {
    UserSignal.TERMINATE: exceptions.ServeUserTerminatedError,
}

# pylint: disable=invalid-name
KeyType = TypeVar('KeyType')
ValueType = TypeVar('ValueType')


# Google style guide: Do not rely on the atomicity of built-in types.
# Our launch and down process pool will be used by multiple threads,
# therefore we need to use a thread-safe dict.
# see https://google.github.io/styleguide/pyguide.html#218-threading
class ThreadSafeDict(Generic[KeyType, ValueType]):
    """A thread-safe dict."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
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


def validate_service_task(task: 'sky.Task') -> None:
    """Validate the task for Sky Serve.

    Args:
        task: sky.Task to validate

    Raises:
        ValueError: if the arguments are invalid.
        RuntimeError: if the task.serve is not found.
    """
    spot_resources: List['sky.Resources'] = [
        resource for resource in task.resources if resource.use_spot
    ]
    # TODO(MaoZiming): Allow mixed on-demand and spot specification in resources
    # On-demand fallback should go to the resources specified as on-demand.
    if len(spot_resources) not in [0, len(task.resources)]:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'Resources must either all use spot or none use spot. '
                'To use on-demand and spot instances together, '
                'use `dynamic_ondemand_fallback` or set '
                'base_ondemand_fallback_replicas.')

    if task.service is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError('Service section not found.')

    policy_description = ('on-demand'
                          if task.service.dynamic_ondemand_fallback else 'spot')
    for resource in list(task.resources):
        if resource.job_recovery is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('job_recovery is disabled for SkyServe. '
                                 'SkyServe will replenish preempted spot '
                                 f'with {policy_description} instances.')

    # Try to create a spot placer from the task yaml. Check if the task yaml
    # is valid for spot placer.
    spot_placer.SpotPlacer.from_task(task.service, task)

    replica_ingress_port: Optional[int] = int(
        task.service.ports) if (task.service.ports is not None) else None
    for requested_resources in task.resources:
        if (task.service.use_ondemand_fallback and
                not requested_resources.use_spot):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    '`use_ondemand_fallback` is only supported '
                    'for spot resources. Please explicitly specify '
                    '`use_spot: true` in resources for on-demand fallback.')
        if (task.service.spot_placer is not None and
                not requested_resources.use_spot):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    '`spot_placer` is only supported for spot resources. '
                    'Please explicitly specify `use_spot: true` in resources.')
        if task.service.ports is None:
            requested_ports = list(
                resources_utils.port_ranges_to_set(requested_resources.ports))
            if len(requested_ports) != 1:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'To open multiple ports on the replica, please set the '
                        '`service.ports` field to specify a main service port. '
                        'Must only specify one port in resources otherwise. '
                        'Each replica will use the port specified as '
                        'application ingress port.')
            service_port = requested_ports[0]
            if replica_ingress_port is None:
                replica_ingress_port = service_port
            elif service_port != replica_ingress_port:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Got multiple ports: {service_port} and '
                        f'{replica_ingress_port} in different resources. '
                        'Please specify the same port instead.')


def generate_service_name():
    return f'sky-service-{uuid.uuid4().hex[:4]}'


def generate_remote_service_dir_name(service_name: str) -> str:
    service_name = service_name.replace('-', '_')
    return os.path.join(constants.SKYSERVE_METADATA_DIR, service_name)


def generate_remote_tmp_task_yaml_file_name(service_name: str) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    # Don't expand here since it is used for remote machine.
    return os.path.join(dir_name, 'task.yaml.tmp')


def generate_task_yaml_file_name(service_name: str,
                                 version: int,
                                 expand_user: bool = True) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    if expand_user:
        dir_name = os.path.expanduser(dir_name)
    return os.path.join(dir_name, f'task_v{version}.yaml')


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


def generate_replica_log_file_name(service_name: str, replica_id: int) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    dir_name = os.path.expanduser(dir_name)
    return os.path.join(dir_name, f'replica_{replica_id}.log')


def generate_remote_tls_keyfile_name(service_name: str) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    # Don't expand here since it is used for remote machine.
    return os.path.join(dir_name, 'tls_keyfile')


def generate_remote_tls_certfile_name(service_name: str) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    # Don't expand here since it is used for remote machine.
    return os.path.join(dir_name, 'tls_certfile')


def generate_replica_cluster_name(service_name: str, replica_id: int) -> str:
    return f'{service_name}-{replica_id}'


def set_service_status_and_active_versions_from_replica(
        service_name: str, replica_infos: List['replica_managers.ReplicaInfo'],
        update_mode: UpdateMode) -> None:
    record = serve_state.get_service_from_name(service_name)
    if record is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'The service is up-ed in an old version and does not '
                'support update. Please `sky serve down` '
                'it first and relaunch the service.')
    if record['status'] == serve_state.ServiceStatus.SHUTTING_DOWN:
        # When the service is shutting down, there is a period of time which the
        # controller still responds to the request, and the replica is not
        # terminated, the service status will still be READY, but we don't want
        # change service status to READY.
        return

    ready_replicas = list(filter(lambda info: info.is_ready, replica_infos))
    if update_mode == UpdateMode.ROLLING:
        active_versions = sorted(
            list(set(info.version for info in ready_replicas)))
    else:
        chosen_version = get_latest_version_with_min_replicas(
            service_name, replica_infos)
        active_versions = [chosen_version] if chosen_version is not None else []
    serve_state.set_service_status_and_active_versions(
        service_name,
        serve_state.ServiceStatus.from_replica_statuses(
            [info.status for info in ready_replicas]),
        active_versions=active_versions)


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
            serve_state.set_service_status_and_active_versions(
                record['name'], serve_state.ServiceStatus.CONTROLLER_FAILED)


def update_service_encoded(service_name: str, version: int, mode: str) -> str:
    service_status = _get_service_status(service_name)
    if service_status is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Service {service_name!r} does not exist.')
    controller_port = service_status['controller_port']
    resp = requests.post(
        _CONTROLLER_URL.format(CONTROLLER_PORT=controller_port) +
        '/controller/update_service',
        json={
            'version': version,
            'mode': mode,
        })
    if resp.status_code == 404:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'The service is up-ed in an old version and does not '
                'support update. Please `sky serve down` '
                'it first and relaunch the service. ')
    elif resp.status_code == 400:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Client error during service update: {resp.text}')
    elif resp.status_code == 500:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Server error during service update: {resp.text}')
    elif resp.status_code != 200:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Failed to update service: {resp.text}')

    service_msg = resp.json()['message']
    return message_utils.encode_payload(service_msg)


def terminate_replica(service_name: str, replica_id: int, purge: bool) -> str:
    service_status = _get_service_status(service_name)
    if service_status is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Service {service_name!r} does not exist.')
    replica_info = serve_state.get_replica_info_from_id(service_name,
                                                        replica_id)
    if replica_info is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Replica {replica_id} for service {service_name} does not '
                'exist.')

    controller_port = service_status['controller_port']
    resp = requests.post(
        _CONTROLLER_URL.format(CONTROLLER_PORT=controller_port) +
        '/controller/terminate_replica',
        json={
            'replica_id': replica_id,
            'purge': purge,
        })

    message: str = resp.json()['message']
    if resp.status_code != 200:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Failed to terminate replica {replica_id} '
                             f'in {service_name}. Reason:\n{message}')
    return message


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
    service_statuses: List[Dict[str, str]] = []
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
    # We have to use payload_type here to avoid the issue of
    # message_utils.decode_payload() not being able to correctly decode the
    # message with <sky-payload> tags.
    return message_utils.encode_payload(service_statuses,
                                        payload_type='service_status')


def load_service_status(payload: str) -> List[Dict[str, Any]]:
    try:
        service_statuses_encoded = message_utils.decode_payload(
            payload, payload_type='service_status')
    except ValueError as e:
        if 'Invalid payload string' in str(e):
            # Backward compatibility for serve controller started before #4660
            # where the payload type is not added.
            service_statuses_encoded = message_utils.decode_payload(payload)
        else:
            raise
    service_statuses: List[Dict[str, Any]] = []
    for service_status in service_statuses_encoded:
        if not isinstance(service_status, dict):
            raise ValueError(f'Invalid service status: {service_status}')
        service_statuses.append({
            k: pickle.loads(base64.b64decode(v))
            for k, v in service_status.items()
        })
    return service_statuses


def add_version_encoded(service_name: str) -> str:
    new_version = serve_state.add_version(service_name)
    return message_utils.encode_payload(new_version)


def load_version_string(payload: str) -> str:
    return message_utils.decode_payload(payload)


def _terminate_failed_services(
        service_name: str,
        service_status: Optional[serve_state.ServiceStatus]) -> Optional[str]:
    """Terminate service in failed status.

    Services included in ServiceStatus.failed_statuses() do not have an
    active controller process, so we can't send a file terminate signal
    to the controller. Instead, we manually cleanup database record for
    the service and alert the user about a potential resource leak.

    Returns:
        A message indicating potential resource leak (if any). If no
        resource leak is detected, return None.
    """
    remaining_replica_clusters: List[str] = []
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
    serve_state.delete_all_versions(service_name)

    if not remaining_replica_clusters:
        return None
    remaining_identity = ', '.join(remaining_replica_clusters)
    return (f'{colorama.Fore.YELLOW}terminate service {service_name!r} with '
            f'failed status ({service_status}). This may indicate a resource '
            'leak. Please check the following SkyPilot clusters on the '
            f'controller: {remaining_identity}{colorama.Style.RESET_ALL}')


def terminate_services(service_names: Optional[List[str]], purge: bool) -> str:
    service_names = serve_state.get_glob_service_names(service_names)
    terminated_service_names: List[str] = []
    messages: List[str] = []
    for service_name in service_names:
        service_status = _get_service_status(service_name,
                                             with_replica_info=False)
        if (service_status is not None and service_status['status']
                == serve_state.ServiceStatus.SHUTTING_DOWN):
            # Already scheduled to be terminated.
            continue
        # If the `services` and `version_specs` table are not aligned, it might
        # result in a None service status. In this case, the controller process
        # is not functioning as well and we should also use the
        # `_terminate_failed_services` function to clean up the service.
        # This is a safeguard for a rare case, that is accidentally abort
        # between `serve_state.add_service` and
        # `serve_state.add_or_update_version` in service.py.
        if (service_status is None or service_status['status']
                in serve_state.ServiceStatus.failed_statuses()):
            failed_status = (service_status['status']
                             if service_status is not None else None)
            if purge:
                message = _terminate_failed_services(service_name,
                                                     failed_status)
                if message is not None:
                    messages.append(message)
            else:
                messages.append(
                    f'{colorama.Fore.YELLOW}Service {service_name!r} is in '
                    f'failed status ({failed_status}). Skipping '
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
                with signal_file.open(mode='w', encoding='utf-8') as f:
                    f.write(UserSignal.TERMINATE.value)
                    f.flush()
        terminated_service_names.append(f'{service_name!r}')
    if not terminated_service_names:
        messages.append('No service to terminate.')
    else:
        identity_str = f'Service {terminated_service_names[0]} is'
        if len(terminated_service_names) > 1:
            terminated_service_names_str = ', '.join(terminated_service_names)
            identity_str = f'Services {terminated_service_names_str} are'
        messages.append(f'{identity_str} scheduled to be terminated.')
    return '\n'.join(messages)


def wait_service_registration(service_name: str, job_id: int) -> str:
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
    setup_completed = False
    while True:
        job_status = job_lib.get_status(job_id)
        if job_status is None or job_status < job_lib.JobStatus.RUNNING:
            # Wait for the controller process to finish setting up. It can be
            # slow if a lot cloud dependencies are being installed.
            if (time.time() - start_time >
                    constants.CONTROLLER_SETUP_TIMEOUT_SECONDS):
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        f'Failed to start the controller '
                        f'process for the service {service_name!r} '
                        f'within '
                        f'{constants.CONTROLLER_SETUP_TIMEOUT_SECONDS} seconds.'
                    )
            # No need to check the service status as the controller process
            # is still setting up.
            time.sleep(1)
            continue

        if not setup_completed:
            setup_completed = True
            # Reset the start time to wait for the service to be registered.
            start_time = time.time()

        record = serve_state.get_service_from_name(service_name)
        if record is not None:
            if job_id != record['controller_job_id']:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'The service {service_name!r} is already running. '
                        'Please specify a different name for your service. '
                        'To update an existing service, run: sky serve update '
                        f'{service_name} <new-service-yaml>')
            lb_port = record['load_balancer_port']
            if lb_port is not None:
                return message_utils.encode_payload(lb_port)
        elif len(serve_state.get_services()) >= get_num_service_threshold():
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Max number of services reached. '
                                   'To spin up more services, please '
                                   'tear down some existing services.')
        elapsed = time.time() - start_time
        if elapsed > constants.SERVICE_REGISTER_TIMEOUT_SECONDS:
            # Print the controller log to help user debug.
            controller_log_path = (
                generate_remote_controller_log_file_name(service_name))
            with open(os.path.expanduser(controller_log_path),
                      'r',
                      encoding='utf-8') as f:
                log_content = f.read()
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Failed to register service {service_name!r} '
                                 'on the SkyServe controller. '
                                 f'Reason:\n{log_content}')
        time.sleep(1)


def load_service_initialization_result(payload: str) -> int:
    return message_utils.decode_payload(payload)


def check_service_status_healthy(service_name: str) -> Optional[str]:
    service_record = serve_state.get_service_from_name(service_name)
    if service_record is None:
        return f'Service {service_name!r} does not exist.'
    if service_record['status'] == serve_state.ServiceStatus.CONTROLLER_INIT:
        return (f'Service {service_name!r} is still initializing its '
                'controller. Please try again later.')
    return None


def get_latest_version_with_min_replicas(
        service_name: str,
        replica_infos: List['replica_managers.ReplicaInfo']) -> Optional[int]:
    # Find the latest version with at least min_replicas replicas.
    version2count: DefaultDict[int, int] = collections.defaultdict(int)
    for info in replica_infos:
        if info.is_ready:
            version2count[info.version] += 1

    active_versions = sorted(version2count.keys(), reverse=True)
    for version in active_versions:
        spec = serve_state.get_spec(service_name, version)
        if (spec is not None and version2count[version] >= spec.min_replicas):
            return version
    # Use the oldest version if no version has enough replicas.
    return active_versions[-1] if active_versions else None


def _follow_logs_with_provision_expanding(
    file: TextIO,
    cluster_name: str,
    *,
    should_stop: Callable[[], bool],
    stop_on_eof: bool = False,
    idle_timeout_seconds: Optional[int] = None,
) -> Iterator[str]:
    """Follows logs and expands any provision.log references found.

    Args:
        file: Log file to read from.
        cluster_name: Name of the cluster being launched.
        should_stop: Callback that returns True when streaming should stop.
        stop_on_eof: If True, stop when reaching end of file.
        idle_timeout_seconds: If set, stop after these many seconds without
            new content.

    Yields:
        Log lines, including expanded content from referenced provision logs.
    """

    def cluster_is_up() -> bool:
        cluster_record = global_user_state.get_cluster_from_name(cluster_name)
        if cluster_record is None:
            return False
        return cluster_record['status'] == status_lib.ClusterStatus.UP

    def process_line(line: str) -> Iterator[str]:
        # The line might be directing users to view logs, like
        # `✓ Cluster launched: new-http.  View logs at: *.log`
        # We should tail the detailed logs for user.
        provision_log_prompt = re.match(_SKYPILOT_PROVISION_LOG_PATTERN, line)
        log_prompt = re.match(_SKYPILOT_LOG_PATTERN, line)

        if provision_log_prompt is not None:
            nested_log_path = os.path.expanduser(provision_log_prompt.group(1))

            try:
                with open(nested_log_path, 'r', newline='',
                          encoding='utf-8') as f:
                    # We still exit if more than 10 seconds without new content
                    # to avoid any internal bug that causes the launch to fail
                    # while cluster status remains INIT.
                    yield from log_utils.follow_logs(f,
                                                     should_stop=cluster_is_up,
                                                     stop_on_eof=stop_on_eof,
                                                     idle_timeout_seconds=10)
            except FileNotFoundError:
                yield line

                yield (f'{colorama.Fore.YELLOW}{colorama.Style.BRIGHT}'
                       f'Try to expand log file {nested_log_path} but not '
                       f'found. Skipping...{colorama.Style.RESET_ALL}')
                pass
            return

        if log_prompt is not None:
            # Now we skip other logs (file sync logs) since we lack
            # utility to determine when these log files are finished
            # writing.
            # TODO(tian): We should not skip these logs since there are
            # small chance that error will happen in file sync. Need to
            # find a better way to do this.
            return

        yield line

    return log_utils.follow_logs(file,
                                 should_stop=should_stop,
                                 stop_on_eof=stop_on_eof,
                                 process_line=process_line,
                                 idle_timeout_seconds=idle_timeout_seconds)


def stream_replica_logs(service_name: str, replica_id: int,
                        follow: bool) -> str:
    msg = check_service_status_healthy(service_name)
    if msg is not None:
        return msg
    print(f'{colorama.Fore.YELLOW}Start streaming logs for launching process '
          f'of replica {replica_id}.{colorama.Style.RESET_ALL}')

    log_file_name = generate_replica_log_file_name(service_name, replica_id)
    if os.path.exists(log_file_name):
        with open(log_file_name, 'r', encoding='utf-8') as f:
            print(f.read(), flush=True)
        return ''

    launch_log_file_name = generate_replica_launch_log_file_name(
        service_name, replica_id)
    if not os.path.exists(launch_log_file_name):
        return (f'{colorama.Fore.RED}Replica {replica_id} doesn\'t exist.'
                f'{colorama.Style.RESET_ALL}')

    replica_cluster_name = generate_replica_cluster_name(
        service_name, replica_id)

    def _get_replica_status() -> serve_state.ReplicaStatus:
        replica_info = serve_state.get_replica_infos(service_name)
        for info in replica_info:
            if info.replica_id == replica_id:
                return info.status
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                _FAILED_TO_FIND_REPLICA_MSG.format(replica_id=replica_id))

    replica_provisioned = (
        lambda: _get_replica_status() != serve_state.ReplicaStatus.PROVISIONING)
    with open(launch_log_file_name, 'r', newline='', encoding='utf-8') as f:
        for line in _follow_logs_with_provision_expanding(
                f,
                replica_cluster_name,
                should_stop=replica_provisioned,
                stop_on_eof=not follow,
        ):
            print(line, end='', flush=True)

    if (not follow and
            _get_replica_status() == serve_state.ReplicaStatus.PROVISIONING):
        # Early exit if not following the logs.
        return ''

    backend = backends.CloudVmRayBackend()
    handle = global_user_state.get_handle_from_cluster_name(
        replica_cluster_name)
    if handle is None:
        return _FAILED_TO_FIND_REPLICA_MSG.format(replica_id=replica_id)
    assert isinstance(handle, backends.CloudVmRayResourceHandle), handle

    # Notify user here to make sure user won't think the log is finished.
    print(f'{colorama.Fore.YELLOW}Start streaming logs for task job '
          f'of replica {replica_id}...{colorama.Style.RESET_ALL}')

    # Always tail the latest logs, which represent user setup & run.
    returncode = backend.tail_logs(handle, job_id=None, follow=follow)
    if returncode != 0:
        return (f'{colorama.Fore.RED}Failed to stream logs for replica '
                f'{replica_id}.{colorama.Style.RESET_ALL}')
    return ''


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

    with open(os.path.expanduser(log_file), 'r', newline='',
              encoding='utf-8') as f:
        for line in log_utils.follow_logs(
                f,
                should_stop=_service_is_terminal,
                stop_on_eof=not follow,
        ):
            print(line, end='', flush=True)
    return ''


# ================== Table Formatter for `sky serve status` ==================


def _get_replicas(service_record: Dict[str, Any]) -> str:
    ready_replica_num, total_replica_num = 0, 0
    for info in service_record['replica_info']:
        if info['status'] == serve_state.ReplicaStatus.READY:
            ready_replica_num += 1
        # TODO(MaoZiming): add a column showing failed replicas number.
        if info['status'] not in serve_state.ReplicaStatus.failed_statuses():
            total_replica_num += 1
    return f'{ready_replica_num}/{total_replica_num}'


def format_service_table(service_records: List[Dict[str, Any]],
                         show_all: bool) -> str:
    if not service_records:
        return 'No existing services.'

    service_columns = [
        'NAME', 'VERSION', 'UPTIME', 'STATUS', 'REPLICAS', 'ENDPOINT'
    ]
    if show_all:
        service_columns.extend([
            'AUTOSCALING_POLICY', 'LOAD_BALANCING_POLICY', 'REQUESTED_RESOURCES'
        ])
    service_table = log_utils.create_table(service_columns)

    replica_infos: List[Dict[str, Any]] = []
    for record in service_records:
        for replica in record['replica_info']:
            replica['service_name'] = record['name']
            replica_infos.append(replica)

        service_name = record['name']
        version = ','.join(
            str(v) for v in record['active_versions']
        ) if 'active_versions' in record and record['active_versions'] else '-'
        uptime = log_utils.readable_time_duration(record['uptime'],
                                                  absolute=True)
        service_status = record['status']
        status_str = service_status.colored_str()
        replicas = _get_replicas(record)
        endpoint = record['endpoint']
        if endpoint is None:
            endpoint = '-'
        policy = record['policy']
        requested_resources_str = record['requested_resources_str']
        load_balancing_policy = record['load_balancing_policy']

        service_values = [
            service_name,
            version,
            uptime,
            status_str,
            replicas,
            endpoint,
        ]
        if show_all:
            service_values.extend(
                [policy, load_balancing_policy, requested_resources_str])
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
        'SERVICE_NAME', 'ID', 'VERSION', 'ENDPOINT', 'LAUNCHED', 'RESOURCES',
        'STATUS', 'REGION'
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
        endpoint = record.get('endpoint', '-')
        service_name = record['service_name']
        replica_id = record['replica_id']
        version = (record['version'] if 'version' in record else '-')
        replica_endpoint = endpoint if endpoint else '-'
        launched_at = log_utils.readable_time_duration(record['launched_at'])
        resources_str = '-'
        replica_status = record['status']
        status_str = replica_status.colored_str()
        region = '-'
        zone = '-'

        replica_handle: Optional['backends.CloudVmRayResourceHandle'] = record[
            'handle']
        if replica_handle is not None:
            resources_str = resources_utils.get_readable_resources_repr(
                replica_handle, simplify=not show_all)
            if replica_handle.launched_resources.region is not None:
                region = replica_handle.launched_resources.region
            if replica_handle.launched_resources.zone is not None:
                zone = replica_handle.launched_resources.zone

        replica_values = [
            service_name,
            replica_id,
            version,
            replica_endpoint,
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

    # TODO(zhwu): When any API is changed, we should update the
    # constants.SERVE_VERSION.
    _PREFIX = [
        'from sky.serve import serve_state',
        'from sky.serve import serve_utils',
        'from sky.serve import constants',
    ]

    @classmethod
    def get_service_status(cls, service_names: Optional[List[str]]) -> str:
        code = [
            f'msg = serve_utils.get_service_status_encoded({service_names!r})',
            'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def add_version(cls, service_name: str) -> str:
        code = [
            f'msg = serve_utils.add_version_encoded({service_name!r})',
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
    def terminate_replica(cls, service_name: str, replica_id: int,
                          purge: bool) -> str:
        code = [
            f'(lambda: print(serve_utils.terminate_replica({service_name!r}, '
            f'{replica_id}, {purge}), end="", flush=True) '
            'if getattr(constants, "SERVE_VERSION", 0) >= 2 else '
            f'exec("raise RuntimeError('
            f'{constants.TERMINATE_REPLICA_VERSION_MISMATCH_ERROR!r})"))()'
        ]
        return cls._build(code)

    @classmethod
    def wait_service_registration(cls, service_name: str, job_id: int) -> str:
        code = [
            'msg = serve_utils.wait_service_registration('
            f'{service_name!r}, {job_id})', 'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def stream_replica_logs(cls, service_name: str, replica_id: int,
                            follow: bool) -> str:
        code = [
            'msg = serve_utils.stream_replica_logs('
            f'{service_name!r}, {replica_id!r}, follow={follow})',
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
        # Use the local user id to make sure the operation goes to the correct
        # user.
        return (f'export {skylet_constants.USER_ID_ENV_VAR}='
                f'"{common_utils.get_user_hash()}"; '
                f'{skylet_constants.SKY_PYTHON_CMD} '
                f'-u -c {shlex.quote(generated_code)}')

    @classmethod
    def update_service(cls, service_name: str, version: int, mode: str) -> str:
        code = [
            f'msg = serve_utils.update_service_encoded({service_name!r}, '
            f'{version}, mode={mode!r})',
            'print(msg, end="", flush=True)',
        ]
        return cls._build(code)
