"""User interface with the SkyServe."""
import base64
import pickle
import requests
import shlex
from typing import Dict, List

from sky.serve import constants
from sky.utils import common_utils

_CONTROL_PLANE_URL = f'http://0.0.0.0:{constants.CONTROL_PLANE_PORT}'

def get_replica_info() -> str:
    resp = requests.get(_CONTROL_PLANE_URL + '/control_plane/get_replica_info')
    if resp.status_code != 200:
        raise ValueError(f'Failed to get replica info: {resp.text}')
    resp = base64.b64encode(pickle.dumps(resp)).decode('utf-8')
    return common_utils.encode_payload(resp)

def load_replica_info(payload: str) -> List[Dict[str, str]]:
    replica_info = common_utils.decode_payload(payload)
    replica_info = pickle.loads(base64.b64decode(replica_info))
    decoded_info = []
    for info in replica_info.json()['replica_info']:
        decoded_info.append({
            k: pickle.loads(base64.b64decode(v))
            for k, v in info.items()
        })
    return decoded_info

def get_replica_nums() -> str:
    resp = requests.get(_CONTROL_PLANE_URL + '/control_plane/get_replica_nums')
    if resp.status_code != 200:
        raise ValueError(f'Failed to get replica nums: {resp.text}')
    resp = base64.b64encode(pickle.dumps(resp)).decode('utf-8')
    return common_utils.encode_payload(resp)

def load_replica_nums(payload: str) -> Dict[str, str]:
    replica_nums_resp = common_utils.decode_payload(payload)
    replica_nums_resp = pickle.loads(base64.b64decode(replica_nums_resp))
    return replica_nums_resp.json()

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
    def get_replica_nums(cls) -> str:
        code = [
            'msg = serve_utils.get_replica_nums()',
            'print(msg, end="", flush=True)'
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        generated_code = '; '.join(code)
        return f'python3 -u -c {shlex.quote(generated_code)}'
