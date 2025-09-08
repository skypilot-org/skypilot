"""RunPod cloud adaptor.

Provides a thin wrapper around the RunPod SDK/REST and adds request-scoped
credential handling so SkyPilot can safely pass per-request API keys without
affecting global process state.
"""

import contextvars
import os
import time
from typing import Any, Dict, Optional

from sky.adaptors import common

runpod = common.LazyImport(
    'runpod',
    import_error_message='Failed to import dependencies for RunPod. '
    'Try running: pip install "skypilot[runpod]"')

# Lazy imports
requests = common.LazyImport('requests')

_REST_BASE = 'https://rest.runpod.io/v1'
_MAX_RETRIES = 3
_TIMEOUT = 10

# Request-scoped RunPod credentials.
_CTX_RUNPOD_API_KEY: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar('runpod_api_key', default=None))
_CTX_RUNPOD_CONFIG_DIR: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar('runpod_config_dir', default=None))


def _get_thread_runpod_api_key() -> Optional[str]:
    """Get the RunPod API key from the current request context, if any."""
    return _CTX_RUNPOD_API_KEY.get()


def set_thread_runpod_api_key(api_key: str) -> None:
    """Set the RunPod API key for the current request context."""
    _CTX_RUNPOD_API_KEY.set(api_key)


def clear_thread_runpod_api_key() -> None:
    """Clear the RunPod API key from the current request context."""
    _CTX_RUNPOD_API_KEY.set(None)


def set_thread_runpod_config_dir(config_dir: str) -> None:
    """Set a custom RunPod config directory for this request context."""
    _CTX_RUNPOD_CONFIG_DIR.set(os.path.expanduser(config_dir))


def clear_thread_runpod_config_dir() -> None:
    """Clear the custom RunPod config directory for this request context."""
    _CTX_RUNPOD_CONFIG_DIR.set(None)


def get_runpod_config_dir() -> str:
    """Return the RunPod config directory, honoring request-local override."""
    custom = _CTX_RUNPOD_CONFIG_DIR.get()
    if custom:
        return custom
    return os.path.expanduser('~/.runpod')


# For rare SDK-only code paths, allow a short, serialized override of the
# SDK's global api_key. Prefer not to use this; use REST/GraphQL with explicit
# api_key where possible.
import threading
from contextlib import contextmanager

_SDK_API_KEY_LOCK = threading.RLock()


@contextmanager
def with_runpod_sdk_api_key(api_key: Optional[str]):
    """Temporarily set the RunPod SDK global api_key in a serialized section.

    This avoids leaking credentials across threads by guarding with a
    process-local lock. Keep usages minimal and the critical section tight.
    """
    if not api_key:
        # No-op if no inline api key provided.
        yield
        return
    with _SDK_API_KEY_LOCK:
        try:
            import runpod as runpod_sdk  # type: ignore
        except Exception:  # pylint: disable=broad-except
            # SDK not installed; nothing to do.
            yield
            return
        old_key = getattr(runpod_sdk, 'api_key', None)
        runpod_sdk.api_key = api_key
        try:
            yield
        finally:
            runpod_sdk.api_key = old_key


def _get_api_key() -> str:
    # Prefer request-scoped API key if present.
    api_key = _get_thread_runpod_api_key()
    if not api_key:
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
