"""User interface with the SkyServe."""
import base64
import colorama
import pickle
import requests
import shlex
from typing import Any, Dict, List, Optional

from sky import backends
from sky import global_user_state
from sky.serve import constants
from sky import status_lib
from sky.utils import common_utils

_CONTROL_PLANE_URL = f'http://0.0.0.0:{constants.CONTROL_PLANE_PORT}'
_FAILED_TO_FIND_REPLICA_MSG = (
    f'{colorama.Fore.RED}Failed to find replica '
    '{replica_id}. Please use `sky serve status [SERVICE_ID]`'
    f' to check all valid replica id.{colorama.Style.RESET_ALL}')


def generate_replica_cluster_name(service_name: str, replica_id: int) -> str:
    return f'{service_name}-{replica_id}'


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


def stream_logs(service_name: str, replica_id: int, follow: bool) -> str:
    resp = requests.get(_CONTROL_PLANE_URL + '/control_plane/get_replica_info')
    if resp.status_code != 200:
        return (f'{colorama.Fore.RED}Failed to get replica info for service '
                f'{service_name}.{colorama.Style.RESET_ALL}')
    replica_info = resp.json()['replica_info']
    replica_info = pickle.loads(base64.b64decode(replica_info))
    target_info: Optional[Dict[str, Any]] = None
    for info in replica_info:
        if info['replica_id'] == replica_id:
            target_info = info
            break
    if target_info is None:
        return _FAILED_TO_FIND_REPLICA_MSG.format(replica_id=replica_id)
    if target_info['status'] == status_lib.ReplicaStatus.PROVISIONING:
        return (
            f'{colorama.Fore.RED}Replica {replica_id} is still provisioning.'
            f' Please try again later.{colorama.Style.RESET_ALL}')
    replica_cluster_name = generate_replica_cluster_name(
        service_name, replica_id)
    handle = global_user_state.get_handle_from_cluster_name(
        replica_cluster_name)
    if handle is None:
        return _FAILED_TO_FIND_REPLICA_MSG.format(replica_id=replica_id)
    assert isinstance(handle, backends.CloudVmRayResourceHandle), handle
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
