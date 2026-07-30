"""Unit tests for the SSH proxy websocket cluster validation.

These verify that an already-accepted SSH proxy websocket is closed
gracefully (rather than raising an HTTPException, which would surface as an
unhandled RuntimeError traceback) when cluster validation fails.
"""

from unittest import mock

import fastapi
import pytest

from sky import clouds
from sky.server import server


def _make_websocket():
    websocket = mock.MagicMock(spec=fastapi.WebSocket)
    websocket.close = mock.AsyncMock()
    return websocket


@pytest.mark.asyncio
async def test_validate_cluster_for_ssh_proxy_ws_returns_handle():
    """On success the handle is returned and the websocket is left open."""
    websocket = _make_websocket()
    handle = mock.MagicMock()
    with mock.patch.object(server,
                           '_get_cluster_and_validate',
                           new=mock.AsyncMock(return_value=handle)):
        result = await server._validate_cluster_for_ssh_proxy_ws(
            websocket, 'my-cluster', clouds.Kubernetes)

    assert result is handle
    websocket.close.assert_not_called()


@pytest.mark.asyncio
async def test_validate_cluster_for_ssh_proxy_ws_closes_on_not_found():
    """A 404 must close the websocket with 1008, not raise."""
    websocket = _make_websocket()
    exc = fastapi.HTTPException(status_code=404,
                                detail='Cluster ghost not found')
    with mock.patch.object(server,
                           '_get_cluster_and_validate',
                           new=mock.AsyncMock(side_effect=exc)):
        result = await server._validate_cluster_for_ssh_proxy_ws(
            websocket, 'ghost', clouds.Kubernetes)

    assert result is None
    websocket.close.assert_awaited_once_with(code=1008,
                                             reason='Cluster ghost not found')


@pytest.mark.asyncio
async def test_validate_cluster_for_ssh_proxy_ws_closes_on_wrong_state():
    """A 400 (e.g. cluster not running) is handled the same way."""
    websocket = _make_websocket()
    exc = fastapi.HTTPException(status_code=400,
                                detail='Cluster my-cluster is not running')
    with mock.patch.object(server,
                           '_get_cluster_and_validate',
                           new=mock.AsyncMock(side_effect=exc)):
        result = await server._validate_cluster_for_ssh_proxy_ws(
            websocket, 'my-cluster', clouds.Slurm)

    assert result is None
    websocket.close.assert_awaited_once_with(
        code=1008, reason='Cluster my-cluster is not running')


@pytest.mark.asyncio
async def test_validate_cluster_for_ssh_proxy_ws_truncates_long_reason():
    """A reason over 123 bytes must be truncated (RFC 6455 close frame limit).

    Otherwise the close frame serialization itself raises, re-introducing the
    unhandled RuntimeError this helper exists to avoid.
    """
    websocket = _make_websocket()
    exc = fastapi.HTTPException(status_code=400, detail='x' * 200)
    with mock.patch.object(server,
                           '_get_cluster_and_validate',
                           new=mock.AsyncMock(side_effect=exc)):
        result = await server._validate_cluster_for_ssh_proxy_ws(
            websocket, 'my-cluster', clouds.Slurm)

    assert result is None
    websocket.close.assert_awaited_once_with(code=1008, reason='x' * 123)
