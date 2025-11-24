"""Verda (DataCrunch) library wrapper for SkyPilot."""

import json
import os
import shlex
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

import requests

from sky.catalog import common as catalog_common
from sky.utils import common_utils

_df = None
_lookup_dict = None

DEFAULT_BASE_URL = 'https://api.datacrunch.io'
CREDENTIALS_PATH = '~/.verda/config.json'
INITIAL_BACKOFF_SECONDS = 10
MAX_BACKOFF_FACTOR = 10
MAX_ATTEMPTS = 6


class VerdaAPIError(Exception):
    """Base exception class."""

    def __init__(self,
                 message: str,
                 status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _parse_api_error(response: Any) -> Tuple[str, bool]:
    """Parse API error response to extract meaningful error messages.

    Returns:
        Tuple[str, bool]:
        - str: A human-readable error message parsed from the API response.
        - bool: True if the error indicates resource unavailability (e.g.,
          capacity issues or quota/limit exceeded), otherwise False.
    """
    try:
        if hasattr(response, 'json'):
            error_data = response.json()
        else:
            error_data = response

        if isinstance(error_data, dict):
            # Try to extract error message from common error response fields
            error_message = error_data.get('message', '')
            if not error_message:
                error_message = error_data.get('error', '')
            if not error_message:
                error_message = error_data.get('detail', '')

            # Check if it's a resource unavailability error
            if any(keyword in error_message.lower() for keyword in [
                    'no capacity', 'capacity', 'unavailable', 'out of stock',
                    'insufficient', 'not available', 'quota exceeded',
                    'limit exceeded'
            ]):
                return error_message, True

            return error_message, False

        return str(error_data), False
    except Exception:  # pylint: disable=broad-except
        return f'HTTP {response.status_code} {response.reason}', False


def _try_request_with_backoff(
        method: str,
        url: str,
        headers: Dict[str, str],
        data: Optional[Union[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    backoff = common_utils.Backoff(initial_backoff=INITIAL_BACKOFF_SECONDS,
                                   max_backoff_factor=MAX_BACKOFF_FACTOR)
    for i in range(MAX_ATTEMPTS):
        timeout = 30
        if method == 'get':
            response = requests.get(url,
                                    headers=headers,
                                    params=data,
                                    timeout=timeout)
        elif method == 'post':
            response = requests.post(url,
                                     headers=headers,
                                     json=data,
                                     timeout=timeout)
        elif method == 'put':
            response = requests.put(url,
                                    headers=headers,
                                    json=data,
                                    timeout=timeout)
        elif method == 'patch':
            response = requests.patch(url,
                                      headers=headers,
                                      json=data,
                                      timeout=timeout)
        elif method == 'delete':
            response = requests.delete(url, headers=headers, timeout=timeout)
        else:
            raise ValueError(f'Unsupported requests method: {method}')
        # If rate limited, wait and try again
        if response.status_code == 429 and i != MAX_ATTEMPTS - 1:
            time.sleep(backoff.current_backoff())
            continue
        if response.ok:
            return response.json()
        else:
            # Parse the error response for meaningful messages
            err = _parse_api_error(response)

            # Create a more informative error message
            if not err:
                err = (f'API request failed: {method} {url}: '
                       f'{response.status_code} {response.reason}')
            else:
                err = f'API request failed: {err}'
            raise VerdaAPIError(
                err,
                status=response.status_code)
    return {}


def get_upstream_cloud_id(instance_type: str) -> Optional[str]:
    global _df, _lookup_dict
    if _df is None:
        _df = catalog_common.read_catalog('verda/vms.csv')
        _lookup_dict = (
            _df.set_index('InstanceType')['UpstreamCloudId'].to_dict())
    return _lookup_dict.get(instance_type)


class VerdaCloudAPIClient:
    """Client for interacting with Verda (DataCrunch) API."""

    def __init__(self) -> None:
        self.credentials = os.path.expanduser(CREDENTIALS_PATH)
        assert os.path.exists(self.credentials), 'Credentials not found'
        with open(self.credentials, 'r', encoding='utf-8') as f:
            self._credentials = json.load(f)
        self.api_key = self._credentials.get('api_key')
        self.api_secret = self._credentials.get('api_secret')
        self.base_url = self._credentials.get('base_url', DEFAULT_BASE_URL)
        response = _try_request_with_backoff('post',
                                             f'{self.base_url}/v1/oauth2/token',
                                             headers={
                                                'Content-Type': 'application/json'
                                             },
                                             data={
                                                "grant_type": "client_credentials",
                                                "client_id": self.api_key,
                                                "client_secret": self.api_secret
                                             })
        # DataCrunch API returns access_token directly in response
        if 'access_token' not in response:
            error_msg = response.get('message', response.get('error', 'Unknown error'))
            raise VerdaAPIError(f'Failed to get access token: {error_msg}')
        self.access_token = response['access_token']
        self.headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

    def list_instances(self, **search_kwargs) -> List[Dict[str, Any]]:
        response = _try_request_with_backoff('get',
                                             f'{self.base_url}/v1/instances',
                                             headers=self.headers,
                                             data=search_kwargs)
        return response['data']

    def get_instance_details(self, instance_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            'get',
            f'{self.base_url}/v1/instances/{instance_id}',
            headers=self.headers)

    def launch(self,
               name: str,
               instance_type: str,
               region: str,
               availability_zone: str,
               disk_size: int,
               vcpus: int = 0,
               memory: int = 0) -> Dict[str, Any]:
        """Create an instance via Verda (DataCrunch) API.

        Args:
            name: User-visible name of the pod.
            instance_type: A catalog instance type string. The expected format
                is:
                "<provider>__<accelerator>__<vcpus>__<memory>[_SPOT]".

                - <provider>: Upstream provider tag (e.g., "datacrunch").
                - <accelerator>:
                  * GPU nodes: "<N>x<GPU_MODEL>", e.g., "8xH100_80GB".
                  * CPU-only nodes: the literal "CPU_NODE".
                - <vcpus>: Integer string for vCPU count (e.g., "104").
                - <memory>: Integer string for memory in GB (e.g., "752").
                - Optional suffix "_SPOT" may be present in the full string
                  (ignored here; pricing/spot behavior is not controlled by
                  this method).

                Notes:
                - Parsing: only the first two components (provider,
                  accelerator) are needed to build the payload. The vCPU
                  and memory values are provided via the ``vcpus`` and
                  ``memory`` arguments.
                - Catalog lookup: the full instance_type string is used to
                  map to the catalog's UpstreamCloudId.
                - CPU-only: accelerator "CPU_NODE" is a sentinel for
                  "no GPU". We set gpuType='CPU_NODE' and gpuCount=1 to
                  represent CPU-only pods.
                - Spot: the optional "__SPOT" suffix (if present) is ignored
                  here; pricing/spot behavior is handled elsewhere.

            region: Country/region code used by Verda (DataCrunch).
            availability_zone: Data center ID (zone) within the region.
            disk_size: Boot disk size in GB.
            vcpus: Optional explicit vCPU override; if >0 it will be sent.
            memory: Optional explicit memory override in GB; if >0 it will be
                sent.

        Returns:
            The API response JSON as a dict.
        """
        cloud_id = get_upstream_cloud_id(instance_type)
        assert cloud_id, 'cloudId cannot be None'
        assert availability_zone, 'availability_zone cannot be None'

        # Parse the instance_type. We only need the first two components:
        # provider and accelerator info (see docstring above).
        provider, gpu_parts, _, _ = instance_type.split('__', 3)
        if 'CPU_NODE' in gpu_parts:
            # DataCrunch API uses the same schema for CPU-only and GPU
            # instances. For CPU-only instances, we set gpuType='CPU_NODE' and
            # gpuCount=1 as a sentinel to indicate "no GPU". This is how CPU
            # instances are represented internally on our platform; the
            # backend does not interpret this as having a physical GPU.
            gpu_type = 'CPU_NODE'
            gpu_count = 1
        else:
            parts = gpu_parts.split('x', 1)
            gpu_count = int(parts[0])
            gpu_type = parts[1]

        # DataCrunch API payload structure
        payload: Dict[str, Any] = {
            'name': name,
            'instanceType': cloud_id,
            'gpuType': gpu_type,
            'gpuCount': int(gpu_count),
            'diskSize': disk_size,
        }

        if vcpus > 0:
            payload['vcpus'] = vcpus
        if memory > 0:
            payload['memory'] = memory

        if region != 'UNSPECIFIED':
            payload['region'] = region
        if availability_zone != 'UNSPECIFIED':
            payload['availabilityZone'] = availability_zone

        response = _try_request_with_backoff(
            'post',
            f'{self.base_url}/v1/instances',
            headers=self.headers,
            data=payload,
        )
        return response

    def remove(self, instance_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            'delete',
            f'{self.base_url}/v1/instances/{instance_id}',
            headers=self.headers,
        )

    def list_ssh_keys(self) -> List[Dict[str, Any]]:
        response = _try_request_with_backoff('get',
                                             f'{self.base_url}/v1/ssh-keys',
                                             headers=self.headers)
        # DataCrunch API may return data directly or in a 'data' field
        if isinstance(response, dict) and 'data' in response:
            return response['data']
        return response if isinstance(response, list) else []

    def get_or_add_ssh_key(self, ssh_pub_key: str = '') -> Dict[str, str]:
        """Add ssh key if not already added."""
        # Check if the public key is already added
        ssh_keys = self.list_ssh_keys()
        for key in ssh_keys:
            key_pub = key.get('publicKey') or key.get('public_key') or key.get('key', '')
            if key_pub.strip().split()[:2] == ssh_pub_key.strip().split()[:2]:
                return {'name': key.get('name', ''), 'ssh_key': ssh_pub_key}

        # Add the public key to Verda account if not already added
        ssh_key_name = 'skypilot-' + str(uuid.uuid4()).replace('-', '')[:8]
        _try_request_with_backoff(
            'post',
            f'{self.base_url}/v1/ssh-keys',
            headers=self.headers,
            data={
                'name': ssh_key_name,
                'publicKey': ssh_pub_key
            },
        )
        return {'name': ssh_key_name, 'ssh_key': ssh_pub_key}
