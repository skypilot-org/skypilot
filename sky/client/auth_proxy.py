"""Credentials for an authenticating proxy in front of the API server.

Some deployments put the API server behind a proxy that authenticates every
request before it reaches SkyPilot, e.g. Google Cloud Identity-Aware Proxy
(IAP) or Cloudflare Access. Browsers get through with the proxy's session
cookie, but such proxies keep that cookie to themselves, so the CLI's cookie
login cannot work. Their supported way for non-browser clients is a short-lived
token in a request header, which this module provides.

Configured in the user's own config file (``~/.sky/config.yaml``), next to the
endpoint it is for::

    api_server:
      endpoint: https://skypilot.example.com
      auth_proxy:
        # Prints a token on stdout, e.g. a Google ID token for IAP.
        command: gcloud auth print-identity-token
        # Optional; these are the defaults.
        header: Proxy-Authorization
        value_template: 'Bearer {token}'

Only that file counts: a project's ``.sky.yaml`` or a ``--config`` override
must not be able to make the client run a command. And the token is sent only
to that endpoint, so pointing the client at another server (for example with
``SKYPILOT_API_SERVER_ENDPOINT``) never hands it the token.

The command's output is cached in memory until the token expires: for a JWT,
until one minute before its ``exp`` claim; otherwise for ``cache_seconds``
(default 300).
"""
import base64
import json
import os
import shlex
import string
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import urllib.parse

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.client import service_account_auth

logger = sky_logging.init_logger(__name__)

DEFAULT_HEADER = 'Proxy-Authorization'
DEFAULT_VALUE_TEMPLATE = 'Bearer {token}'
DEFAULT_CACHE_SECONDS = 300
_COMMAND_TIMEOUT_SECONDS = 60
# Refresh a JWT this long before it expires.
_JWT_EXPIRY_MARGIN_SECONDS = 60

# Held while running the command, so concurrent callers run it once.
_lock = threading.Lock()
# Command (as a tuple) -> (token, monotonic time after which to refresh).
_cache: Dict[Tuple[str, ...], Tuple[str, float]] = {}
# The user config's (auth_proxy, endpoint), keyed by the file's identity, so
# requests don't re-parse it.
_settings_cache: Optional[Tuple[Tuple[Any, ...], Optional[Dict[str, Any]],
                                Optional[str]]] = None


def _user_settings() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """api_server.auth_proxy and api_server.endpoint from the user config."""
    global _settings_cache
    path = skypilot_config.resolve_user_config_path()
    if path is None:
        return None, None
    try:
        stat = os.stat(path)
    except OSError:
        return None, None
    key = (path, stat.st_mtime_ns, stat.st_size)
    cached = _settings_cache
    if cached is not None and cached[0] == key:
        return cached[1], cached[2]
    config = skypilot_config.get_user_config()
    settings = config.get_nested(('api_server', 'auth_proxy'), None)
    endpoint = config.get_nested(('api_server', 'endpoint'), None)
    _settings_cache = (key, settings, endpoint)
    return settings, endpoint


def _origin(url: Optional[str]) -> Optional[Tuple[str, str, int]]:
    """(scheme, host, port) of an http(s) or ws(s) URL, ws mapped to http."""
    if not url:
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        scheme = {
            'ws': 'http',
            'wss': 'https'
        }.get(parsed.scheme.lower(), parsed.scheme.lower())
        port = parsed.port
    except ValueError:
        return None
    if scheme not in ('http', 'https') or not parsed.hostname:
        return None
    if port is None:
        port = 443 if scheme == 'https' else 80
    return scheme, parsed.hostname.lower(), port


def same_server(url_a: Optional[str], url_b: Optional[str]) -> bool:
    """Whether two URLs point at the same server (scheme, host and port)."""
    origin = _origin(url_a)
    return origin is not None and origin == _origin(url_b)


