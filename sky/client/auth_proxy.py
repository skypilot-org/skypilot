"""Credentials for an authenticating proxy in front of the API server.

Some deployments put the API server behind a proxy that authenticates every
request before it reaches SkyPilot, e.g. Google Cloud Identity-Aware Proxy
(IAP) or Cloudflare Access. Browsers get through with the proxy's session
cookie, but such proxies keep that cookie to themselves, so the CLI's cookie
login cannot work. Their supported way for non-browser clients is a short-lived
token in a request header, which this module provides.

Configured in ``~/.sky/config.yaml``::

    api_server:
      endpoint: https://skypilot.example.com
      auth_proxy:
        # Prints a token on stdout, e.g. a Google ID token for IAP.
        command: gcloud auth print-identity-token
        # Optional; these are the defaults.
        header: Proxy-Authorization
        value_template: 'Bearer {token}'

The command's output is cached in memory until the token expires: for a JWT,
until one minute before its ``exp`` claim; otherwise for
``cache_seconds`` (default 300).
"""
import base64
import json
import shlex
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple, Union

from sky import exceptions
from sky import skypilot_config

DEFAULT_HEADER = 'Proxy-Authorization'
DEFAULT_VALUE_TEMPLATE = 'Bearer {token}'
DEFAULT_CACHE_SECONDS = 300
_COMMAND_TIMEOUT_SECONDS = 60
# Refresh a JWT this long before it expires.
_JWT_EXPIRY_MARGIN_SECONDS = 60

_lock = threading.Lock()
# Command (as a tuple) -> (token, monotonic time after which to refresh).
_cache: Dict[Tuple[str, ...], Tuple[str, float]] = {}


def _get_config() -> Optional[Dict[str, object]]:
    config = skypilot_config.get_nested(('api_server', 'auth_proxy'),
                                        default_value=None)
    if not config or not config.get('command'):
        return None
    return config


def _command_argv(command: Union[str, List[str]]) -> List[str]:
    if isinstance(command, str):
        return shlex.split(command)
    return [str(arg) for arg in command]


def _jwt_seconds_to_expiry(token: str) -> Optional[float]:
    """Seconds until a JWT's exp claim, or None if it isn't a JWT with one."""
    parts = token.split('.')
    if len(parts) != 3:
        return None
    payload = parts[1]
    try:
        claims = json.loads(
            base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        return float(claims['exp']) - time.time()
    except (ValueError, KeyError, TypeError):
        return None


def _run_command(argv: List[str]) -> str:
    try:
        proc = subprocess.run(argv,
                              capture_output=True,
                              text=True,
                              timeout=_COMMAND_TIMEOUT_SECONDS,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise exceptions.ApiServerAuthProxyError(
            f'api_server.auth_proxy.command {shlex.join(argv)!r} failed to '
            f'run: {e}') from e
    if proc.returncode != 0:
        raise exceptions.ApiServerAuthProxyError(
            f'api_server.auth_proxy.command {shlex.join(argv)!r} exited with '
            f'code {proc.returncode}: {proc.stderr.strip()[-500:]}')
    token = proc.stdout.strip()
    # The token goes into a header: refuse anything that could add another.
    if not token or any(c in token for c in '\r\n\0'):
        raise exceptions.ApiServerAuthProxyError(
            f'api_server.auth_proxy.command {shlex.join(argv)!r} must print '
            'exactly one non-empty line.')
    return token


def _get_token(argv: List[str], cache_seconds: float) -> str:
    key = tuple(argv)
    with _lock:
        cached = _cache.get(key)
        if cached is not None and time.monotonic() < cached[1]:
            return cached[0]
        token = _run_command(argv)
        ttl = _jwt_seconds_to_expiry(token)
        if ttl is None:
            ttl = cache_seconds
        else:
            ttl -= _JWT_EXPIRY_MARGIN_SECONDS
        _cache[key] = (token, time.monotonic() + max(ttl, 0))
        return token


def get_auth_proxy_headers() -> Dict[str, str]:
    """Headers for the authenticating proxy, or {} if none is configured.

    Raises:
        exceptions.ApiServerAuthProxyError: if the configured command
            fails or prints something that isn't a single-line token.
    """
    config = _get_config()
    if config is None:
        return {}
    argv = _command_argv(config['command'])  # type: ignore[arg-type]
    cache_seconds = float(config.get('cache_seconds',
                                     DEFAULT_CACHE_SECONDS))  # type: ignore
    token = _get_token(argv, cache_seconds)
    header = str(config.get('header', DEFAULT_HEADER))
    template = str(config.get('value_template', DEFAULT_VALUE_TEMPLATE))
    return {header: template.format(token=token)}


def clear_cache() -> None:
    """Forget cached tokens, e.g. after the proxy rejected one."""
    with _lock:
        _cache.clear()
