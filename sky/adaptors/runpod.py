"""RunPod cloud adaptor."""

import json as json_lib
import os
import time
from typing import Any, Dict, Optional

from sky.adaptors import common
from sky.utils import annotations

runpod = common.LazyImport(
    'runpod',
    import_error_message='Failed to import dependencies for RunPod. '
    'Try running: pip install "skypilot[runpod]"')

# Lazy imports
requests = common.LazyImport('requests')

_REST_BASE = 'https://rest.runpod.io/v1'
_MAX_RETRIES = 3
_TIMEOUT = 10


@annotations.ttl_cache(scope='request', maxsize=128, ttl=60)
def get_gpu_host_quote(gpu_id: str, gpu_count: int, secure: bool, cpus: int,
                       memory_gb: int,
                       region: Optional[str]) -> Optional[Dict[str, Any]]:
    """Cache a bounded host quote for resource selection."""
    return _get_gpu_host_quote(gpu_id, gpu_count, secure, cpus, memory_gb,
                               region)


def _get_gpu_host_quote(gpu_id: str, gpu_count: int, secure: bool, cpus: int,
                        memory_gb: int,
                        region: Optional[str]) -> Optional[Dict[str, Any]]:
    """Read a fresh host quote without reserving capacity."""
    fields = [
        f'gpuCount: {gpu_count}', f'secureCloud: {str(secure).lower()}',
        f'minVcpuCount: {cpus}', f'minMemoryInGb: {memory_gb}'
    ]
    if region is not None:
        fields.append(f'countryCode: {json_lib.dumps(region)}')
    query = ('query { gpuTypes(input: {id: ' + json_lib.dumps(gpu_id) + '}) {'
             ' lowestPrice(input: {' + ', '.join(fields) + '}) {'
             ' uninterruptablePrice minVcpu minMemory stockStatus '
             'availableGpuCounts } } }')
    try:
        response = requests.post(
            'https://api.runpod.io/graphql',
            headers={'Authorization': f'Bearer {_get_api_key()}'},
            json={'query': query},
            timeout=_TIMEOUT)
        response.raise_for_status()
        result = response.json()
        if result.get('errors'):
            return None
        rows = result['data']['gpuTypes']
        return rows[0]['lowestPrice'] if len(rows) == 1 else None
    except Exception:  # pylint: disable=broad-except
        # Unavailable credentials, transport or malformed data is not an offer.
        # Avoid logging response/exception text that may contain credentials.
        return None


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
