"""User interface with the SkyServe."""
import base64
import collections
import dataclasses
import datetime
import enum
import os
import pathlib
import pickle
import re
import shlex
import shutil
import threading
import time
import traceback
import typing
from typing import (Any, Callable, DefaultDict, Deque, Dict, Generic, Iterator,
                    List, Optional, TextIO, Type, TypeVar, Union)
import uuid

import colorama
import filelock

from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.jobs import state as managed_job_state
from sky.serve import constants
from sky.serve import serve_state
from sky.serve import spot_placer
from sky.skylet import constants as skylet_constants
from sky.skylet import job_lib
from sky.utils import annotations
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import log_utils
from sky.utils import message_utils
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import ux_utils
from sky.utils import yaml_utils

if typing.TYPE_CHECKING:
    import fastapi
    import psutil
    import requests

    import sky
    from sky.serve import replica_managers
else:
    psutil = adaptors_common.LazyImport('psutil')
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)

_CONTROLLER_URL = 'http://localhost:{CONTROLLER_PORT}'

# NOTE(dev): We assume log are print with the hint 'sky api logs -l'. Be careful
# when changing UX as this assumption is used to expand some log files while
# ignoring others.
_SKYPILOT_LOG_HINT = r'.*sky api logs -l'
_SKYPILOT_PROVISION_API_LOG_PATTERN = (
    fr'{_SKYPILOT_LOG_HINT} (.*/provision\.log)')
# New hint pattern for provision logs
_SKYPILOT_PROVISION_LOG_CMD_PATTERN = r'.*sky logs --provision\s+(\S+)'
_SKYPILOT_LOG_PATTERN = fr'{_SKYPILOT_LOG_HINT} (.*\.log)'

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


@dataclasses.dataclass
class ServiceComponentTarget:
    """Represents a target service component with an optional replica ID.
    """
    component: ServiceComponent
    replica_id: Optional[int] = None

    def __init__(self,
                 component: Union[str, ServiceComponent],
                 replica_id: Optional[int] = None):
        if isinstance(component, str):
            component = ServiceComponent(component)
        self.component = component
        self.replica_id = replica_id

    def __post_init__(self):
        """Validate that replica_id is only provided for REPLICA component."""
        if (self.component
                == ServiceComponent.REPLICA) != (self.replica_id is None):
            raise ValueError(
                'replica_id must be specified if and only if component is '
                'REPLICA.')

    def __hash__(self) -> int:
        return hash((self.component, self.replica_id))

    def __str__(self) -> str:
        if self.component == ServiceComponent.REPLICA:
            return f'{self.component.value}-{self.replica_id}'
        return self.component.value


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


def get_service_filelock_path(pool: str) -> str:
    path = (pathlib.Path(constants.SKYSERVE_METADATA_DIR) / pool /
            'pool.lock').expanduser().absolute()
    path.parents[0].mkdir(parents=True, exist_ok=True)
    return str(path)


def _validate_consolidation_mode_config(current_is_consolidation_mode: bool,
                                        pool: bool) -> None:
    """Validate the consolidation mode config."""
    # Check whether the consolidation mode config is changed.
    controller = controller_utils.get_controller_for_pool(pool).value
    if current_is_consolidation_mode:
        controller_cn = controller.cluster_name
        if global_user_state.cluster_with_name_exists(controller_cn):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.InconsistentConsolidationModeError(
                    f'{colorama.Fore.RED}Consolidation mode for '
                    f'{controller.controller_type} is enabled, but the '
                    f'controller cluster {controller_cn} is still running. '
                    'Please terminate the controller cluster first.'
                    f'{colorama.Style.RESET_ALL}')
    else:
        noun = 'pool' if pool else 'service'
        all_services = [
            svc for svc in serve_state.get_services() if svc['pool'] == pool
        ]
        if all_services:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.InconsistentConsolidationModeError(
                    f'{colorama.Fore.RED}Consolidation mode for '
                    f'{controller.controller_type} is disabled, but there are '
                    f'still {len(all_services)} {noun}s running. Please '
                    f'terminate those {noun}s first.{colorama.Style.RESET_ALL}')


@annotations.lru_cache(scope='request', maxsize=1)
def is_consolidation_mode(pool: bool = False) -> bool:
    # Use jobs config for pool consolidation mode.
    controller = controller_utils.get_controller_for_pool(pool).value
    consolidation_mode = skypilot_config.get_nested(
        (controller.controller_type, 'controller', 'consolidation_mode'),
        default_value=False)
    # We should only do this check on API server, as the controller will not
    # have related config and will always seemingly disabled for consolidation
    # mode. Check #6611 for more details.
    if (os.environ.get(skylet_constants.OVERRIDE_CONSOLIDATION_MODE) is not None
            and controller.controller_type == 'jobs'):
        # if we are in the job controller, we must always be in consolidation
        # mode.
        return True
    if os.environ.get(skylet_constants.ENV_VAR_IS_SKYPILOT_SERVER) is not None:
        _validate_consolidation_mode_config(consolidation_mode, pool)
    return consolidation_mode


