"""User interface with the SkyServe."""
import base64
import os
import pickle
import re
import shlex
import threading
import time
import typing
from typing import (Any, Callable, Dict, Generic, Iterator, List, Optional,
                    TextIO, TypeVar)

import colorama
import requests

from sky import backends
from sky import global_user_state
from sky import status_lib
from sky.data import storage as storage_lib
from sky.serve import constants
from sky.utils import common_utils

if typing.TYPE_CHECKING:
    import sky

# A series of pre-hook commands that will be insert to the beginning of each
# serve-related task, Including controller and replcias.
# Shutdown jupyter service that is default enabled on our GCP Deep
# Learning Image. This is to avoid port conflict on 8080.
# Shutdown jupyterhub service that is default enabled on our Azure Deep
# Learning Image. This is to avoid port conflict on 8081.
SERVE_PREHOOK_COMMANDS = """\
sudo systemctl stop jupyter > /dev/null 2>&1 || true
sudo systemctl stop jupyterhub > /dev/null 2>&1 || true
"""

_CONTROLLER_URL = 'http://localhost:{CONTROLLER_PORT}'
_SKYPILOT_PROVISION_LOG_PATTERN = r'.*tail -n100 -f (.*provision\.log).*'
_SKYPILOT_LOG_PATTERN = r'.*tail -n100 -f (.*\.log).*'
_FAILED_TO_FIND_REPLICA_MSG = (
    f'{colorama.Fore.RED}Failed to find replica '
    '{replica_id}. Please use `sky serve status [SERVICE_ID]`'
    f' to check all valid replica id.{colorama.Style.RESET_ALL}')

KeyType = TypeVar('KeyType')
ValueType = TypeVar('ValueType')


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


def generate_controller_cluster_name(service_name: str) -> str:
    return constants.CONTROLLER_PREFIX + service_name


def generate_remote_task_yaml_file_name(service_name: str) -> str:
    service_name = service_name.replace('-', '_')
    # Don't expand here since it is used for remote machine.
    prefix = constants.SERVE_PREFIX
    return os.path.join(prefix, f'{service_name}.yaml')


def generate_controller_yaml_file_name(service_name: str) -> str:
    service_name = service_name.replace('-', '_')
    prefix = os.path.expanduser(constants.SERVE_PREFIX)
    return os.path.join(prefix, f'{service_name}_controller.yaml')


def generate_replica_cluster_name(service_name: str, replica_id: int) -> str:
    return f'{service_name}-{replica_id}'


def get_replica_id_from_cluster_name(cluster_name: str) -> int:
    return int(cluster_name.split('-')[-1])


def generate_replica_launch_log_file_name(cluster_name: str) -> str:
    cluster_name = cluster_name.replace('-', '_')
    prefix = os.path.expanduser(constants.SERVE_PREFIX)
    return os.path.join(prefix, f'{cluster_name}_launch.log')


def generate_replica_down_log_file_name(cluster_name: str) -> str:
    cluster_name = cluster_name.replace('-', '_')
    prefix = os.path.expanduser(constants.SERVE_PREFIX)
    return os.path.join(prefix, f'{cluster_name}_down.log')


def generate_replica_local_log_file_name(cluster_name: str) -> str:
    cluster_name = cluster_name.replace('-', '_')
    prefix = os.path.expanduser(constants.SERVE_PREFIX)
    return os.path.join(prefix, f'{cluster_name}_local.log')


