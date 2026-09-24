"""RunPod cloud adaptor.

Talks to the RunPod REST API v2 (https://api.runpod.io/v2). The ``runpod``
SDK is only used to resolve the API key from ``~/.runpod/config.toml``.
"""

import os
import time
from typing import Any, Dict, List, Optional
import urllib.parse

from sky.adaptors import common

runpod = common.LazyImport(
    'runpod',
    import_error_message='Failed to import dependencies for RunPod. '
    'Try running: pip install "skypilot[runpod]"')

# Lazy imports
requests = common.LazyImport('requests')

_REST_BASE = 'https://api.runpod.io/v2'
_MAX_RETRIES = 3
_TIMEOUT = 10
_RETRY_SLEEP_SECONDS = 1
_MAX_RETRY_AFTER_SECONDS = 30
# Repeating one of these after a lost response cannot leave a duplicate
# resource behind. A POST is only repeated on 429, which RunPod returns before
# processing the request.
_IDEMPOTENT_METHODS = frozenset({'GET', 'PUT', 'DELETE'})


class RunPodRestError(RuntimeError):
    """A RunPod REST API call failed.

    ``status_code`` is the HTTP status of the failed response, or None for
    network errors.
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code

    def is_auth_error(self) -> bool:
        return self.status_code in (401, 403)


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


def path_segment(value: str) -> str:
    """URL-encodes a path segment (e.g. a GPU id containing spaces)."""
    return urllib.parse.quote(value, safe='')


def _retry_after_seconds(resp: Any) -> float:
    headers = getattr(resp, 'headers', None) or {}
    retry_after = headers.get('Retry-After')
    if retry_after is None:
        return _RETRY_SLEEP_SECONDS
    try:
        return min(float(retry_after), _MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        return _RETRY_SLEEP_SECONDS


def rest_request(method: str,
                 path: str,
                 json: Optional[Dict[str, Any]] = None,
                 params: Optional[Dict[str, Any]] = None) -> Any:
    """Sends a request to the RunPod REST API v2.

    Args:
        method: HTTP method.
        path: Path below the v2 base URL, e.g. '/pods'.
        json: JSON body.
        params: Query parameters.

    Returns:
        The decoded JSON body, the raw text if it is not JSON, or None for
        an empty body (e.g. 204 No Content).

    Idempotent methods (GET, PUT, DELETE) are retried on network errors, 5xx
    and 429. A POST is retried on 429 only, so a create whose response was
    lost is never repeated.

    Raises:
        RunPodRestError: on a 4xx response, on a failed POST, or after
            exhausting retries.
    """
    url = f'{_REST_BASE}{path}'
    idempotent = method.upper() in _IDEMPOTENT_METHODS
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
                                    params=params,
                                    timeout=_TIMEOUT)
        except Exception as e:  # pylint: disable=broad-except
            # Retry on transient network errors
            if not idempotent or attempt >= _MAX_RETRIES:
                raise RunPodRestError(f'RunPod REST network error: {e}') from e
            time.sleep(_RETRY_SLEEP_SECONDS)
            continue

        # Retry on 5xx and 429
        if resp.status_code == 429 or (resp.status_code >= 500 and idempotent):
            if attempt >= _MAX_RETRIES:
                raise RunPodRestError(
                    f'RunPod REST error {resp.status_code}: {resp.text}',
                    status_code=resp.status_code)
            time.sleep(_retry_after_seconds(resp))
            continue

        if resp.status_code >= 400:
            # Non-retryable client error
            raise RunPodRestError(
                f'RunPod REST error {resp.status_code}: {resp.text}',
                status_code=resp.status_code)

        if resp.text:
            try:
                return resp.json()
            except Exception:  # pylint: disable=broad-except
                return resp.text
        return None


def rest_list(path: str, key: str) -> List[Dict[str, Any]]:
    """Lists a v2 collection, following cursor pagination.

    Args:
        path: Collection path, e.g. '/pods'.
        key: Name of the array in the response, e.g. 'pods'.
    """
    items: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        params = {'cursor': cursor} if cursor else None
        resp = rest_request('GET', path, params=params)
        if not isinstance(resp, dict):
            raise RunPodRestError(
                f'Unexpected RunPod response for GET {path}: {resp!r}')
        items.extend(resp.get(key) or [])
        pagination = resp.get('pagination') or {}
        cursor = pagination.get('nextCursor')
        if not pagination.get('hasNextPage') or not cursor:
            return items
