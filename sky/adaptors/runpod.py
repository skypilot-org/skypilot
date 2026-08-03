"""RunPod cloud adaptor."""

from importlib import metadata as import_lib_metadata
import os
import time
from typing import Any, Dict, Optional

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
