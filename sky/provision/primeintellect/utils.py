"""Primeintellect library wrapper for SkyPilot."""

import json
import os
import time
import typing
from typing import Any, Dict, List, Optional, Union
import uuid

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.catalog import common as catalog_common
from sky.utils import common_utils

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')

_df = None
_lookup_dict = None

CREDENTIALS_PATH = '~/.prime/config.json'
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
            response = requests.post(url, headers=headers, json=data)
        elif method == 'put':
            response = requests.put(url, headers=headers, json=data)
        elif method == 'patch':
            response = requests.patch(url, headers=headers, json=data)
        elif method == 'delete':
            response = requests.delete(url, headers=headers)
        else:
            raise ValueError(f'Unsupported requests method: {method}')
        # If rate limited, wait and try again
        if response.status_code == 429 and i != MAX_ATTEMPTS - 1:
            time.sleep(backoff.current_backoff())
            continue
        if response.ok:
            return response.json()
        else:
            raise Exception(
                f"API request failed: {method} {url}: {response.status_code} {response.reason}"
            )
    return {}


def get_key_suffix():
    return str(uuid.uuid4()).replace('-', '')[:8]


def get_upstream_cloud_id(instance_type: str) -> Optional[str]:
    global _df, _lookup_dict
    if _df is None:
        _df = catalog_common.read_catalog('primeintellect/vms.csv')
    if _lookup_dict is None:
        _lookup_dict = _df.set_index(
            'InstanceType')['UpstreamCloudId'].to_dict()

    return _lookup_dict.get(instance_type)


class PrimeintellectAPIClient:
    """Wrapper functions for Primeintellect API."""

    def __init__(self) -> None:
        self.credentials = os.path.expanduser(CREDENTIALS_PATH)
        assert os.path.exists(self.credentials), 'Credentials not found'
        with open(self.credentials, 'r', encoding='utf-8') as f:
            self._credentials = json.load(f)
        self.api_key = self._credentials['api_key']
        self.base_url = self._credentials['base_url']
        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    def list_instances(self, **search_kwargs) -> List[Dict[str, Any]]:
        response = _try_request_with_backoff('get',
                                             f'{self.base_url}/api/v1/pods',
                                             headers=self.headers,
                                             data=search_kwargs)
        return response['data']

    def get_instance_details(self, instance_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            'get',
            f'{self.base_url}/api/v1/pods/{instance_id}',
            headers=self.headers)

    def launch(self, name: str, instance_type: str, region: str,
               disk_size: int) -> Dict[str, Any]:
        cloudId = get_upstream_cloud_id(instance_type)
        assert cloudId, "cloudId cannot be None"

        if not region == 'PLACEHOLDER':
            parts = region.split(' - ', 1)  # Split at most once
            country = parts[0]
            data_center_id = parts[1] if len(parts) > 1 else ''
        else:
            country = None
            data_center_id = None

        provider, gpu_parts, _, _ = instance_type.split('__', 3)
        if "CPU_NODE" in gpu_parts:
            gpuType = "CPU_NODE"
            gpuCount = 1  # CPU considered as 1
        else:
            parts = gpu_parts.split('x', 1)
            gpuCount = int(parts[0])
            gpuType = parts[1]

        payload = {
            'pod': {
                'name': name,
                'cloudId': cloudId,
                'socket': "PCIe",
                'gpuType': gpuType,
                'gpuCount': int(gpuCount),
                'diskSize': disk_size,
                'country': country,
                'dataCenterId': data_center_id,
            },
            'provider': {
                'type': provider,
            }
        }

        response = _try_request_with_backoff(
            'post',
            f'{self.base_url}/api/v1/pods',
            headers=self.headers,
            data=payload,
        )
        return response

    def remove(self, instance_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            'delete',
            f'{self.base_url}/api/v1/pods/{instance_id}',
            headers=self.headers,
        )

    def list_ssh_keys(self) -> List[Dict[str, Any]]:
        response = _try_request_with_backoff('get',
                                             f'{self.base_url}/api/v1/ssh_keys',
                                             headers=self.headers)
        return response['data']

    def get_or_add_ssh_key(self, ssh_pub_key: str = '') -> Dict[str, str]:
        """Add ssh key if not already added."""
        ssh_keys = self.list_ssh_keys()
        for key in ssh_keys:
            if key['publicKey'].strip().split()[:2] == ssh_pub_key.strip(
            ).split()[:2]:
                return {'name': key['name'], 'ssh_key': ssh_pub_key}

        ssh_key_name = 'skypilot-' + get_key_suffix()
        _try_request_with_backoff(
            'post',
            f'{self.base_url}/api/v1/ssh_keys',
            headers=self.headers,
            data={
                "name": ssh_key_name,
                "publicKey": ssh_pub_key
            },
        )
        return {'name': ssh_key_name, 'ssh_key': ssh_pub_key}
