"""Runcrate API client and shared helpers.

Runcrate's public API is documented at https://www.runcrate.ai/docs. Every
request authenticates with an API key, which is scoped to one workspace
environment.
"""
import base64
import hashlib
import os
import time
import typing
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.adaptors import common as adaptors_common

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)

API_URL = 'https://www.runcrate.ai/api/v1'
CREDENTIALS_PATH = '~/.runcrate/api_key'
API_KEY_URL = 'https://www.runcrate.ai/dashboard/api-keys'

# Launching can try several machines within one request, which the API caps
# at 60 seconds.
_TIMEOUT_SECONDS = 90
_MAX_RETRIES = 4
_RETRY_BACKOFF_SECONDS = 5

# Runcrate GPU type -> (SkyPilot accelerator name, memory per GPU in GiB,
# manufacturer). Offers with a GPU type missing here are left out of the
# catalog and are never launched.
GPU_TYPES: Dict[str, Tuple[str, int, str]] = {
    'A10': ('A10', 24, 'NVIDIA'),
    'A16': ('A16', 16, 'NVIDIA'),
    'A30': ('A30', 24, 'NVIDIA'),
    'A40': ('A40', 48, 'NVIDIA'),
    'A100': ('A100', 40, 'NVIDIA'),
    'A100_80G': ('A100-80GB', 80, 'NVIDIA'),
    'A4000': ('A4000', 16, 'NVIDIA'),
    'A5000': ('A5000', 24, 'NVIDIA'),
    'A6000': ('A6000', 48, 'NVIDIA'),
    'B200': ('B200', 180, 'NVIDIA'),
    'B300': ('B300', 288, 'NVIDIA'),
    'GAUDI2': ('GAUDI2', 96, 'Intel'),
    'GH200': ('GH200', 96, 'NVIDIA'),
    'H100': ('H100', 80, 'NVIDIA'),
    'H100_nvl': ('H100-NVL', 94, 'NVIDIA'),
    'H200': ('H200', 141, 'NVIDIA'),
    'L4': ('L4', 24, 'NVIDIA'),
    'L40': ('L40', 48, 'NVIDIA'),
    'L40S': ('L40S', 48, 'NVIDIA'),
    'MI300X': ('MI300X', 192, 'AMD'),
    'RTX3090': ('RTX3090', 24, 'NVIDIA'),
    'RTX4000Ada': ('RTX4000-Ada', 20, 'NVIDIA'),
    'RTX4090': ('RTX4090', 24, 'NVIDIA'),
    'RTX5090': ('RTX5090', 32, 'NVIDIA'),
    'RTX6000Ada': ('RTX6000-Ada', 48, 'NVIDIA'),
    'RTXPro6000': ('RTXPRO6000', 96, 'NVIDIA'),
    'V100': ('V100', 16, 'NVIDIA'),
    'V100_32G': ('V100-32GB', 32, 'NVIDIA'),
}


