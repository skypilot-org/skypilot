"""Prime Intellect library wrapper for SkyPilot."""

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

import requests

from sky.catalog import common as catalog_common
from sky.utils import common_utils

_df = None
_lookup_dict = None

# Base URL for Prime Intellect API (used as default if not configured).
DEFAULT_BASE_URL = 'https://api.primeintellect.ai'
CREDENTIALS_PATH = '~/.prime/config.json'
INITIAL_BACKOFF_SECONDS = 10
MAX_BACKOFF_FACTOR = 10
MAX_ATTEMPTS = 6


class PrimeintellectAPIError(Exception):
    """Base exception for Prime Intellect API errors."""

    def __init__(self,
                 message: str,
                 status_code: Optional[int] = None,
                 response_data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class PrimeintellectResourcesUnavailableError(PrimeintellectAPIError):
    """Exception for when resources are unavailable on Prime Intellect."""
    pass


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
            err, is_resource_unavailable = _parse_api_error(response)

            # Create a more informative error message
            if not err:
                err = (f'API request failed: {method} {url}: '
                       f'{response.status_code} {response.reason}')
            else:
                err = f'API request failed: {err}'

            # Raise appropriate exception based on error type
            if is_resource_unavailable:
                raise PrimeintellectResourcesUnavailableError(
                    err,
                    status_code=response.status_code,
                    response_data=response.json()
                    if hasattr(response, 'json') else None)
            else:
                raise PrimeintellectAPIError(
                    err,
                    status_code=response.status_code,
                    response_data=response.json()
                    if hasattr(response, 'json') else None)
    return {}


def get_upstream_cloud_id(instance_type: str) -> Optional[str]:
    global _df, _lookup_dict
    if _df is None:
        _df = catalog_common.read_catalog('primeintellect/vms.csv')
        _lookup_dict = (
            _df.set_index('InstanceType')['UpstreamCloudId'].to_dict())
    return _lookup_dict.get(instance_type)


class PrimeIntellectAPIClient:
    """Client for interacting with Prime Intellect API."""

    def __init__(self) -> None:
        self.credentials = os.path.expanduser(CREDENTIALS_PATH)
        assert os.path.exists(self.credentials), 'Credentials not found'
        with open(self.credentials, 'r', encoding='utf-8') as f:
            self._credentials = json.load(f)
        self.api_key = self._credentials.get('api_key')
        self.team_id = self._credentials.get('team_id')
        self.base_url = self._credentials.get('base_url', DEFAULT_BASE_URL)
        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
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

    def launch(self,
               name: str,
               instance_type: str,
               region: str,
               availability_zone: str,
               disk_size: int,
               vcpus: int = 0,
               memory: int = 0) -> Dict[str, Any]:
        """Create a pod/instance via Prime Intellect API.

        Args:
            name: User-visible name of the pod.
            instance_type: A catalog instance type string. The expected format
                is:
                "<provider>__<accelerator>__<vcpus>__<memory>[_SPOT]".

                - <provider>: Upstream provider tag (e.g., "primecompute").
                - <accelerator>:
                  * GPU nodes: "<N>x<GPU_MODEL>", e.g., "8xH100_80GB".
                  * CPU-only nodes: the literal "CPU_NODE".
                - <vcpus>: Integer string for vCPU count (e.g., "104").
                - <memory>: Integer string for memory in GB (e.g., "752").
                - Optional suffix "_SPOT" may be present in the full string
                  (ignored here; pricing/spot behavior is not controlled by
                  this method).

                Notes:
                - This method only requires the first two components
                  (provider, accelerator) for building the API payload. The
                  vCPU and memory values are provided separately via the
                  ``vcpus`` and ``memory`` arguments; upstream code may also
                  parse them from the instance_type for validation.

            region: Country/region code used by Prime Intellect.
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
        #   provider and accelerator info (see docstring above).
        provider, gpu_parts, _, _ = instance_type.split('__', 3)
        if 'CPU_NODE' in gpu_parts:
            # Prime Intellect API uses the same schema for CPU-only and GPU pods.
            # For CPU-only instances, we set gpuType='CPU_NODE' and gpuCount=1 as a
            # sentinel to indicate "no GPU". This is how CPU instances are represented
            # internally on our platform; the backend does not interpret this as
            # having a physical GPU.
            gpu_type = 'CPU_NODE'
            gpu_count = 1
        else:
            parts = gpu_parts.split('x', 1)
            gpu_count = int(parts[0])
            gpu_type = parts[1]

        payload: Dict[str, Any] = {
            'pod': {
                'name': name,
                'cloudId': cloud_id,
                'socket': 'PCIe',
                'gpuType': gpu_type,
                'gpuCount': int(gpu_count),
                'diskSize': disk_size,
                # Prime Intellect API historically required maxPrice.
                # Set to 0 to indicate on-demand/non-spot pricing.
                'maxPrice': 0,
            },
            'provider': {
                'type': provider,
            }
        }

        if vcpus > 0:
            payload['pod']['vcpus'] = vcpus
        if memory > 0:
            payload['pod']['memory'] = memory

        if region != 'UNSPECIFIED':
            payload['pod']['country'] = region
        if availability_zone != 'UNSPECIFIED':
            payload['pod']['dataCenterId'] = availability_zone

        if self.team_id is not None and self.team_id != '':
            payload['team'] = {'teamId': self.team_id}

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
        # Check if the public key is already added
        ssh_keys = self.list_ssh_keys()
        for key in ssh_keys:
            if key['publicKey'].strip().split()[:2] == ssh_pub_key.strip(
            ).split()[:2]:
                return {'name': key['name'], 'ssh_key': ssh_pub_key}

        # Add the public key to Prime Intellect account if not already added
        ssh_key_name = 'skypilot-' + str(uuid.uuid4()).replace('-', '')[:8]
        _try_request_with_backoff(
            'post',
            f'{self.base_url}/api/v1/ssh_keys',
            headers=self.headers,
            data={
                'name': ssh_key_name,
                'publicKey': ssh_pub_key
            },
        )
        return {'name': ssh_key_name, 'ssh_key': ssh_pub_key}