def _settings_for(url: str) -> Optional[Dict[str, Any]]:
    """The auth_proxy settings if they apply to a request to `url`."""
    settings, endpoint = _user_settings()
    if not settings or not settings.get('command'):
        return None
    if not same_server(url, endpoint):
        logger.debug('api_server.auth_proxy is configured for '
                     f'{endpoint!r}, not sending its token to {url!r}.')
        return None
    return settings


def _command_argv(command: Union[str, List[str]]) -> List[str]:
    if isinstance(command, str):
        return shlex.split(command)
    return [str(arg) for arg in command]


def _check_value_template(template: str) -> None:
    """The template must use {token}, and no other field."""
    try:
        fields = [
            (name, conversion, spec)
            for _, name, spec, conversion in string.Formatter().parse(template)
            if name is not None
        ]
    except ValueError:
        fields = []
    if not fields or any(field != ('token', None, '') for field in fields):
        raise exceptions.ApiServerAuthProxyError(
            f'api_server.auth_proxy.value_template {template!r} must contain '
            '{token} and no other {...} field (write {{ or }} for a literal '
            'brace), as in the default: Bearer {token}')


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


def _cached_token(key: Tuple[str, ...]) -> Optional[str]:
    cached = _cache.get(key)
    if cached is not None and time.monotonic() < cached[1]:
        return cached[0]
    return None


def _get_token(argv: List[str], cache_seconds: float) -> str:
    key = tuple(argv)
    with _lock:
        token = _cached_token(key)
        if token is not None:
            return token
        token = _run_command(argv)
        ttl = _jwt_seconds_to_expiry(token)
        if ttl is None:
            ttl = cache_seconds
        else:
            ttl -= _JWT_EXPIRY_MARGIN_SECONDS
        _cache[key] = (token, time.monotonic() + max(ttl, 0))
        return token


def get_auth_proxy_headers(url: str) -> Dict[str, str]:
    """The proxy's header for a request to `url`, or {} if none applies.

    Raises:
        exceptions.ApiServerAuthProxyError: if the configured command
            fails or prints something that isn't a single-line token, or
            value_template is unusable.
    """
    settings = _settings_for(url)
    if settings is None:
        return {}
    header = str(settings.get('header', DEFAULT_HEADER))
    template = str(settings.get('value_template', DEFAULT_VALUE_TEMPLATE))
    _check_value_template(template)
    token = _get_token(
        _command_argv(settings['command']),
        float(settings.get('cache_seconds', DEFAULT_CACHE_SECONDS)))
    return {header: template.format(token=token)}


def get_auth_headers(url: str) -> Dict[str, str]:
    """All credential headers for a request to the API server at `url`.

    The authenticating proxy's token (if configured for this server) and the
    service account token (if any). They must use different headers: a proxy
    that reads Authorization can't be combined with a service account token.

    Raises:
        exceptions.ApiServerAuthProxyError: as get_auth_proxy_headers, or if
            both credentials would use the same header.
    """
    headers = get_auth_proxy_headers(url)
    sa_headers = service_account_auth.get_service_account_headers()
    clash = {h.lower() for h in headers} & {h.lower() for h in sa_headers}
    if clash:
        raise exceptions.ApiServerAuthProxyError(
            f'api_server.auth_proxy.header {sorted(clash)[0]!r} is also where '
            'the service account token goes. Use a header the proxy reads '
            'besides that one (IAP reads Proxy-Authorization, the default), '
            'or remove one of the two credentials.')
    headers.update(sa_headers)
    return headers


def token_needs_refresh(url: str) -> bool:
    """Whether getting headers for `url` would run the command first."""
    settings = _settings_for(url)
    if settings is None:
        return False
    return _cached_token(tuple(_command_argv(settings['command']))) is None


def clear_cache() -> None:
    """Forget cached tokens, e.g. after the proxy rejected one."""
    global _settings_cache
    with _lock:
        _cache.clear()
        _settings_cache = None
