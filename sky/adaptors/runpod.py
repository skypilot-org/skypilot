"""RunPod cloud adaptor."""

from importlib import metadata as import_lib_metadata
import os
import time
from typing import Any, Dict, Iterable, Optional, Set, Tuple
from urllib.parse import quote

from packaging import version as version_lib

from sky.adaptors import common

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

MINIMUM_SDK_VERSION = '1.7.10'

runpod = common.LazyImport(
    'runpod',
    import_error_message='Failed to import dependencies for RunPod. '
    'Try running: pip install "skypilot[runpod]"')

# Lazy imports
requests = common.LazyImport('requests')

_REST_BASE = 'https://rest.runpod.io/v1'
_CATALOG_V2_BASE = 'https://api.runpod.io/v2/catalog'
_MAX_RETRIES = 3
_TIMEOUT = 10
_MAX_POD_ID_LENGTH = 128
_UNSAFE_POD_ID_CHARACTERS = frozenset('/\\?#')
_GPU_AVAILABILITY_TTL_SECONDS = 20
_AVAILABLE_GPU_STATUSES = frozenset(('LOW', 'MEDIUM', 'HIGH'))
_gpu_availability_cache: Dict[Tuple[str, int, str, Tuple[str, ...]],
                              Tuple[float, Set[str]]] = {}

# RunPod's catalog instance names are shorter than the GPU identifiers used by
# both the v1 and v2 APIs.
GPU_NAME_MAP = {
    'MI300X': 'AMD Instinct MI300X OAM',
    'A100-80GB': 'NVIDIA A100 80GB PCIe',
    'A100-80GB-SXM': 'NVIDIA A100-SXM4-80GB',
    'A30': 'NVIDIA A30',
    'A40': 'NVIDIA A40',
    'B200': 'NVIDIA B200',
    'RTX3070': 'NVIDIA GeForce RTX 3070',
    'RTX3080': 'NVIDIA GeForce RTX 3080',
    'RTX3080Ti': 'NVIDIA GeForce RTX 3080 Ti',
    'RTX3090': 'NVIDIA GeForce RTX 3090',
    'RTX3090Ti': 'NVIDIA GeForce RTX 3090 Ti',
    'RTX4070Ti': 'NVIDIA GeForce RTX 4070 Ti',
    'RTX4080': 'NVIDIA GeForce RTX 4080',
    'RTX4080SUPER': 'NVIDIA GeForce RTX 4080 SUPER',
    'RTX4090': 'NVIDIA GeForce RTX 4090',
    'RTX5080': 'NVIDIA GeForce RTX 5080',
    'RTX5090': 'NVIDIA GeForce RTX 5090',
    'H100-SXM': 'NVIDIA H100 80GB HBM3',
    'H100-NVL': 'NVIDIA H100 NVL',
    'H100': 'NVIDIA H100 PCIe',
    'H200-SXM': 'NVIDIA H200',
    'L4': 'NVIDIA L4',
    'L40': 'NVIDIA L40',
    'L40S': 'NVIDIA L40S',
    'RTX2000-Ada': 'NVIDIA RTX 2000 Ada Generation',
    'RTX4000-Ada': 'NVIDIA RTX 4000 Ada Generation',
    'RTX4000-Ada-SFF': 'NVIDIA RTX 4000 SFF Ada Generation',
    'RTX5000-Ada': 'NVIDIA RTX 5000 Ada Generation',
    'RTX6000-Ada': 'NVIDIA RTX 6000 Ada Generation',
    'RTXA2000': 'NVIDIA RTX A2000',
    'RTXA4000': 'NVIDIA RTX A4000',
    'RTXA4500': 'NVIDIA RTX A4500',
    'RTXA5000': 'NVIDIA RTX A5000',
    'RTXA6000': 'NVIDIA RTX A6000',
    'RTXPRO4500': 'NVIDIA RTX PRO 4500 Blackwell',
    'RTXPRO6000': 'NVIDIA RTX PRO 6000 Blackwell Server Edition',
    'RTXPRO6000-WK': 'NVIDIA RTX PRO 6000 Blackwell Workstation Edition',
    'V100-16GB-FHHL': 'Tesla V100-FHHL-16GB',
    'V100-16GB-SXM2': 'Tesla V100-SXM2-16GB',
    'V100-32GB-SXM2': 'Tesla V100-SXM2-32GB',
    'V100-16GB-PCIe': 'Tesla V100-PCIE-16GB',
}


