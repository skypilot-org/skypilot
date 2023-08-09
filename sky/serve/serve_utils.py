"""User interface with the SkyServe."""
import base64
import colorama
import os
import pickle
import re
import requests
import shlex
import time
from typing import Any, Dict, List, Optional, Iterator, TextIO, Callable

from sky import backends
from sky import global_user_state
from sky.serve import constants
from sky import status_lib
from sky.utils import common_utils

_CONTROL_PLANE_URL = f'http://localhost:{constants.CONTROL_PLANE_PORT}'
_SKYPILOT_PROVISION_LOG_PATTERN = r'.*tail -n100 -f (.*provision\.log).*'
_SKYPILOT_LOG_PATTERN = r'.*tail -n100 -f (.*\.log).*'
_FAILED_TO_FIND_REPLICA_MSG = (
    f'{colorama.Fore.RED}Failed to find replica '
    '{replica_id}. Please use `sky serve status [SERVICE_ID]`'
    f' to check all valid replica id.{colorama.Style.RESET_ALL}')


def generate_replica_cluster_name(service_name: str, replica_id: int) -> str:
    return f'{service_name}-{replica_id}'


def generate_replica_launch_log_file_name(cluster_name: str) -> str:
    cluster_name = cluster_name.replace('-', '_')
    prefix = os.path.expanduser(constants.SERVICE_YAML_PREFIX)
    return f'{prefix}/{cluster_name}_launch_log.txt'


def generate_replica_down_log_file_name(cluster_name: str) -> str:
    cluster_name = cluster_name.replace('-', '_')
    prefix = os.path.expanduser(constants.SERVICE_YAML_PREFIX)
    return f'{prefix}/{cluster_name}_down_log.txt'


def get_replica_info() -> str:
    resp = requests.get(_CONTROL_PLANE_URL + '/control_plane/get_replica_info')
    if resp.status_code != 200:
        raise ValueError(f'Failed to get replica info: {resp.text}')
    return common_utils.encode_payload(resp.json()['replica_info'])


def load_replica_info(payload: str) -> List[Dict[str, Any]]:
    replica_info = common_utils.decode_payload(payload)
    replica_info = pickle.loads(base64.b64decode(replica_info))
    return replica_info


def terminate_service() -> str:
    resp = requests.post(_CONTROL_PLANE_URL + '/control_plane/terminate')
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


def stream_logs(service_name: str, replica_id: int, follow: bool) -> str:
    print(f'{colorama.Fore.YELLOW}Start streaming logs for launching process '
          f'of replica {replica_id}.{colorama.Style.RESET_ALL}')
    replica_cluster_name = generate_replica_cluster_name(
        service_name, replica_id)
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
        resp = requests.get(_CONTROL_PLANE_URL +
                            '/control_plane/get_replica_info')
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

    replica_is_provisioning = (lambda: follow and _get_replica_status() !=
                               status_lib.ReplicaStatus.PROVISIONING)
    with open(launch_log_file_name, 'r', newline='') as f:
        for line in _follow_logs(f,
                                 replica_cluster_name,
                                 finish_stream=replica_is_provisioning):
            print(line, end='', flush=True)
    if not follow and _get_replica_status(
    ) == status_lib.ReplicaStatus.PROVISIONING:
        # Early exit if not following the logs.
        return ''

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
      >> code = ServeCodeGen.get_replica_info()
    """
    _PREFIX = [
        'from sky.serve import serve_utils',
    ]

    @classmethod
    def get_replica_info(cls) -> str:
        code = [
            'msg = serve_utils.get_replica_info()',
            'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def terminate_service(cls) -> str:
        code = [
            'msg = serve_utils.terminate_service()',
            'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def stream_logs(cls, service_name: str, replica_id: int,
                    follow: bool) -> str:
        code = [
            f'msg = serve_utils.stream_logs({service_name!r}, '
            f'{replica_id!r}, follow={follow})', 'print(msg, flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        generated_code = '; '.join(code)
        return f'python3 -u -c {shlex.quote(generated_code)}'
