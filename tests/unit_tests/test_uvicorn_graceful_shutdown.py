"""Tests for graceful shutdown of the API server uvicorn wrapper.

The server worker sets ``block_requests`` early during graceful shutdown so
that new requests are rejected while in-flight requests drain. If the drain
step then fails to complete (e.g. the request backend / database is
unreachable), the worker must still exit so it can be restarted and clear the
block -- otherwise it stays up rejecting every request forever.
"""
import signal
from unittest import mock

import filelock
import uvicorn

from sky.server import uvicorn as sky_uvicorn


async def _dummy_app(scope, receive, send):  # pylint: disable=unused-argument
    pass


def _make_server() -> sky_uvicorn.Server:
    return sky_uvicorn.Server(uvicorn.Config(app=_dummy_app))


def _patch_fast(monkeypatch) -> None:
    # Skip the real sleeps and the block-requests side effect.
    monkeypatch.setattr(sky_uvicorn.time, 'sleep', lambda *_a, **_k: None)
    monkeypatch.setattr(sky_uvicorn.state, 'set_block_requests',
                        lambda *_a, **_k: None)


def test_graceful_shutdown_exits_when_backend_unreachable(monkeypatch):
    """A drain failure must not prevent the worker from exiting."""
    server = _make_server()
    _patch_fast(monkeypatch)

    backend = mock.Mock()
    backend.get_shutdown_active_requests.side_effect = RuntimeError('db down')
    monkeypatch.setattr(sky_uvicorn.request_storage, 'get_request_backend',
                        lambda: backend)

    server._graceful_shutdown(signal.SIGTERM, None)  # pylint: disable=protected-access

    assert server.should_exit is True


def test_graceful_shutdown_exits_when_no_requests(monkeypatch):
    """The happy path still exits cleanly."""
    server = _make_server()
    _patch_fast(monkeypatch)

    backend = mock.Mock()
    backend.get_shutdown_active_requests.return_value = []
    monkeypatch.setattr(sky_uvicorn.request_storage, 'get_request_backend',
                        lambda: backend)

    server._graceful_shutdown(signal.SIGTERM, None)  # pylint: disable=protected-access

    assert server.should_exit is True


def test_graceful_shutdown_exits_when_coordinator_lock_times_out(monkeypatch):
    """A worker that cannot acquire the coordinator lock still exits."""
    server = _make_server()
    _patch_fast(monkeypatch)

    class _StuckLock:

        def __init__(self, *_a, **_k):
            pass

        def acquire(self, *_a, **_k):
            raise filelock.Timeout('lock held')

    monkeypatch.setattr(sky_uvicorn.filelock, 'FileLock', _StuckLock)

    server._graceful_shutdown(signal.SIGTERM, None)  # pylint: disable=protected-access

    assert server.should_exit is True
