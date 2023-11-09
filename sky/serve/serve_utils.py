"""User interface with the SkyServe."""
import base64
import enum
import os
import pathlib
import pickle
import re
import shlex
import threading
import time
import traceback
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

if typing.TYPE_CHECKING:
    import fastapi

SKY_SERVE_CONTROLLER_NAME: str = (
    f'sky-serve-controller-{common_utils.get_user_hash()}')
_SYSTEM_MEMORY_GB = psutil.virtual_memory().total // (1024**3)
NUM_SERVICE_THRESHOLD = _SYSTEM_MEMORY_GB // constants.SERVICES_MEMORY_USAGE_GB

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


class UserSignal(enum.Enum):
    """User signal to send to controller.

    User can send signal to controller by writing to a file. The controller
    will read the file and handle the signal.
    """
    # Stop the controller, load balancer and all replicas.
    TERMINATE = 'terminate'

    # TODO(tian): Add more signals, such as update or pause.

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


class RedirectOutputTo:
    """Redirect stdout and stderr to a file."""

    def __init__(self, func: Callable, file: str) -> None:
        self.func = func
        self.file = file

    def run(self, *args, **kwargs):
        import sys  # pylint: disable=import-outside-toplevel

        from sky import sky_logging  # pylint: disable=import-outside-toplevel

        with open(self.file, 'w') as f:
            sys.stdout = f
            sys.stderr = f
            # reconfigure logger since the logger is initialized before
            # with previous stdout/stderr
            sky_logging.reload_logger()
            logger = sky_logging.init_logger(__name__)
            # The subprocess_util.run('sky status') inside
            # sky.execution::_execute cannot be redirect, since we cannot
            # directly operate on the stdout/stderr of the subprocess. This
            # is because some code in skypilot will specify the stdout/stderr
            # of the subprocess.
            try:
                self.func(*args, **kwargs)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Failed to run {self.func.__name__}. '
                             f'Details: {common_utils.format_exception(e)}\n'
                             f'Traceback:\n{traceback.format_exc()}')
                raise


def generate_service_name():
    return f'sky-service-{uuid.uuid4().hex[:4]}'


def generate_remote_service_dir_name(service_name: str) -> str:
    service_name = service_name.replace('-', '_')
    return os.path.join(constants.SKYSERVE_METADATA_DIR, service_name)


def generate_remote_task_yaml_file_name(service_name: str) -> str:
    dir_name = generate_remote_service_dir_name(service_name)
    # Don't expand here since it is used for remote machine.
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
        if controller_job_id is None:
            # The service just registered and the controller job is not
            # scheduled yet.
            continue
        controller_status = job_lib.get_status(controller_job_id)
        if controller_status is None or controller_status.is_terminal():
            # If controller job is not running, set it as controller failed.
            serve_state.set_service_status(
                record['name'], serve_state.ServiceStatus.CONTROLLER_FAILED)


def add_service_if_not_exist(service_name: str) -> str:
    return common_utils.encode_payload(
        serve_state.add_service_if_not_exist(service_name))


def load_add_service_result(payload: str) -> bool:
    return common_utils.decode_payload(payload)


def get_serve_status(service_name: str,
                     with_replica_info: bool = True) -> Dict[str, Any]:
    """Get the status dict of the service.

    Args:
        service_name: The name of the service.
        with_replica_info: Whether to include the information of all replicas.

    Returns:
        A dictionary, describing the status of the service.
    """
    record = serve_state.get_service_from_name(service_name)
    if record is None:
        raise ValueError(f'Service {service_name!r} does not exist.')
    if with_replica_info:
        record['replica_info'] = [
            info.to_info_dict(with_handle=True)
            for info in serve_state.get_replica_infos(service_name)
        ]
    return record


def get_serve_status_encoded(service_names: Optional[List[str]]) -> str:
    serve_statuss = []
    if service_names is None:
        # Get all service names
        service_names = serve_state.get_glob_service_names(None)
    for service_name in service_names:
        serve_status = get_serve_status(service_name)
        serve_statuss.append({
            k: base64.b64encode(pickle.dumps(v)).decode('utf-8')
            for k, v in serve_status.items()
        })
    return common_utils.encode_payload(serve_statuss)


def load_serve_status(payload: str) -> List[Dict[str, Any]]:
    serve_statuss_encoded = common_utils.decode_payload(payload)
    serve_statuss = []
    for serve_status in serve_statuss_encoded:
        serve_statuss.append({
            k: pickle.loads(base64.b64decode(v))
            for k, v in serve_status.items()
        })
    return serve_statuss


def terminate_services(service_names: Optional[List[str]]) -> str:
    service_names = serve_state.get_glob_service_names(service_names)
    terminated_service_names = []
    for service_name in service_names:
        serve_status = get_serve_status(service_name, with_replica_info=False)
        if (serve_status['status']
                in serve_state.ServiceStatus.refuse_to_terminate_statuses()):
            # TODO(tian): Cleanup replicas for CONTROLLER_FAILED status. Seems
            # like spot doesn't implement this yet?
            continue
        # Send the terminate signal to controller.
        signal_file = pathlib.Path(
            constants.SIGNAL_FILE_PATH.format(service_name))
        # Filelock is needed to prevent race condition between signal
        # check/removal and signal writing.
        with filelock.FileLock(str(signal_file) + '.lock'):
            with signal_file.open(mode='w') as f:
                # TODO(tian): Probably write a dict instead of bare string
                # to the file? It will be helpful for update cases.
                f.write(UserSignal.TERMINATE.value)
                f.flush()
        terminated_service_names.append(service_name)
    if len(terminated_service_names) == 0:
        return 'No service to terminate.'
    identity_str = f'Service with name {terminated_service_names[0]} is'
    if len(terminated_service_names) > 1:
        terminated_service_names_str = ', '.join(terminated_service_names)
        identity_str = f'Services with names {terminated_service_names_str} are'
    return f'{identity_str} scheduled to be terminated.'


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


# TODO(tian): Use REST API instead of SSH in the future. This will require
# authentication.
class ServeCodeGen:
    """Code generator for SkyServe.

    Usage:
      >> code = ServeCodeGen.get_serve_status(service_name)
    """
    _PREFIX = [
        'from sky.serve import serve_state',
        'from sky.serve import serve_utils',
    ]

    @classmethod
    def add_service_if_not_exist(cls, service_name: str) -> str:
        code = [
            f'msg = serve_utils.add_service_if_not_exist({service_name!r})',
            'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def get_serve_status(cls, service_names: Optional[List[str]]) -> str:
        code = [
            f'msg = serve_utils.get_serve_status_encoded({service_names!r})',
            'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def terminate_services(cls, service_names: Optional[List[str]]) -> str:
        code = [
            f'msg = serve_utils.terminate_services({service_names!r})',
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

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        generated_code = '; '.join(code)
        return f'python3 -u -c {shlex.quote(generated_code)}'