def ha_recovery_for_consolidation_mode(pool: bool):
    """Recovery logic for HA mode."""
    # No setup recovery is needed in consolidation mode, as the API server
    # already has all runtime installed. Directly start jobs recovery here.
    # Refers to sky/templates/kubernetes-ray.yml.j2 for more details.
    runner = command_runner.LocalProcessCommandRunner()
    noun = 'pool' if pool else 'serve'
    capnoun = noun.capitalize()
    prefix = f'{noun}_'
    with open(skylet_constants.HA_PERSISTENT_RECOVERY_LOG_PATH.format(prefix),
              'w',
              encoding='utf-8') as f:
        start = time.time()
        f.write(f'Starting HA recovery at {datetime.datetime.now()}\n')
        for service_name in serve_state.get_glob_service_names(None):
            svc = _get_service_status(service_name,
                                      pool=pool,
                                      with_replica_info=False)
            if svc is None:
                continue
            controller_pid = svc['controller_pid']
            if controller_pid is not None:
                try:
                    if _controller_process_alive(controller_pid, service_name):
                        f.write(f'Controller pid {controller_pid} for '
                                f'{noun} {service_name} is still running. '
                                'Skipping recovery.\n')
                        continue
                except Exception:  # pylint: disable=broad-except
                    # _controller_process_alive may raise if psutil fails; we
                    # should not crash the recovery logic because of this.
                    f.write('Error checking controller pid '
                            f'{controller_pid} for {noun} {service_name}\n')

            script = serve_state.get_ha_recovery_script(service_name)
            if script is None:
                f.write(f'{capnoun} {service_name}\'s recovery script does '
                        'not exist. Skipping recovery.\n')
                continue
            rc, out, err = runner.run(script, require_outputs=True)
            if rc:
                f.write(f'Recovery script returned {rc}. '
                        f'Output: {out}\nError: {err}\n')
            f.write(f'{capnoun} {service_name} completed recovery at '
                    f'{datetime.datetime.now()}\n')
        f.write(f'HA recovery completed at {datetime.datetime.now()}\n')
        f.write(f'Total recovery time: {time.time() - start} seconds\n')


def _controller_process_alive(pid: int, service_name: str) -> bool:
    """Check if the controller process is alive."""
    try:
        process = psutil.Process(pid)
        cmd_str = ' '.join(process.cmdline())
        return process.is_running(
        ) and f'--service-name {service_name}' in cmd_str
    except psutil.NoSuchProcess:
        return False


def validate_service_task(task: 'sky.Task', pool: bool) -> None:
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

    field_name = 'service' if not pool else 'pool'
    if task.service is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'{field_name.capitalize()} section not found.')

    if pool != task.service.pool:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'{field_name.capitalize()} section in the YAML '
                             f'file does not match the pool argument. '
                             f'To fix, add a valid `{field_name}` field.')

    policy_description = ('on-demand'
                          if task.service.dynamic_ondemand_fallback else 'spot')
    for resource in list(task.resources):
        if resource.job_recovery is not None:
            sys_name = 'SkyServe' if not pool else 'Pool'
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'job_recovery is disabled for {sys_name}. '
                                 f'{sys_name} will replenish preempted spot '
                                 f'with {policy_description} instances.')

    if pool:
        accelerators = set()
        for resource in task.resources:
            if resource.accelerators is not None:
                if isinstance(resource.accelerators, str):
                    accelerators.add(resource.accelerators)
                elif isinstance(resource.accelerators, dict):
                    accelerators.update(resource.accelerators.keys())
                elif isinstance(resource.accelerators, list):
                    accelerators.update(resource.accelerators)
        if len(accelerators) > 1:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Heterogeneous clusters are not supported for '
                                 'pools please specify one accelerator '
                                 'for all workers.')

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
        if not pool and task.service.ports is None:
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
        if pool:
            if (task.service.ports is not None or
                    requested_resources.ports is not None):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('Cannot specify ports in a pool.')


def generate_service_name(pool: bool = False):
    noun = 'pool' if pool else 'service'
    return f'sky-{noun}-{uuid.uuid4().hex[:4]}'


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
    # NOTE(dev): This format is used in sky/serve/service.py::_cleanup, for
    # checking replica cluster existence. Be careful when changing it.
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


