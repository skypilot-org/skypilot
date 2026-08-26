"""Unit tests for the SSH proxy websocket cluster validation.

These verify that an already-accepted SSH proxy websocket is closed
gracefully (rather than raising an HTTPException, which would surface as an
unhandled RuntimeError traceback) when cluster validation fails.
"""

import shlex
from unittest import mock

import fastapi
import pytest

from sky import clouds
from sky.server import server
from sky.utils import status_lib


def _make_websocket():
    websocket = mock.MagicMock(spec=fastapi.WebSocket)
    websocket.close = mock.AsyncMock()
    return websocket


# `wrap_command_as_user` prefixes the payload with this: `sudo -u` stays in the
# caller's cwd, unlike `su --login`, so the target's home is restored by hand.
_CD_HOME = 'cd -- "$HOME" || exit 1; '


def test_slurm_ssh_proxy_runs_as_submit_user():
    command = server._build_slurm_job_ssh_command(
        provider_config={
            'ssh': {
                'user': 'root'
            },
            'slurm_user': 'alice',
        },
        job_id='123',
        target_node='node-1',
        cluster_name_on_cloud='test-cluster',
        is_container_image=False)

    argv = shlex.split(command)
    assert argv[:5] == ['su', '--login', '--shell', '/bin/bash', '--command']
    assert argv[6:] == ['--', 'alice']
    assert argv[5].startswith(_CD_HOME + 'srun ')
    assert '~alice/.ssh/authorized_keys' in argv[5]


def test_slurm_ssh_proxy_uses_sudo_for_non_root_transport():
    command = server._build_slurm_job_ssh_command(
        provider_config={
            'ssh': {
                'user': 'ubuntu'
            },
            'slurm_user': 'alice',
        },
        job_id='123',
        target_node='node-1',
        cluster_name_on_cloud='test-cluster',
        is_container_image=False)

    argv = shlex.split(command)
    # `-u alice` is load-bearing: the privilege drop happens in sudo itself,
    # so the sudoers grant can be scoped to the submit users. Without it sudo
    # would run the inner shell as root.
    assert argv[:9] == [
        'sudo', '--non-interactive', '-H', '-u', 'alice', '--', '/bin/bash',
        '--login', '-c'
    ]
    # The old form ran `su` as root and dropped privileges there.
    assert 'su' not in argv
    assert argv[9].startswith(_CD_HOME + 'srun ')
    assert len(argv) == 10


def test_slurm_container_ssh_proxy_uses_root_in_container():
    command = server._build_slurm_job_ssh_command(
        provider_config={
            'ssh': {
                'user': 'root'
            },
            'slurm_user': 'alice',
        },
        job_id='123',
        target_node='node-1',
        cluster_name_on_cloud='test-cluster',
        is_container_image=True)

    argv = shlex.split(command)
    assert argv[:5] == ['su', '--login', '--shell', '/bin/bash', '--command']
    assert argv[6:] == ['--', 'alice']
    assert '--container-remap-root' in argv[5]


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


# _get_cluster_and_validate: stale-workspace-config miss path ---------------


def _up_k8s_record():
    handle = mock.MagicMock()
    handle.launched_resources.cloud = clouds.Kubernetes()
    return {'status': status_lib.ClusterStatus.UP, 'handle': handle}


@pytest.mark.asyncio
async def test_get_cluster_and_validate_known_cluster_does_not_refresh():
    """Hot path: a cluster this process already sees costs no config reload."""
    record = _up_k8s_record()
    with mock.patch.object(server.core, 'status', return_value=[record]) as \
            status, mock.patch.object(
                server.common,
                'refresh_workspace_state_for_sync_handler') as refresh:
        handle = await server._get_cluster_and_validate('my-cluster',
                                                        clouds.Kubernetes)

    assert handle is record['handle']
    assert status.call_count == 1
    refresh.assert_not_called()


@pytest.mark.asyncio
async def test_get_cluster_and_validate_refreshes_and_retries_on_miss():
    """A miss caused by a stale process-cached workspace config must not
    surface as 404: a cluster in a workspace created via `/workspaces/create`
    after the server booted is filtered out of `core.status()` until this
    process reloads its config. The reload happens on the miss path only."""
    record = _up_k8s_record()
    with mock.patch.object(server.core, 'status',
                           side_effect=[[], [record]]) as status, \
            mock.patch.object(
                server.common,
                'refresh_workspace_state_for_sync_handler') as refresh:
        handle = await server._get_cluster_and_validate('new-ws-cluster',
                                                        clouds.Kubernetes)

    assert handle is record['handle']
    assert status.call_count == 2
    refresh.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_cluster_and_validate_genuine_miss_still_404s():
    """A cluster that is missing even after a refresh is a real 404."""
    with mock.patch.object(server.core, 'status',
                           side_effect=[[], []]) as status, \
            mock.patch.object(
                server.common,
                'refresh_workspace_state_for_sync_handler') as refresh:
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server._get_cluster_and_validate('ghost', clouds.Kubernetes)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == 'Cluster ghost not found'
    assert status.call_count == 2
    refresh.assert_called_once_with()
