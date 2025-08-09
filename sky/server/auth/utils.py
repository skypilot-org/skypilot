"""Utils for auth."""

import os

from sky.server.auth import oauth2_proxy


def is_ingress_oauth2_proxy_enabled() -> bool:
    """Check if ingress OAuth2 proxy is enabled."""
    return os.environ.get(oauth2_proxy.INGRESS_OAUTH2_PROXY_ENABLED_ENV_VAR,
                          'false').lower() == 'true'


def is_api_server_oauth2_proxy_enabled() -> bool:
    """Check if API server OAuth2 proxy is enabled."""
    return os.environ.get(oauth2_proxy.OAUTH2_PROXY_ENABLED_ENV_VAR,
                          'false').lower() == 'true'


def is_oauth2_proxy_enabled() -> bool:
    """Check if OAuth2 proxy is enabled."""
    return (is_ingress_oauth2_proxy_enabled() or
            is_api_server_oauth2_proxy_enabled())
