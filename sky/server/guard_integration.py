"""Optional fastapi-guard security middleware wiring for the API server.

fastapi-guard (https://github.com/Guard-Core/fastapi-guard) provides IP
block/allow lists, rate limiting with auto-ban, user-agent blocking,
penetration-attempt detection, passive mode, optional Redis-backed shared
state, and optional IPInfo geo/cloud-provider lookups.

Everything here is opt-in: with SKYPILOT_GUARD_ENABLED unset (the default),
attach_guard is a no-op and the server behaves exactly as before. The
dependency carries a python_version >= 3.10 marker, so on 3.9 the flag
raises an actionable error instead of installing.

The screening pass runs outside the auth middlewares, so blocked requests
never reach the bcrypt/DB auth path. It does not touch
request.state.auth_user; whichever auth middleware authenticates still
runs for everything that passes.
"""

import os
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI
    from guard import SecurityConfig

DEFAULT_EXCLUDED_PATHS = '/api/health,/docs,/redoc,/openapi.json'
DEFAULT_TRUSTED_PROXIES = '10.0.0.0/8,172.16.0.0/12,192.168.0.0/16'


def _csv(raw):
    # type: (Optional[str]) -> tuple
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(',') if item.strip())


def _env_int(name, default):
    # type: (str, int) -> int
    raw = os.environ.get(name)
    if raw is None or raw == '':
        return default
    return int(raw)


def _env_bool(name):
    # type: (str) -> bool
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes')


def _build_guard_config():
    # type: () -> SecurityConfig
    """Build the SecurityConfig from the SKYPILOT_GUARD_* env vars."""
    import guard  # pylint: disable=import-outside-toplevel

    kwargs = {
        'enable_rate_limiting': True,
        'rate_limit': _env_int('SKYPILOT_GUARD_RATE_LIMIT', 100),
        'rate_limit_window': _env_int('SKYPILOT_GUARD_RATE_LIMIT_WINDOW', 60),
        'enable_ip_banning': True,
        'auto_ban_threshold': _env_int('SKYPILOT_GUARD_AUTO_BAN_THRESHOLD', 10),
        'auto_ban_duration': _env_int('SKYPILOT_GUARD_AUTO_BAN_DURATION', 300),
        'enable_penetration_detection': True,
        # In-memory state unless a Redis URL is configured: never implicitly
        # depend on a Redis server being reachable. Local `sky api start` is
        # single-process; `sky api start --deploy` runs one worker per CPU,
        # so set SKYPILOT_GUARD_REDIS_URL to share counters and bans there.
        'enable_redis': False,
        'passive_mode': _env_bool('SKYPILOT_GUARD_PASSIVE_MODE'),
        'blacklist': _csv(os.environ.get('SKYPILOT_GUARD_BLOCKED_IPS')),
        'blocked_user_agents': list(
            _csv(os.environ.get('SKYPILOT_GUARD_BLOCKED_USER_AGENTS'))),
        'trusted_proxies': _csv(
            os.environ.get('SKYPILOT_GUARD_TRUSTED_PROXIES'))
                           or _csv(DEFAULT_TRUSTED_PROXIES),
        'trusted_proxy_depth': _env_int('SKYPILOT_GUARD_TRUSTED_PROXY_DEPTH',
                                        1),
        'exclude_paths': list(
            _csv(os.environ.get('SKYPILOT_GUARD_EXCLUDED_PATHS')) or
            DEFAULT_EXCLUDED_PATHS.split(',')),
    }

    if allowed_ips := _csv(os.environ.get('SKYPILOT_GUARD_ALLOWED_IPS')):
        kwargs['whitelist'] = allowed_ips
    if blocked_countries := _csv(
            os.environ.get('SKYPILOT_GUARD_BLOCKED_COUNTRIES')):
        kwargs['blocked_countries'] = frozenset(blocked_countries)
    if allowed_countries := _csv(
            os.environ.get('SKYPILOT_GUARD_ALLOWED_COUNTRIES')):
        kwargs['whitelist_countries'] = frozenset(allowed_countries)
    if cloud_providers := _csv(
            os.environ.get('SKYPILOT_GUARD_BLOCK_CLOUD_PROVIDERS')):
        kwargs['block_cloud_providers'] = frozenset(cloud_providers)

    if redis_url := os.environ.get('SKYPILOT_GUARD_REDIS_URL'):
        kwargs['enable_redis'] = True
        kwargs['redis_url'] = redis_url
        kwargs['redis_prefix'] = 'skypilot_guard:'

    if ipinfo_token := os.environ.get('SKYPILOT_GUARD_IPINFO_TOKEN'):
        kwargs['ipinfo_token'] = ipinfo_token

    return guard.SecurityConfig(**kwargs)


def attach_guard(app):
    # type: (FastAPI) -> None
    """Attach the fastapi-guard middleware when SKYPILOT_GUARD_ENABLED is set.

    No-op unless the env flag is on. Fails loudly when the flag is on but
    the package is missing: a misconfiguration must never silently disable
    security.
    """
    if not _env_bool('SKYPILOT_GUARD_ENABLED'):
        return
    try:
        import guard  # pylint: disable=import-outside-toplevel
    except ImportError as exc:
        raise ImportError(
            'SKYPILOT_GUARD_ENABLED requires fastapi-guard, which needs '
            'python >= 3.10. Install it with: pip install '
            '"skypilot[server]" fastapi-guard') from exc

    app.add_middleware(guard.SecurityMiddleware, config=_build_guard_config())