class ServiceHandle(object):
    """A pickle-able tuple of:

    - (required) Controller cluster name.
    - (required) Service autoscaling policy descriotion str.
    - (required) Service requested resources.
    - (required) All replica info.
    - (optional) Service uptime.
    - (optional) Service endpoint URL.
    - (optional) Controller port to use.
    - (optional) Redirector port to use.
    - (optional) Controller job id.
    - (optional) Redirector job id.
    - (optional) Epemeral storage generated for the service.

    This class is only used as a cache for information fetched from controller.
    """
    _VERSION = 1

    def __init__(
        self,
        *,
        controller_cluster_name: str,
        policy: str,
        requested_resources: 'sky.Resources',
        replica_info: List[Dict[str, Any]],
        uptime: Optional[int] = None,
        endpoint: Optional[str] = None,
        controller_port: Optional[int] = None,
        redirector_port: Optional[int] = None,
        controller_job_id: Optional[int] = None,
        redirector_job_id: Optional[int] = None,
        ephemeral_storage: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self._version = self._VERSION
        self.controller_cluster_name = controller_cluster_name
        self.replica_info = replica_info
        self.uptime = uptime
        self.endpoint = endpoint
        self.policy = policy
        self.requested_resources = requested_resources
        self.controller_port = controller_port
        self.redirector_port = redirector_port
        self.controller_job_id = controller_job_id
        self.redirector_job_id = redirector_job_id
        self.ephemeral_storage = ephemeral_storage

    def __repr__(self):
        return ('ServiceHandle('
                f'\n\tcontroller_cluster_name={self.controller_cluster_name},'
                f'\n\treplica_info={self.replica_info},'
                f'\n\tuptime={self.uptime},'
                f'\n\tendpoint={self.endpoint},'
                f'\n\tpolicy={self.policy},'
                f'\n\trequested_resources={self.requested_resources},'
                f'\n\tcontroller_port={self.controller_port},'
                f'\n\tredirector_port={self.redirector_port},'
                f'\n\tcontroller_job_id={self.controller_job_id},'
                f'\n\tredirector_job_id={self.redirector_job_id},'
                f'\n\tephemeral_storage={self.ephemeral_storage})')

    def cleanup_ephemeral_storage(self) -> None:
        if self.ephemeral_storage is None:
            return
        for storage_config in self.ephemeral_storage:
            storage = storage_lib.Storage.from_yaml_config(storage_config)
            storage.delete(silent=True)

    def __setsate__(self, state):
        self._version = self._VERSION
        self.__dict__.update(state)


def get_latest_info(controller_port: int) -> str:
    resp = requests.get(
        _CONTROLLER_URL.format(CONTROLLER_PORT=controller_port) +
        '/controller/get_latest_info')
    if resp.status_code != 200:
        raise ValueError(f'Failed to get replica info: {resp.text}')
    return common_utils.encode_payload(resp.json())


def load_latest_info(payload: str) -> Dict[str, Any]:
    latest_info = common_utils.decode_payload(payload)
    latest_info = {
        k: pickle.loads(base64.b64decode(v)) for k, v in latest_info.items()
    }
    return latest_info


def terminate_service(controller_port: int) -> str:
    resp = requests.post(
        _CONTROLLER_URL.format(CONTROLLER_PORT=controller_port) +
        '/controller/terminate')
    resp = base64.b64encode(pickle.dumps(resp)).decode('utf-8')
    return common_utils.encode_payload(resp)


def load_terminate_service_result(payload: str) -> Any:
    terminate_resp = common_utils.decode_payload(payload)
    terminate_resp = pickle.loads(base64.b64decode(terminate_resp))
    return terminate_resp


def _follow_logs(file: TextIO,
                 cluster_name: str,
                 *,
                 finish_stream: Callable[[], bool],
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
                            for l in _follow_logs(f,
                                                  cluster_name,
                                                  finish_stream=cluster_is_up,
                                                  no_new_content_timeout=10):
                                yield l
                        log_file = None
                line = ''
        else:
            if finish_stream():
                break
            if no_new_content_timeout is not None:
                if no_new_content_cnt >= no_new_content_timeout:
                    break
                no_new_content_cnt += 1
            time.sleep(1)


def stream_logs(service_name: str,
                controller_port: int,
                replica_id: int,
                follow: bool,
                skip_local_log_file_check: bool = False) -> str:
    print(f'{colorama.Fore.YELLOW}Start streaming logs for launching process '
          f'of replica {replica_id}.{colorama.Style.RESET_ALL}')
    replica_cluster_name = generate_replica_cluster_name(
        service_name, replica_id)
    local_log_file_name = generate_replica_local_log_file_name(
        replica_cluster_name)

    if not skip_local_log_file_check and os.path.exists(local_log_file_name):
        # When sync down, we set skip_local_log_file_check to False so it won't
        # detect the just created local log file. Otherwise, it indicates the
        # replica is already been terminated. All logs should be in the local
        # log file and we don't need to stream logs for it.
        with open(local_log_file_name, 'r') as f:
            print(f.read(), flush=True)
        return ''

    handle = global_user_state.get_handle_from_cluster_name(
        replica_cluster_name)
    if handle is None:
        return _FAILED_TO_FIND_REPLICA_MSG.format(replica_id=replica_id)
    assert isinstance(handle, backends.CloudVmRayResourceHandle), handle

    launch_log_file_name = generate_replica_launch_log_file_name(
        replica_cluster_name)
    if not os.path.exists(launch_log_file_name):
        return (f'{colorama.Fore.RED}Replica {replica_id} doesn\'t exist.'
                f'{colorama.Style.RESET_ALL}')

    def _get_replica_status() -> status_lib.ReplicaStatus:
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

    finish_stream = (lambda: not follow or _get_replica_status() != status_lib.
                     ReplicaStatus.PROVISIONING)
    with open(launch_log_file_name, 'r', newline='') as f:
        for line in _follow_logs(f,
                                 replica_cluster_name,
                                 finish_stream=finish_stream):
            print(line, end='', flush=True)
    if not follow and _get_replica_status(
    ) == status_lib.ReplicaStatus.PROVISIONING:
        # Early exit if not following the logs.
        return ''

    # Notify user here to make sure user won't think the log is finished.
    print(f'{colorama.Fore.YELLOW}Start streaming logs for task job '
          f'of replica {replica_id}...{colorama.Style.RESET_ALL}')

    backend = backends.CloudVmRayBackend()
    # Always tail the logs of the first job, which represent user setup & run.
    returncode = backend.tail_logs(handle, job_id=1, follow=follow)
    if returncode != 0:
        return (f'{colorama.Fore.RED}Failed to stream logs for replica '
                f'{replica_id}.{colorama.Style.RESET_ALL}')
    return ''


class ServeCodeGen:
    """Code generator for SkyServe.

    Usage:
      >> code = ServeCodeGen.get_latest_info(controller_port)
    """
    _PREFIX = [
        'from sky.serve import serve_utils',
    ]

    @classmethod
    def get_latest_info(cls, controller_port: int) -> str:
        code = [
            f'msg = serve_utils.get_latest_info({controller_port})',
            'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def terminate_service(cls, controller_port: int) -> str:
        code = [
            f'msg = serve_utils.terminate_service({controller_port})',
            'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def stream_logs(cls,
                    service_name: str,
                    controller_port: int,
                    replica_id: int,
                    follow: bool,
                    skip_local_log_file_check: bool = False) -> str:
        code = [
            f'msg = serve_utils.stream_logs({service_name!r}, '
            f'{controller_port}, {replica_id!r}, follow={follow}, '
            f'skip_local_log_file_check={skip_local_log_file_check})',
            'print(msg, flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        generated_code = '; '.join(code)
        return f'python3 -u -c {shlex.quote(generated_code)}'