class RuncrateError(Exception):
    """An error returned by the Runcrate API."""

    def __init__(self,
                 message: str,
                 status_code: Optional[int] = None,
                 code: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def read_api_key() -> str:
    path = os.path.expanduser(CREDENTIALS_PATH)
    if not os.path.exists(path):
        raise RuncrateError(
            f'Runcrate API key not found at {CREDENTIALS_PATH}.')
    with open(path, 'r', encoding='utf-8') as f:
        api_key = f.read().strip()
    if not api_key:
        raise RuncrateError(f'Runcrate API key file {CREDENTIALS_PATH} is '
                            'empty.')
    return api_key


def _error_message(response: 'requests.Response') -> Tuple[str, Optional[str]]:
    try:
        error = response.json().get('error') or {}
        return error.get('message') or response.text, error.get('code')
    except ValueError:
        return response.text, None


def _request(method: str,
             path: str,
             params: Optional[Dict[str, Any]] = None,
             body: Optional[Dict[str, Any]] = None) -> Any:
    """Sends one API request and returns the `data` of its answer.

    Rate limits are retried for every method, after the time the API asks
    for. Server errors and dropped connections are retried only for GET and
    DELETE: a retried POST could create a second instance.
    """
    headers = {
        'Authorization': f'Bearer {read_api_key()}',
        'Accept': 'application/json',
    }
    url = f'{API_URL}{path}'
    for attempt in range(_MAX_RETRIES + 1):
        retry_after = None
        try:
            response = requests.request(method,
                                        url,
                                        headers=headers,
                                        params=params,
                                        json=body,
                                        timeout=_TIMEOUT_SECONDS)
        except requests.exceptions.RequestException as e:
            if method == 'POST' or attempt == _MAX_RETRIES:
                raise RuncrateError(
                    f'Runcrate API {method} {path} failed: {e}') from e
        else:
            if response.status_code < 400:
                if response.status_code == 204 or not response.content:
                    return None
                return response.json().get('data')
            retryable = (response.status_code == 429 or
                         (response.status_code >= 500 and method != 'POST'))
            if not retryable or attempt == _MAX_RETRIES:
                message, code = _error_message(response)
                raise RuncrateError(
                    f'Runcrate API {method} {path} failed '
                    f'({response.status_code}): {message}',
                    status_code=response.status_code,
                    code=code)
            retry_after = response.headers.get('Retry-After')
        delay = (int(retry_after) if retry_after and retry_after.isdigit() else
                 _RETRY_BACKOFF_SECONDS * (attempt + 1))
        logger.debug(f'Retrying Runcrate API {method} {path} in {delay}s.')
        time.sleep(delay)
    raise AssertionError('unreachable')


# ---------------------------------------------------------------- offers


def region_name(region: str) -> str:
    """The SkyPilot region name of a Runcrate region code.

    Some codes contain '/', which SkyPilot reads as the region/zone separator
    in infra strings, so it is replaced with '-'.
    """
    return region.replace('/', '-')


def instance_type_name(offer: Dict[str, Any]) -> Optional[str]:
    """The SkyPilot instance type of an offer from /instances/types.

    Offers are ephemeral, so the instance type names the shape instead:
    GPU type and count, vCPUs and memory, e.g. `1x_A6000_6c_48g` or
    `cpu_8c_32g`. None for an offer whose GPU type is not mapped.
    """
    vcpus = offer['cpu_cores']
    memory = offer['memory_gb']
    if not offer['gpu_count']:
        return f'cpu_{vcpus:g}c_{memory:g}g'
    gpu = GPU_TYPES.get(offer['gpu_type'])
    if gpu is None:
        return None
    return f'{offer["gpu_count"]}x_{gpu[0]}_{vcpus:g}c_{memory:g}g'


def is_supported_offer(offer: Dict[str, Any]) -> bool:
    """Whether SkyPilot can use an offer.

    Only datacenter machines with their own address: a port-mapped machine
    sits behind its host's shared address, where SSH is not on port 22.
    """
    return (offer.get('host_tier', 'datacenter') == 'datacenter' and
            not offer.get('port_mapped', False) and
            instance_type_name(offer) is not None)


def list_offers(gpu_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lists the offers that can be launched right now."""
    params = {'gpu_type': gpu_type} if gpu_type else None
    offers = _request('GET', '/instances/types', params=params) or []
    return [offer for offer in offers if is_supported_offer(offer)]


def find_offer(instance_type: str, region: str) -> Optional[Dict[str, Any]]:
    """The cheapest live offer of an instance type in a region."""
    matches = [
        offer for offer in list_offers()
        if instance_type_name(offer) == instance_type and
        region_name(offer['region']) == region
    ]
    if not matches:
        return None
    return min(matches, key=lambda offer: offer['hourly_rate'])


# ------------------------------------------------------------- instances


def list_instances(name_prefix: str) -> List[Dict[str, Any]]:
    """Lists starting and running instances whose name contains a string."""
    return _request('GET', '/instances', params={'search': name_prefix}) or []


def create_instance(name: str, instance_type_id: str,
                    ssh_key_id: str) -> Dict[str, Any]:
    return _request('POST',
                    '/instances',
                    body={
                        'name': name,
                        'instance_type_id': instance_type_id,
                        'ssh_key_id': ssh_key_id,
                    })


def get_instance_status(instance_id: str) -> Dict[str, Any]:
    """Status and address of an instance, advancing its launch."""
    return _request('GET', f'/instances/{instance_id}/status')


def delete_instance(instance_id: str) -> None:
    try:
        _request('DELETE', f'/instances/{instance_id}')
    except RuncrateError as e:
        if e.status_code != 404:
            raise


# --------------------------------------------------------------- SSH keys


def _fingerprint(public_key: str) -> str:
    """The SHA256 fingerprint of an OpenSSH public key, as ssh-keygen -l."""
    blob = base64.b64decode(public_key.split()[1])
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode()
    return 'SHA256:' + digest.rstrip('=')


def list_ssh_keys() -> List[Dict[str, Any]]:
    """The workspace's SSH keys (also how `sky check` tests the API key)."""
    return _request('GET', '/ssh-keys') or []


def get_or_add_ssh_key(public_key: str, name: str) -> str:
    """Returns the id of the workspace SSH key, adding it if missing."""
    fingerprint = _fingerprint(public_key)
    for key in list_ssh_keys():
        if key.get('fingerprint') == fingerprint:
            return key['id']
    try:
        key = _request('POST',
                       '/ssh-keys',
                       body={
                           'name': name,
                           'public_key': public_key
                       })
    except RuncrateError as e:
        if e.status_code != 409:
            raise
        # The name is taken by another key: keep both.
        key = _request('POST',
                       '/ssh-keys',
                       body={
                           'name': f'{name}-{fingerprint[7:15]}',
                           'public_key': public_key
                       })
    return key['id']


# --------------------------------------------------------------- firewall


def list_firewall_rules() -> List[Dict[str, Any]]:
    """The firewall rules of the API key's environment."""
    rules = _request('GET', '/environments/current/firewall') or {}
    return rules.get('rules', [])


def add_firewall_rule(port_from: int, port_to: int, description: str) -> None:
    _request('POST',
             '/environments/current/firewall',
             body={
                 'protocol': 'tcp',
                 'port_from': port_from,
                 'port_to': port_to,
                 'sources': ['anywhere'],
                 'description': description,
             })