def update_service_status(pool: bool) -> None:
    noun = 'pool' if pool else 'serve'
    capnoun = noun.capitalize()
    service_names = serve_state.get_glob_service_names(None)
    for service_name in service_names:
        record = _get_service_status(service_name,
                                     pool=pool,
                                     with_replica_info=False)
        if record is None:
            continue
        service_status = record['status']
        if service_status == serve_state.ServiceStatus.SHUTTING_DOWN:
            # Skip services that is shutting down.
            continue

        logger.info(f'Update {noun} status for {service_name!r} '
                    f'with status {service_status}')

        controller_pid = record['controller_pid']
        if controller_pid is None:
            logger.info(f'{capnoun} {service_name!r} controller pid is None. '
                        f'Unexpected status {service_status}. Set to failure.')
        elif controller_pid < 0:
            # Backwards compatibility: this service was submitted when ray was
            # still used for controller process management. We set the
            # value_to_replace_existing_entries to -1 to indicate historical
            # services.
            # TODO(tian): Remove before 0.13.0.
            controller_job_id = record['controller_job_id']
            assert controller_job_id is not None
            controller_status = job_lib.get_status(controller_job_id)
            if (controller_status is not None and
                    not controller_status.is_terminal()):
                continue
            logger.info(f'Updating {noun} {service_name!r} in old version. '
                        f'SkyPilot job status: {controller_status}. '
                        'Set to failure.')
        else:
            if _controller_process_alive(controller_pid, service_name):
                # The controller is still running.
                continue
            logger.info(f'{capnoun} {service_name!r} controller pid '
                        f'{controller_pid} is not alive. Set to failure.')

        # If controller job is not running, set it as controller failed.
        serve_state.set_service_status_and_active_versions(
            service_name, serve_state.ServiceStatus.CONTROLLER_FAILED)


def update_service_encoded(service_name: str, version: int, mode: str,
                           pool: bool) -> str:
    noun = 'pool' if pool else 'service'
    capnoun = noun.capitalize()
    service_status = _get_service_status(service_name, pool=pool)
    if service_status is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'{capnoun} {service_name!r} does not exist.')
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
            # This only happens for services since pool is added after the
            # update feature is introduced.
            raise ValueError(
                'The service is up-ed in an old version and does not '
                'support update. Please `sky serve down` '
                'it first and relaunch the service. ')
    elif resp.status_code == 400:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Client error during {noun} update: {resp.text}')
    elif resp.status_code == 500:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Server error during {noun} update: {resp.text}')
    elif resp.status_code != 200:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Failed to update {noun}: {resp.text}')

    service_msg = resp.json()['message']
    return message_utils.encode_payload(service_msg)


def terminate_replica(service_name: str, replica_id: int, purge: bool) -> str:
    # TODO(tian): Currently pool does not support terminating replica.
    service_status = _get_service_status(service_name, pool=False)
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


def get_yaml_content(service_name: str, version: int) -> str:
    yaml_content = serve_state.get_yaml_content(service_name, version)
    if yaml_content is not None:
        return yaml_content
    # Backward compatibility for old service records that
    # does not dump the yaml content to version database.
    # TODO(tian): Remove this after 2 minor releases, i.e. 0.13.0.
    latest_yaml_path = generate_task_yaml_file_name(service_name, version)
    with open(latest_yaml_path, 'r', encoding='utf-8') as f:
        return f.read()


def _get_service_status(
        service_name: str,
        pool: bool,
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
    if record['pool'] != pool:
        return None

    record['pool_yaml'] = ''
    if record['pool']:
        version = record['version']
        try:
            yaml_content = get_yaml_content(service_name, version)
            raw_yaml_config = yaml_utils.read_yaml_str(yaml_content)
        except Exception as e:  # pylint: disable=broad-except
            # If this is a consolidation mode running without an PVC, the file
            # might lost after an API server update (restart). In such case, we
            # don't want it to crash the command. Fall back to an empty string.
            logger.error(f'Failed to read YAML for service {service_name} '
                         f'with version {version}: {e}')
            record['pool_yaml'] = ''
        else:
            original_config = raw_yaml_config.get('_user_specified_yaml')
            if original_config is None:
                # Fall back to old display format.
                original_config = raw_yaml_config
                original_config.pop('run', None)
                svc: Dict[str, Any] = original_config.pop('service')
                if svc is not None:
                    svc.pop('pool', None)  # Remove pool from service config
                    original_config['pool'] = svc  # Add pool to root config
            else:
                original_config = yaml_utils.safe_load(original_config)
            record['pool_yaml'] = yaml_utils.dump_yaml_str(original_config)

    record['target_num_replicas'] = 0
    try:
        controller_port = record['controller_port']
        resp = requests.get(
            _CONTROLLER_URL.format(CONTROLLER_PORT=controller_port) +
            '/autoscaler/info')
        record['target_num_replicas'] = resp.json()['target_num_replicas']
    except requests.exceptions.RequestException:
        record['target_num_replicas'] = None
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Failed to get autoscaler info for {service_name}: '
                     f'{common_utils.format_exception(e)}\n'
                     f'Traceback: {traceback.format_exc()}')

    if with_replica_info:
        record['replica_info'] = [
            info.to_info_dict(with_handle=True, with_url=not pool)
            for info in serve_state.get_replica_infos(service_name)
        ]
        if pool:
            for replica_info in record['replica_info']:
                job_ids = managed_job_state.get_nonterminal_job_ids_by_pool(
                    service_name, replica_info['name'])
                replica_info['used_by'] = job_ids[0] if job_ids else None
    return record


