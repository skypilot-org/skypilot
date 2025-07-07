"""State for API server process."""

import os
from typing import Optional

from sky.skylet import constants

# This state is used to block requests except /api operations, which is useful
# when a server is shutting down: new requests will be blocked, but existing
# requests will be allowed to finish and be operated via /api operations, e.g.
# /api/logs, /api/cancel, etc.
_block_requests = False

_server_port: Optional[int] = None
_server_host: Optional[str] = None


# TODO(aylei): refactor, state should be a instance property of API server app
# instead of a global variable.
def get_block_requests() -> bool:
    """Whether block requests except /api operations."""
    return _block_requests


def set_block_requests(shutting_down: bool) -> None:
    """Set the API server to block requests except /api operations."""
    global _block_requests
    _block_requests = shutting_down

def set_server_port(port: int) -> None:
    """Set the port of the API server."""
    global _server_port
    _server_port = port

def get_server_port() -> Optional[int]:
    """Get the port of the API server."""
    return _server_port

def set_server_host(host: str) -> None:
    """Set the host of the API server."""
    global _server_host
    _server_host = host

def get_server_host() -> Optional[str]:
    """Get the host of the API server."""
    return _server_host

def get_host_address() -> str:
    """Get the advertise host of the API server."""
    # Use environment variable since the advertise host should typically be
    # managed by ochestration system (e.g. kuberentes with our helm chart).
    host = get_server_host()
    if host is None:
        raise RuntimeError('Server host is not set in server process')
    port = get_server_port()
    if port is None:
        raise RuntimeError('Server port is not set in server process')
    return f'{host}:{port}'
