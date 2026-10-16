"""Shared loopback detection utilities for auth middlewares."""

import ipaddress

import fastapi

from sky import sky_logging
from sky import skypilot_config

logger = sky_logging.init_logger(__name__)

COMMON_PROXY_HEADERS = [
    'X-Forwarded-For', 'Forwarded', 'X-Real-IP', 'X-Client-IP',
    'X-Forwarded-Host', 'X-Forwarded-Proto'
]


def _is_loopback_ip(ip_str: str) -> bool:
    """Check if an IP address is a loopback address."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_loopback
    except ValueError:
        return False


def is_loopback_request(request: fastapi.Request) -> bool:
    """Determine if a request is coming from localhost.

    Callers use this to exempt local traffic from authentication, so the
    exemption can be turned off with `api_server.trust_loopback: false`.
    That matters on Kubernetes: `kubectl port-forward` delivers a remote
    client's traffic to the pod from 127.0.0.1 with no proxy headers, which
    is indistinguishable here from traffic that never left the host. A
    deployment that authenticates every caller (e.g. behind oauth2-proxy)
    can opt out so port-forwarded requests are authenticated like any other.
    """
    if not skypilot_config.get_nested(('api_server', 'trust_loopback'), True):
        return False

    if request.client is None:
        return False

    client_host = request.client.host
    if client_host == 'localhost' or _is_loopback_ip(client_host):
        # Additional checks: ensure no forwarding headers are present.
        # If there are any, assume this traffic went through a proxy.
        return not any(
            request.headers.get(header) for header in COMMON_PROXY_HEADERS)

    return False