def get_service_status_pickled(service_names: Optional[List[str]],
                               pool: bool) -> List[Dict[str, str]]:
    service_statuses: List[Dict[str, str]] = []
    if service_names is None:
        # Get all service names
        service_names = serve_state.get_glob_service_names(None)
    for service_name in service_names:
        service_status = _get_service_status(service_name, pool=pool)
        if service_status is None:
            continue
        service_statuses.append({
            k: base64.b64encode(pickle.dumps(v)).decode('utf-8')
            for k, v in service_status.items()
        })
    return sorted(service_statuses, key=lambda x: x['name'])


# TODO (kyuds): remove when serve codegen is removed
def get_service_status_encoded(service_names: Optional[List[str]],
                               pool: bool) -> str:
    # We have to use payload_type here to avoid the issue of
    # message_utils.decode_payload() not being able to correctly decode the
    # message with <sky-payload> tags.
    service_statuses = get_service_status_pickled(service_names, pool)
    return message_utils.encode_payload(service_statuses,
                                        payload_type='service_status')


def unpickle_service_status(
        payload: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    service_statuses: List[Dict[str, Any]] = []
    for service_status in payload:
        if not isinstance(service_status, dict):
            raise ValueError(f'Invalid service status: {service_status}')
        service_statuses.append({
            k: pickle.loads(base64.b64decode(v))
            for k, v in service_status.items()
        })
    return service_statuses


# TODO (kyuds): remove when serve codegen is removed
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
    return unpickle_service_status(service_statuses_encoded)


# TODO (kyuds): remove when serve codegen is removed
def add_version_encoded(service_name: str) -> str:
    new_version = serve_state.add_version(service_name)
    return message_utils.encode_payload(new_version)


# TODO (kyuds): remove when serve codegen is removed
def load_version_string(payload: str) -> str:
    return message_utils.decode_payload(payload)


def get_ready_replicas(
        service_name: str) -> List['replica_managers.ReplicaInfo']:
    logger.info(f'Get number of replicas for pool {service_name!r}')
    return [
        info for info in serve_state.get_replica_infos(service_name)
        if info.status == serve_state.ReplicaStatus.READY
    ]


def get_next_cluster_name(service_name: str, job_id: int) -> Optional[str]:
    """Get the next available cluster name from idle replicas.

    Args:
        service_name: The name of the service.
        job_id: Optional job ID to associate with the acquired cluster.
                If None, a placeholder will be used.

    Returns:
        The cluster name if an idle replica is found, None otherwise.
    """
    # Check if service exists
    service_status = _get_service_status(service_name,
                                         pool=True,
                                         with_replica_info=False)
    if service_status is None:
        logger.error(f'Service {service_name!r} does not exist.')
        return None
    if not service_status['pool']:
        logger.error(f'Service {service_name!r} is not a pool.')
        return None
    with filelock.FileLock(get_service_filelock_path(service_name)):
        logger.debug(f'Get next cluster name for pool {service_name!r}')
        ready_replicas = get_ready_replicas(service_name)
        idle_replicas: List['replica_managers.ReplicaInfo'] = []
        for replica_info in ready_replicas:
            jobs_on_replica = managed_job_state.get_nonterminal_job_ids_by_pool(
                service_name, replica_info.cluster_name)
            # TODO(tian): Make it resources aware. Currently we allow and only
            # allow one job per replica. In the following PR, we should:
            #  i) When the replica is launched with `any_of` resources (
            #     replicas can have different resources), we should check if
            #     the resources that jobs require are available on the replica.
            #     e.g., if a job requires A100:1 on a {L4:1, A100:1} pool, it
            #     should only goes to replica with A100.
            # ii) When a job only requires a subset of the resources on the
            #     replica, each replica should be able to handle multiple jobs
            #     at the same time. e.g., if a job requires A100:1 on a A100:8
            #     pool, it should be able to run 4 jobs at the same time.
            if not jobs_on_replica:
                idle_replicas.append(replica_info)
        if not idle_replicas:
            logger.info(f'No idle replicas found for pool {service_name!r}')
            return None

        # Select the first idle replica.
        # TODO(tian): "Load balancing" policy.
        replica_info = idle_replicas[0]
        logger.info(f'Selected replica {replica_info.replica_id} with cluster '
                    f'{replica_info.cluster_name!r} for job {job_id!r} in pool '
                    f'{service_name!r}')
        managed_job_state.set_current_cluster_name(job_id,
                                                   replica_info.cluster_name)
        return replica_info.cluster_name


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
        if global_user_state.cluster_with_name_exists(
                replica_info.cluster_name):
            remaining_replica_clusters.append(f'{replica_info.cluster_name!r}')
        serve_state.remove_replica(service_name, replica_info.replica_id)

    service_dir = os.path.expanduser(
        generate_remote_service_dir_name(service_name))
    shutil.rmtree(service_dir)
    serve_state.remove_service(service_name)
    serve_state.delete_all_versions(service_name)
    serve_state.remove_ha_recovery_script(service_name)

    if not remaining_replica_clusters:
        return None
    # TODO(tian): Try to terminate those replica clusters.
    remaining_identity = ', '.join(remaining_replica_clusters)
    return (f'{colorama.Fore.YELLOW}terminate service {service_name!r} with '
            f'failed status ({service_status}). This may indicate a resource '
            'leak. Please check the following SkyPilot clusters on the '
            f'controller: {remaining_identity}{colorama.Style.RESET_ALL}')


def terminate_services(service_names: Optional[List[str]], purge: bool,
                       pool: bool) -> str:
    noun = 'pool' if pool else 'service'
    capnoun = noun.capitalize()
    service_names = serve_state.get_glob_service_names(service_names)
    terminated_service_names: List[str] = []
    messages: List[str] = []
    for service_name in service_names:
        service_status = _get_service_status(service_name,
                                             pool=pool,
                                             with_replica_info=False)
        if service_status is None:
            continue
        if (service_status is not None and service_status['status']
                == serve_state.ServiceStatus.SHUTTING_DOWN):
            # Already scheduled to be terminated.
            continue
        if pool:
            nonterminal_job_ids = (
                managed_job_state.get_nonterminal_job_ids_by_pool(service_name))
            if nonterminal_job_ids:
                nonterminal_job_ids_str = ','.join(
                    str(job_id) for job_id in nonterminal_job_ids)
                num_nonterminal_jobs = len(nonterminal_job_ids)
                messages.append(
                    f'{colorama.Fore.YELLOW}{capnoun} {service_name!r} has '
                    f'{num_nonterminal_jobs} nonterminal jobs: '
                    f'{nonterminal_job_ids_str}. To terminate the {noun}, '
                    f'please run `sky jobs cancel --pool {service_name}` to '
                    'cancel all jobs in the pool first.'
                    f'{colorama.Style.RESET_ALL}')
                continue
        # If the `services` and `version_specs` table are not aligned, it might
        # result in a None service status. In this case, the controller process
        # is not functioning as well and we should also use the
        # `_terminate_failed_services` function to clean up the service.
        # This is a safeguard for a rare case, that is accidentally abort
        # between `serve_state.add_service` and
        # `serve_state.add_or_update_version` in service.py.
        purge_cmd = (f'sky jobs pool down {service_name} --purge'
                     if pool else f'sky serve down {service_name} --purge')
        if (service_status['status']
                in serve_state.ServiceStatus.failed_statuses()):
            failed_status = service_status['status']
            if purge:
                message = _terminate_failed_services(service_name,
                                                     failed_status)
                if message is not None:
                    messages.append(message)
            else:
                messages.append(
                    f'{colorama.Fore.YELLOW}{capnoun} {service_name!r} is in '
                    f'failed status ({failed_status}). Skipping '
                    'its termination as it could lead to a resource leak. '
                    f'(Use `{purge_cmd}` to forcefully terminate the {noun}.)'
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
        messages.append(f'No {noun} to terminate.')
    else:
        identity_str = f'{capnoun} {terminated_service_names[0]} is'
        if len(terminated_service_names) > 1:
            terminated_service_names_str = ', '.join(terminated_service_names)
            identity_str = f'{capnoun}s {terminated_service_names_str} are'
        messages.append(f'{identity_str} scheduled to be terminated.')
    return '\n'.join(messages)


def wait_service_registration(service_name: str, job_id: int,
                              pool: bool) -> str:
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
    # TODO (kyuds): when codegen is fully deprecated, return the lb port
    # as an int directly instead of encoding it.
    start_time = time.time()
    setup_completed = False
    noun = 'pool' if pool else 'service'
    while True:
        # Only do this check for non-consolidation mode as consolidation mode
        # has no setup process.
        if not is_consolidation_mode(pool):
            job_status = job_lib.get_status(job_id)
            if job_status is None or job_status < job_lib.JobStatus.RUNNING:
                # Wait for the controller process to finish setting up. It
                # can be slow if a lot cloud dependencies are being installed.
                if (time.time() - start_time >
                        constants.CONTROLLER_SETUP_TIMEOUT_SECONDS):
                    with ux_utils.print_exception_no_traceback():
                        raise RuntimeError(
                            f'Failed to start the controller process for '
                            f'the {noun} {service_name!r} within '
                            f'{constants.CONTROLLER_SETUP_TIMEOUT_SECONDS}'
                            f' seconds.')
                # No need to check the service status as the controller process
                # is still setting up.
                time.sleep(1)
                continue

        if not setup_completed:
            setup_completed = True
            # Reset the start time to wait for the service to be registered.
            start_time = time.time()

        record = _get_service_status(service_name,
                                     pool=pool,
                                     with_replica_info=False)
        if record is not None:
            if job_id != record['controller_job_id']:
                if pool:
                    command_to_run = 'sky jobs pool apply --pool'
                else:
                    command_to_run = 'sky serve update'
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'The {noun} {service_name!r} is already running. '
                        f'Please specify a different name for your {noun}. '
                        f'To update an existing {noun}, run: {command_to_run}'
                        f' {service_name} <new-{noun}-yaml>')
            lb_port = record['load_balancer_port']
            if lb_port is not None:
                return message_utils.encode_payload(lb_port)
        else:
            controller_log_path = os.path.expanduser(
                generate_remote_controller_log_file_name(service_name))
            if os.path.exists(controller_log_path):
                with open(controller_log_path, 'r', encoding='utf-8') as f:
                    log_content = f.read()
                if (constants.MAX_NUMBER_OF_SERVICES_REACHED_ERROR
                        in log_content):
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


def _check_service_status_healthy(service_name: str,
                                  pool: bool) -> Optional[str]:
    service_record = _get_service_status(service_name,
                                         pool,
                                         with_replica_info=False)
    capnoun = 'Service' if not pool else 'Pool'
    if service_record is None:
        return f'{capnoun} {service_name!r} does not exist.'
    if service_record['status'] == serve_state.ServiceStatus.CONTROLLER_INIT:
        return (f'{capnoun} {service_name!r} is still initializing its '
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


def _process_line(line: str,
                  cluster_name: str,
                  stop_on_eof: bool = False) -> Iterator[str]:
    # The line might be directing users to view logs, like
    # `✓ Cluster launched: new-http.  View logs at: *.log`
    # We should tail the detailed logs for user.
    def cluster_is_up() -> bool:
        status = global_user_state.get_status_from_cluster_name(cluster_name)
        return status == status_lib.ClusterStatus.UP

    provision_api_log_prompt = re.match(_SKYPILOT_PROVISION_API_LOG_PATTERN,
                                        line)
    provision_log_cmd_prompt = re.match(_SKYPILOT_PROVISION_LOG_CMD_PATTERN,
                                        line)
    log_prompt = re.match(_SKYPILOT_LOG_PATTERN, line)

    def _stream_provision_path(p: pathlib.Path) -> Iterator[str]:
        try:
            with open(p, 'r', newline='', encoding='utf-8') as f:
                # Exit if >10s without new content to avoid hanging when INIT
                yield from log_utils.follow_logs(f,
                                                 should_stop=cluster_is_up,
                                                 stop_on_eof=stop_on_eof,
                                                 idle_timeout_seconds=10)
        except FileNotFoundError:
            # Fall back cleanly if the hinted path doesn't exist
            yield line
            yield (f'{colorama.Fore.YELLOW}{colorama.Style.BRIGHT}'
                   f'Try to expand log file {p} but not found. Skipping...'
                   f'{colorama.Style.RESET_ALL}')
        return

    if provision_api_log_prompt is not None:
        rel_path = provision_api_log_prompt.group(1)
        nested_log_path = pathlib.Path(
            skylet_constants.SKY_LOGS_DIRECTORY).expanduser().joinpath(
                rel_path).resolve()
        yield from _stream_provision_path(nested_log_path)
        return

    if provision_log_cmd_prompt is not None:
        # Resolve provision log via cluster table first, then history.
        log_path_str = global_user_state.get_cluster_provision_log_path(
            cluster_name)
        if not log_path_str:
            log_path_str = (
                global_user_state.get_cluster_history_provision_log_path(
                    cluster_name))
        if not log_path_str:
            yield line
            return
        yield from _stream_provision_path(
            pathlib.Path(log_path_str).expanduser().resolve())
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

    def process_line(line: str) -> Iterator[str]:
        yield from _process_line(line, cluster_name, stop_on_eof=stop_on_eof)

    return log_utils.follow_logs(file,
                                 should_stop=should_stop,
                                 stop_on_eof=stop_on_eof,
                                 process_line=process_line,
                                 idle_timeout_seconds=idle_timeout_seconds)


def _capped_follow_logs_with_provision_expanding(
    log_list: List[str],
    cluster_name: str,
    *,
    line_cap: int = 100,
) -> Iterator[str]:
    """Follows logs and expands any provision.log references found.

    Args:
        log_list: List of Log Lines to read from.
        cluster_name: Name of the cluster being launched.
        line_cap: Number of last lines to return

    Yields:
        Log lines, including expanded content from referenced provision logs.
    """
    all_lines: Deque[str] = collections.deque(maxlen=line_cap)

    for line in log_list:
        for processed in _process_line(line=line,
                                       cluster_name=cluster_name,
                                       stop_on_eof=False):
            all_lines.append(processed)

    yield from all_lines


def stream_replica_logs(service_name: str, replica_id: int, follow: bool,
                        tail: Optional[int], pool: bool) -> str:
    msg = _check_service_status_healthy(service_name, pool=pool)
    if msg is not None:
        return msg
    repnoun = 'worker' if pool else 'replica'
    caprepnoun = repnoun.capitalize()
    print(f'{colorama.Fore.YELLOW}Start streaming logs for launching process '
          f'of {repnoun} {replica_id}.{colorama.Style.RESET_ALL}')
    log_file_name = generate_replica_log_file_name(service_name, replica_id)
    if os.path.exists(log_file_name):
        if tail is not None:
            lines = common_utils.read_last_n_lines(log_file_name, tail)
            for line in lines:
                if not line.endswith('\n'):
                    line += '\n'
                print(line, end='', flush=True)
        else:
            with open(log_file_name, 'r', encoding='utf-8') as f:
                print(f.read(), flush=True)
        return ''

    launch_log_file_name = generate_replica_launch_log_file_name(
        service_name, replica_id)
    if not os.path.exists(launch_log_file_name):
        return (f'{colorama.Fore.RED}{caprepnoun} {replica_id} doesn\'t exist.'
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

    # Handle launch logs based on number parameter
    final_lines_to_print = []
    if tail is not None:
        static_lines = common_utils.read_last_n_lines(launch_log_file_name,
                                                      tail)
        lines = list(
            _capped_follow_logs_with_provision_expanding(
                log_list=static_lines,
                cluster_name=replica_cluster_name,
                line_cap=tail,
            ))
        final_lines_to_print += lines
    else:
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
        if tail is not None:
            for line in final_lines_to_print:
                if not line.endswith('\n'):
                    line += '\n'
                print(line, end='', flush=True)
        return ''

    backend = backends.CloudVmRayBackend()
    handle = global_user_state.get_handle_from_cluster_name(
        replica_cluster_name)
    if handle is None:
        if tail is not None:
            for line in final_lines_to_print:
                if not line.endswith('\n'):
                    line += '\n'
                print(line, end='', flush=True)
        return _FAILED_TO_FIND_REPLICA_MSG.format(replica_id=replica_id)
    assert isinstance(handle, backends.CloudVmRayResourceHandle), handle

    # Notify user here to make sure user won't think the log is finished.
    print(f'{colorama.Fore.YELLOW}Start streaming logs for task job '
          f'of {repnoun} {replica_id}...{colorama.Style.RESET_ALL}')

    # Always tail the latest logs, which represent user setup & run.
    if tail is None:
        returncode = backend.tail_logs(handle, job_id=None, follow=follow)
        if returncode != 0:
            return (f'{colorama.Fore.RED}Failed to stream logs for {repnoun} '
                    f'{replica_id}.{colorama.Style.RESET_ALL}')
    elif not follow and tail > 0:
        final = backend.tail_logs(handle,
                                  job_id=None,
                                  follow=follow,
                                  tail=tail,
                                  stream_logs=False,
                                  require_outputs=True,
                                  process_stream=True)
        if isinstance(final, int) or (final[0] != 0 and final[0] != 101):
            if tail is not None:
                for line in final_lines_to_print:
                    if not line.endswith('\n'):
                        line += '\n'
                    print(line, end='', flush=True)
            return (f'{colorama.Fore.RED}Failed to stream logs for replica '
                    f'{replica_id}.{colorama.Style.RESET_ALL}')
        final_lines_to_print += final[1].splitlines()
        for line in final_lines_to_print[-tail:]:
            if not line.endswith('\n'):
                line += '\n'
            print(line, end='', flush=True)
    return ''


def stream_serve_process_logs(service_name: str, stream_controller: bool,
                              follow: bool, tail: Optional[int],
                              pool: bool) -> str:
    msg = _check_service_status_healthy(service_name, pool)
    if msg is not None:
        return msg
    if stream_controller:
        log_file = generate_remote_controller_log_file_name(service_name)
    else:
        log_file = generate_remote_load_balancer_log_file_name(service_name)

    def _service_is_terminal() -> bool:
        record = _get_service_status(service_name,
                                     pool,
                                     with_replica_info=False)
        if record is None:
            return True
        return record['status'] in serve_state.ServiceStatus.failed_statuses()

    if tail is not None:
        lines = common_utils.read_last_n_lines(os.path.expanduser(log_file),
                                               tail)
        for line in lines:
            if not line.endswith('\n'):
                line += '\n'
            print(line, end='', flush=True)
    else:
        with open(os.path.expanduser(log_file),
                  'r',
                  newline='',
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


def format_service_table(service_records: List[Dict[str, Any]], show_all: bool,
                         pool: bool) -> str:
    noun = 'pool' if pool else 'service'
    if not service_records:
        return f'No existing {noun}s.'

    service_columns = [
        'NAME', 'VERSION', 'UPTIME', 'STATUS',
        'REPLICAS' if not pool else 'WORKERS'
    ]
    if not pool:
        service_columns.append('ENDPOINT')
    if show_all:
        service_columns.extend([
            'AUTOSCALING_POLICY', 'LOAD_BALANCING_POLICY', 'REQUESTED_RESOURCES'
        ])
        if pool:
            # Remove the load balancing policy column for pools.
            service_columns.pop(-2)
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
        ]
        if not pool:
            service_values.append(endpoint)
        if show_all:
            service_values.extend(
                [policy, load_balancing_policy, requested_resources_str])
            if pool:
                service_values.pop(-2)
        service_table.add_row(service_values)

    replica_table = _format_replica_table(replica_infos, show_all, pool)
    replica_noun = 'Pool Workers' if pool else 'Service Replicas'
    return (f'{service_table}\n'
            f'\n{colorama.Fore.CYAN}{colorama.Style.BRIGHT}'
            f'{replica_noun}{colorama.Style.RESET_ALL}\n'
            f'{replica_table}')


def _format_replica_table(replica_records: List[Dict[str, Any]], show_all: bool,
                          pool: bool) -> str:
    noun = 'worker' if pool else 'replica'
    if not replica_records:
        return f'No existing {noun}s.'

    replica_columns = [
        'POOL_NAME' if pool else 'SERVICE_NAME', 'ID', 'VERSION', 'ENDPOINT',
        'LAUNCHED', 'INFRA', 'RESOURCES', 'STATUS'
    ]
    if pool:
        replica_columns.append('USED_BY')
        # Remove the endpoint column for pool workers.
        replica_columns.pop(3)
    replica_table = log_utils.create_table(replica_columns)

    truncate_hint = ''
    if not show_all:
        if len(replica_records) > _REPLICA_TRUNC_NUM:
            truncate_hint = f'\n... (use --all to show all {noun}s)'
        replica_records = replica_records[:_REPLICA_TRUNC_NUM]

    for record in replica_records:
        endpoint = record.get('endpoint', '-')
        service_name = record['service_name']
        replica_id = record['replica_id']
        version = (record['version'] if 'version' in record else '-')
        replica_endpoint = endpoint if endpoint else '-'
        launched_at = log_utils.readable_time_duration(record['launched_at'])
        infra = '-'
        resources_str = '-'
        replica_status = record['status']
        status_str = replica_status.colored_str()
        used_by = record.get('used_by', None)
        used_by_str = str(used_by) if used_by is not None else '-'

        replica_handle: Optional['backends.CloudVmRayResourceHandle'] = record[
            'handle']
        if replica_handle is not None:
            infra = replica_handle.launched_resources.infra.formatted_str()
            simplified = not show_all
            resources_str_simple, resources_str_full = (
                resources_utils.get_readable_resources_repr(
                    replica_handle, simplified_only=simplified))
            if simplified:
                resources_str = resources_str_simple
            else:
                assert resources_str_full is not None
                resources_str = resources_str_full

        replica_values = [
            service_name,
            replica_id,
            version,
            replica_endpoint,
            launched_at,
            infra,
            resources_str,
            status_str,
        ]
        if pool:
            replica_values.append(used_by_str)
            replica_values.pop(3)
        replica_table.add_row(replica_values)

    return f'{replica_table}{truncate_hint}'


# =========================== CodeGen for Sky Serve ===========================
# TODO (kyuds): deprecate and remove serve codegen entirely.


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
        'serve_version = constants.SERVE_VERSION',
    ]

    @classmethod
    def get_service_status(cls, service_names: Optional[List[str]],
                           pool: bool) -> str:
        code = [
            f'kwargs={{}} if serve_version < 3 else {{"pool": {pool}}}',
            f'msg = serve_utils.get_service_status_encoded({service_names!r}, '
            '**kwargs)', 'print(msg, end="", flush=True)'
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
    def terminate_services(cls, service_names: Optional[List[str]], purge: bool,
                           pool: bool) -> str:
        code = [
            f'kwargs={{}} if serve_version < 3 else {{"pool": {pool}}}',
            f'msg = serve_utils.terminate_services({service_names!r}, '
            f'purge={purge}, **kwargs)', 'print(msg, end="", flush=True)'
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
    def wait_service_registration(cls, service_name: str, job_id: int,
                                  pool: bool) -> str:
        code = [
            f'kwargs={{}} if serve_version < 4 else {{"pool": {pool}}}',
            'msg = serve_utils.wait_service_registration('
            f'{service_name!r}, {job_id}, **kwargs)',
            'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def stream_replica_logs(cls, service_name: str, replica_id: int,
                            follow: bool, tail: Optional[int],
                            pool: bool) -> str:
        code = [
            f'kwargs={{}} if serve_version < 5 else {{"pool": {pool}}}',
            'msg = serve_utils.stream_replica_logs('
            f'{service_name!r}, {replica_id!r}, follow={follow}, tail={tail}, '
            '**kwargs)', 'print(msg, flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def stream_serve_process_logs(cls, service_name: str,
                                  stream_controller: bool, follow: bool,
                                  tail: Optional[int], pool: bool) -> str:
        code = [
            f'kwargs={{}} if serve_version < 5 else {{"pool": {pool}}}',
            f'msg = serve_utils.stream_serve_process_logs({service_name!r}, '
            f'{stream_controller}, follow={follow}, tail={tail}, **kwargs)',
            'print(msg, flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def update_service(cls, service_name: str, version: int, mode: str,
                       pool: bool) -> str:
        code = [
            f'kwargs={{}} if serve_version < 3 else {{"pool": {pool}}}',
            f'msg = serve_utils.update_service_encoded({service_name!r}, '
            f'{version}, mode={mode!r}, **kwargs)',
            'print(msg, end="", flush=True)',
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