def get_sdk_version_error() -> Optional[str]:
    """Return an actionable error when the RunPod SDK is not supported."""
    try:
        installed_version = import_lib_metadata.version('runpod')
    except import_lib_metadata.PackageNotFoundError:
        return ('RunPod SDK is not installed. Install it with: pip install '
                '"skypilot[runpod]".')
    try:
        supported = (version_lib.Version(installed_version) >=
                     version_lib.Version(MINIMUM_SDK_VERSION))
    except version_lib.InvalidVersion:
        return (f'RunPod SDK version {installed_version!r} is invalid. Install '
                'a supported version with: pip install "skypilot[runpod]".')
    if supported:
        return None
    return (f'RunPod SDK {installed_version} is too old. Install '
            f'"runpod>={MINIMUM_SDK_VERSION}" with: pip install '
            '"skypilot[runpod]".')


def _get_api_key() -> str:
    api_key = getattr(runpod, 'api_key', None)
    if not api_key:
        # Fallback to env if SDK global not set
        api_key = os.environ.get('RUNPOD_API_KEY')
    if not api_key:
        raise RuntimeError(
            'RunPod API key is not set. Please set runpod.api_key '
            'or RUNPOD_API_KEY.')
    return str(api_key)


def ensure_api_key_configured() -> None:
    """Load the default RunPod credential into the SDK when needed."""
    sdk = runpod.load_module()
    if getattr(sdk, 'api_key', None):
        return

    credential_file = os.path.expanduser('~/.runpod/config.toml')
    try:
        with open(credential_file, 'rb') as credential_stream:
            config = tomllib.load(credential_stream)
    except FileNotFoundError:
        return
    except (OSError, TypeError, ValueError):
        return

    api_key = config.get('default', {}).get('api_key')
    if isinstance(api_key, str) and api_key:
        sdk.api_key = api_key


