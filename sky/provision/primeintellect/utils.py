"""Primeintellect library wrapper for SkyPilot."""

import json
import os
import time
import typing
from typing import Any, Dict, List, Optional, Union

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.utils import common_utils

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')

CREDENTIALS_PATH = '~/.prime/config.json'
API_ENDPOINT = "https://api.primeintellect.ai"
INITIAL_BACKOFF_SECONDS = 10
MAX_BACKOFF_FACTOR = 10
MAX_ATTEMPTS = 6

logger = sky_logging.init_logger(__name__)

def _try_request_with_backoff(
        method: str,
        url: str,
        headers: Dict[str, str],
        data: Optional[Union[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    backoff = common_utils.Backoff(initial_backoff=INITIAL_BACKOFF_SECONDS,
                                   max_backoff_factor=MAX_BACKOFF_FACTOR)
    for i in range(MAX_ATTEMPTS):
        if method == 'get':
            response = requests.get(url, headers=headers, params=data)
        elif method == 'post':
            response = requests.post(url, headers=headers, data=data)
        elif method == 'put':
            response = requests.put(url, headers=headers, data=data)
        elif method == 'patch':
            response = requests.patch(url, headers=headers, data=data)
        elif method == 'delete':
            response = requests.delete(url, headers=headers)
        else:
            raise ValueError(f'Unsupported requests method: {method}')
        # If rate limited, wait and try again
        if response.status_code == 429 and i != MAX_ATTEMPTS - 1:
            time.sleep(backoff.current_backoff())
            continue
        if response.status_code == 200:
            return response.json()
    return {}

def get_key_suffix():
    return str(uuid.uuid4()).replace('-', '')[:8]

class PrimeintellectAPIClient:
    """Wrapper functions for Primeintellect API."""

    def __init__(self) -> None:
        self.credentials = os.path.expanduser(CREDENTIALS_PATH)
        assert os.path.exists(self.credentials), 'Credentials not found'
        with open(self.credentials, 'r', encoding='utf-8') as f:
            self._credentials = json.load(f)
        self.api_key = self._credentials['api_key']
        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    def list_instances(self, **search_kwargs) -> List[Dict[str, Any]]:
        response = _try_request_with_backoff('get',
                                             f'{API_ENDPOINT}/api/v1/pods',
                                             headers=self.headers,
                                             data=search_kwargs)
        return response['data']

    def launch(self, name: str, instance_type: str, network_id: str,
               region: str, disk_size: int) -> Dict[str, Any]:
        del network_id, region
        response = _try_request_with_backoff(
            'post',
            f'{API_ENDPOINT}/api/v1/pods',
            headers=self.headers,
            # TODO: remove hardcode
            # TODO: logic to fetch other infos not available from instance
            data=json.dumps({
                'pod': {
                    'name': name,
                    'cloudId': instance_type,
                    'gpuType': instance_type,
                    'socket': "PCIe",
                    'gpuCount': 1,
                    'diskSize': disk_size,
                },
                'provider': {
                    'type': "runpod",   # TODO: get provider name from instance_type
                },
                'team': {
                    'teamId': None,
                }
            }))
        return response

    def remove(self, instance_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            'delete',
            f'{API_ENDPOINT}/api/v1/pods/{instance_id}',
            headers=self.headers,
        )

    def list_ssh_keys(self):
        return _try_request_with_backoff(
            'get',
            f'{API_ENDPOINT}/api/v1/ssh_keys',
            headers=self.headers
        )

    def get_or_add_ssh_key(self, ssh_pub_key: str = '') -> Dict[str, str]:
        """Add ssh key if not already added."""
        ssh_keys = self.list_ssh_keys()
        for key in ssh_keys:
            if key['public_key'].strip().split()[:2] == ssh_pub_key.strip(
            ).split()[:2]:
                return {'name': key['name'], 'ssh_key': ssh_pub_key}
        ssh_key_name = 'skypilot-' + get_key_suffix()
        _try_request_with_backoff(
            'post',
            f'{API_ENDPOINT}/api/v1/ssh_keys',
            headers=self.headers,
            data=dict(name=ssh_key_name, public_key=ssh_pub_key),
        )
        return {'name': ssh_key_name, 'ssh_key': ssh_pub_key}
