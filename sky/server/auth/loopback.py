"""Shared loopback detection utilities for auth middlewares."""

import contextvars
import ipaddress

import fastapi

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Whether the HTTP request being dispatched came from loopback, recorded by
# `APIVersionMiddleware` and stamped onto the request body, because the worker
# that runs the request cannot see this ContextVar. Unset means not loopback.
_request_from_loopback: contextvars.ContextVar[bool] = (contextvars.ContextVar(
    'request_from_loopback', default=False))


def set_request_from_loopback(from_loopback: bool) -> None:
    _request_from_loopback.set(from_loopback)


def get_request_from_loopback() -> bool:
    return _request_from_loopback.get()


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
    """Determine if a request is coming from localhost."""
    if request.client is None:
        return False

    client_host = request.client.host
    if client_host == 'localhost' or _is_loopback_ip(client_host):
        # Additional checks: ensure no forwarding headers are present.
        # If there are any, assume this traffic went through a proxy.
        return not any(
            request.headers.get(header) for header in COMMON_PROXY_HEADERS)

    return False
