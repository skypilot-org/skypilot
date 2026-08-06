# pylint: disable=protected-access
"""Unit tests for per-worker SO_REUSEPORT binding in sky.server.uvicorn.

These verify the opt-in gating of `_reuse_port_enabled` (env var values,
uds/fd listeners, platform support) and the socket-level behavior of
`_bind_reuse_port_socket` (SO_REUSEPORT is set, multiple binds on the same
address succeed, bind failure exits instead of raising).
"""

import socket

import pytest
import uvicorn as uvicorn_lib

from sky.server import uvicorn
from sky.skylet import constants

_HAS_SO_REUSEPORT = hasattr(socket, 'SO_REUSEPORT')


def _config(**kwargs) -> uvicorn_lib.Config:
    kwargs.setdefault('host', '127.0.0.1')
    kwargs.setdefault('port', 0)
    return uvicorn_lib.Config('dummy:app', **kwargs)


@pytest.mark.skipif(not _HAS_SO_REUSEPORT, reason='Platform lacks SO_REUSEPORT')
def test_reuse_port_disabled_by_default(monkeypatch):
    """Without the env var set, the feature is off (opt-in)."""
    monkeypatch.delenv(constants.ENV_VAR_SERVER_REUSE_PORT, raising=False)
    assert not uvicorn._reuse_port_enabled(_config())


@pytest.mark.skipif(not _HAS_SO_REUSEPORT, reason='Platform lacks SO_REUSEPORT')
@pytest.mark.parametrize('value', ['1', 'true', 'yes', 'TRUE', 'Yes'])
def test_reuse_port_enabled_values(monkeypatch, value):
    monkeypatch.setenv(constants.ENV_VAR_SERVER_REUSE_PORT, value)
    assert uvicorn._reuse_port_enabled(_config())


@pytest.mark.parametrize('value', ['0', 'false', 'no', '', 'enable'])
def test_reuse_port_disabled_values(monkeypatch, value):
    monkeypatch.setenv(constants.ENV_VAR_SERVER_REUSE_PORT, value)
    assert not uvicorn._reuse_port_enabled(_config())


def test_reuse_port_disabled_for_uds_and_fd(monkeypatch):
    """SO_REUSEPORT only applies to TCP host/port listeners."""
    monkeypatch.setenv(constants.ENV_VAR_SERVER_REUSE_PORT, '1')
    assert not uvicorn._reuse_port_enabled(_config(uds='/tmp/skypilot.sock'))
    assert not uvicorn._reuse_port_enabled(_config(fd=3))


def test_reuse_port_disabled_without_platform_support(monkeypatch):
    """On platforms without SO_REUSEPORT the flag is a no-op."""
    monkeypatch.setenv(constants.ENV_VAR_SERVER_REUSE_PORT, '1')
    monkeypatch.delattr(socket, 'SO_REUSEPORT', raising=False)
    assert not uvicorn._reuse_port_enabled(_config())


@pytest.mark.skipif(not _HAS_SO_REUSEPORT, reason='Platform lacks SO_REUSEPORT')
def test_bind_reuse_port_socket_allows_multiple_binds():
    """Two workers can bind the same address, and SO_REUSEPORT is set."""
    sock1 = uvicorn._bind_reuse_port_socket(_config())
    try:
        port = sock1.getsockname()[1]
        # A second bind on the same (host, port) must succeed, which is
        # exactly what restarted/parallel workers do.
        sock2 = uvicorn._bind_reuse_port_socket(_config(port=port))
        try:
            for sock in (sock1, sock2):
                assert sock.getsockopt(socket.SOL_SOCKET,
                                       socket.SO_REUSEPORT) != 0
                assert sock.getsockname()[1] == port
                assert sock.get_inheritable()
        finally:
            sock2.close()
    finally:
        sock1.close()


@pytest.mark.skipif(not _HAS_SO_REUSEPORT, reason='Platform lacks SO_REUSEPORT')
def test_bind_reuse_port_socket_exits_on_bind_failure():
    """Bind failure (port held by a non-reuseport socket) exits the process.

    This is the fail-fast path the parent relies on at startup; in a worker
    it matches ``uvicorn.Config.bind_socket`` behavior (exit and let the
    supervisor handle it) instead of leaking a traceback.
    """
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        blocker.bind(('127.0.0.1', 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        with pytest.raises(SystemExit):
            uvicorn._bind_reuse_port_socket(_config(port=port))
    finally:
        blocker.close()