def _catalog_v2_request(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch a RunPod v2 catalog response without exposing response bodies."""
    response = requests.get(
        f'{_CATALOG_V2_BASE}{path}',
        headers={'Authorization': f'Bearer {_get_api_key()}'},
        params=params,
        timeout=_TIMEOUT)
    if response.status_code >= 400:
        raise RuntimeError('RunPod v2 catalog request failed with status '
                           f'{response.status_code}.')
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError('RunPod v2 catalog returned a malformed response.')
    return result


def _data_center_ids_from_gpu_response(response: Dict[str, Any]) -> Set[str]:
    """Return available data-center IDs from an exact v2 GPU response."""
    availability = response.get('availability')
    data_centers = response.get('dataCenters', [])
    if availability == 'NONE' and data_centers in (None, []):
        return set()
    if availability not in _AVAILABLE_GPU_STATUSES or not isinstance(
            data_centers, list):
        raise ValueError('RunPod v2 GPU availability response is malformed.')
    available_data_centers = set()
    for data_center in data_centers:
        if not isinstance(data_center, dict):
            raise ValueError(
                'RunPod v2 GPU availability response is malformed.')
        data_center_id = data_center.get('id')
        if not isinstance(data_center_id, str):
            raise ValueError(
                'RunPod v2 GPU availability response is malformed.')
        if data_center.get('availability') in _AVAILABLE_GPU_STATUSES:
            available_data_centers.add(data_center_id)
    return available_data_centers


def _normalized_country_codes(
        country_codes: Optional[Iterable[str]]) -> Tuple[str, ...]:
    if country_codes is None:
        return ()
    return tuple(sorted(country_codes))


def get_live_gpu_data_center_ids(
        gpu_type_id: str,
        gpu_count: int,
        cloud_type: str,
        country_codes: Optional[Iterable[str]] = None,
        *,
        force_refresh: bool = False) -> Optional[Set[str]]:
    """Return live v2 capacity, or ``None`` when the query is unavailable."""
    normalized_countries = _normalized_country_codes(country_codes)
    cache_key = (gpu_type_id, gpu_count, cloud_type, normalized_countries)
    cached = _gpu_availability_cache.get(cache_key)
    if (not force_refresh and cached is not None and
            time.time() - cached[0] < _GPU_AVAILABILITY_TTL_SECONDS):
        return cached[1]
    params: Dict[str, Any] = {
        'include': 'AVAILABILITY',
        'product': 'POD',
        'count': gpu_count,
        'cloud': cloud_type,
    }
    if normalized_countries:
        params['countryCodes'] = ','.join(normalized_countries)
    try:
        response = _catalog_v2_request(f'/gpus/{quote(gpu_type_id, safe="")}',
                                       params)
        available_data_centers = _data_center_ids_from_gpu_response(response)
    except Exception:  # pylint: disable=broad-except
        return None
    _gpu_availability_cache[cache_key] = (time.time(), available_data_centers)
    return available_data_centers


def available_data_center_ids_for_instance_type(
        instance_type: str,
        country_codes: Optional[Iterable[str]] = None,
        *,
        force_refresh: bool = False) -> Optional[Set[str]]:
    """Return live capacity for a RunPod catalog instance type."""
    if instance_type.startswith('cpu'):
        return None
    try:
        count_text, gpu_name, cloud_type = instance_type.split('_')
        gpu_count = int(
            count_text[:-1] if count_text.endswith('x') else count_text)
        gpu_type_id = GPU_NAME_MAP[gpu_name]
    except (KeyError, TypeError, ValueError):
        return None
    return get_live_gpu_data_center_ids(gpu_type_id,
                                        gpu_count,
                                        cloud_type,
                                        country_codes,
                                        force_refresh=force_refresh)


def get_catalog_gpu_data_center_ids(gpu_count: int,
                                    cloud_type: str) -> Dict[str, Set[str]]:
    """Return v2 available GPU-to-data-center pairs for a catalog snapshot."""
    response = _catalog_v2_request(
        '/gpus', {
            'include': 'AVAILABILITY',
            'product': 'POD',
            'count': gpu_count,
            'cloud': cloud_type,
        })
    gpu_entries = response.get('gpus')
    if not isinstance(gpu_entries, list):
        raise RuntimeError('RunPod v2 catalog returned a malformed response.')
    gpu_data_centers = {}
    for gpu in gpu_entries:
        if not isinstance(gpu, dict) or not isinstance(gpu.get('id'), str):
            raise RuntimeError(
                'RunPod v2 catalog returned a malformed response.')
        gpu_data_centers[gpu['id']] = _data_center_ids_from_gpu_response(gpu)
    return gpu_data_centers


def terminate_current_pod() -> None:
    """Delete the RunPod pod identified by the process environment.

    This uses the pod-scoped identity injected into the workload. It is kept
    separate from ``rest_request()`` so failures never expose provider response
    bodies or credentials.
    """
    # RunPod injects the current pod's ID and pod-scoped API key. Do not fall
    # back to _get_api_key(): controller or local credentials could target a
    # different pod. See:
    # https://docs.runpod.io/pods/templates/environment-variables
    pod_id = os.environ.get('RUNPOD_POD_ID')
    if not pod_id:
        raise RuntimeError(
            'RunPod self-termination requires RUNPOD_POD_ID to be set.')
    invalid_pod_id = (len(pod_id) > _MAX_POD_ID_LENGTH or
                      pod_id in ('.', '..') or
                      any(not character.isprintable() or character.isspace() or
                          character in _UNSAFE_POD_ID_CHARACTERS
                          for character in pod_id))
    if invalid_pod_id:
        raise RuntimeError(
            'RunPod self-termination requires a valid RUNPOD_POD_ID.')
    api_key = os.environ.get('RUNPOD_API_KEY')
    if not api_key:
        raise RuntimeError(
            'RunPod self-termination requires RUNPOD_API_KEY to be set.')

    url = f'{_REST_BASE}/pods/{quote(pod_id, safe="")}'
    headers = {'Authorization': f'Bearer {api_key}'}
    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.request('DELETE',
                                        url,
                                        headers=headers,
                                        timeout=_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == _MAX_RETRIES - 1:
                raise RuntimeError(
                    'RunPod self-termination failed due to a network error.'
                ) from None
            time.sleep(1)
            continue
        except requests.RequestException:
            raise RuntimeError(
                'RunPod self-termination failed due to a request error.'
            ) from None

        status_code = response.status_code
        if 200 <= status_code < 300 or status_code in (404, 410):
            return None
        if status_code == 429 or 500 <= status_code < 600:
            if attempt == _MAX_RETRIES - 1:
                raise RuntimeError('RunPod self-termination failed with status '
                                   f'{status_code}.')
            time.sleep(1)
            continue
        raise RuntimeError('RunPod self-termination failed with status '
                           f'{status_code}.')


def rest_request(method: str,
                 path: str,
                 json: Optional[Dict[str, Any]] = None) -> Any:
    url = f'{_REST_BASE}{path}'
    headers = {
        'Authorization': f'Bearer {_get_api_key()}',
        'Content-Type': 'application/json',
    }
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = requests.request(method,
                                    url,
                                    headers=headers,
                                    json=json,
                                    timeout=_TIMEOUT)
        except Exception as e:  # pylint: disable=broad-except
            # Retry on transient network errors
            if attempt >= _MAX_RETRIES:
                raise RuntimeError(f'RunPod REST network error: {e}') from e
            time.sleep(1)
            continue

        # Retry on 5xx and 429
        if resp.status_code >= 500 or resp.status_code == 429:
            if attempt >= _MAX_RETRIES:
                raise RuntimeError(
                    f'RunPod REST error {resp.status_code}: {resp.text}')
            time.sleep(1)
            continue

        if resp.status_code >= 400:
            # Non-retryable client error
            raise RuntimeError(
                f'RunPod REST error {resp.status_code}: {resp.text}')

        if resp.text:
            try:
                return resp.json()
            except Exception:  # pylint: disable=broad-except
                return resp.text
        return None
