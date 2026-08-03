"""RunPod cloud adaptor."""

from importlib import metadata as import_lib_metadata
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

from packaging import version as version_lib

from sky.adaptors import common

MINIMUM_SDK_VERSION = '1.7.10'

runpod = common.LazyImport(
    'runpod',
    import_error_message='Failed to import dependencies for RunPod. '
    'Try running: pip install "skypilot[runpod]"')

# Lazy imports
requests = common.LazyImport('requests')

_REST_BASE = 'https://rest.runpod.io/v1'
_MAX_RETRIES = 3
_TIMEOUT = 10
_MAX_POD_ID_LENGTH = 128
_UNSAFE_POD_ID_CHARACTERS = frozenset('/\\?#')


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
