"""Shared loopback detection utilities for auth middlewares."""

import ipaddress
from typing import Optional

import fastapi

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Headers that might indicate the original client IP when behind a proxy
FORWARDED_IP_HEADERS = ['X-Forwarded-For', 'X-Real-IP', 'X-Client-IP']


def _is_loopback_ip(ip_str: str) -> bool:
    """Check if an IP address is a loopback address."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_loopback
    except ValueError:
        return False


def _get_forwarded_client_ip(request: fastapi.Request) -> Optional[str]:
    """Extract the original client IP from forwarding headers.

    Returns the first IP from X-Forwarded-For, X-Real-IP, or X-Client-IP.
    For X-Forwarded-For, returns the leftmost (original client) IP.
    """
    # Check X-Forwarded-For first (most common)
    xff = request.headers.get('X-Forwarded-For')
    if xff:
        # X-Forwarded-For can be a comma-separated list; first IP is the client
        first_ip = xff.split(',')[0].strip()
        if first_ip:
            return first_ip

    # Check other headers
    for header in ['X-Real-IP', 'X-Client-IP']:
        value = request.headers.get(header)
        if value:
            return value.strip()

    return None


def is_loopback_request(request: fastapi.Request) -> bool:
    """Determine if a request is coming from localhost.

    Returns True if:
    1. The direct client IP is loopback AND no forwarding headers indicate
       a different original client, OR
    2. The direct client IP is loopback AND the forwarded client IP is also
       loopback (e.g., request went through a local proxy like OAuth2 proxy).
    """
    if request.client is None:
        return False

    client_host = request.client.host
    if not (client_host == 'localhost' or _is_loopback_ip(client_host)):
        return False

    # Direct connection is from loopback. Now check if there are forwarding
    # headers that indicate the original client was NOT from loopback.
    forwarded_ip = _get_forwarded_client_ip(request)
    if forwarded_ip is None:
        # No forwarding headers - this is a direct loopback connection
        return True

    # There are forwarding headers. Check if the original client was also
    # from loopback. This handles cases like OAuth2 proxy running locally
    # that adds X-Forwarded-For headers even for local requests.
    if forwarded_ip == 'localhost' or _is_loopback_ip(forwarded_ip):
        return True

    # The forwarded IP indicates the original client was not from loopback,
    # so this request went through a proxy from an external client.
    logger.debug(f'Rejecting loopback bypass: direct client is {client_host} '
                 f'but forwarded client is {forwarded_ip}')
    return False
