"""A persisted tunnel must never reuse or terminate an unrelated process."""

import dataclasses
import socket
import subprocess
import sys
from unittest import mock

import psutil
import pytest

from sky import global_user_state
from sky.backends import cloud_vm_ray_backend as backend
from sky.utils import subprocess_utils


@pytest.fixture
def listener():
    process = subprocess.Popen([
        sys.executable, '-u', '-c', 'import socket, time; s = socket.socket(); '
        's.bind(("127.0.0.1", 0)); s.listen(); '
        'print(s.getsockname()[1], flush=True); time.sleep(60)'
    ],
                               stdout=subprocess.PIPE,
                               text=True,
                               start_new_session=True)
    try:
        port = int(process.stdout.readline())
        tunnel = backend.SSHTunnelInfo(
            port, process.pid,
            psutil.Process(process.pid).create_time(), socket.gethostname())
        yield process, tunnel
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        process.stdout.close()


@pytest.fixture
def handle():
    resource_handle = object.__new__(backend.CloudVmRayResourceHandle)
    resource_handle.cluster_name = 'tunnel-identity-test'
    return resource_handle


@pytest.mark.parametrize('record', ['legacy', 'reused_pid', 'other_host'])
def test_unrelated_listener_is_neither_reused_nor_killed(
        listener, handle, record):
    process, tunnel = listener
    if record == 'legacy':
        tunnel = backend.SSHTunnelInfo(tunnel.port, tunnel.pid)
    elif record == 'reused_pid':
        tunnel = dataclasses.replace(tunnel, started_at=tunnel.started_at - 1)
    else:
        tunnel = dataclasses.replace(tunnel, hostname='another-api-server')

    with mock.patch.object(socket, 'socket') as connect:
        assert not backend._is_tunnel_healthy(tunnel)
        connect.assert_not_called()
    handle._terminate_ssh_tunnel_process(tunnel)
    assert process.poll() is None


def test_owned_listener_is_reused_and_terminated(listener, handle):
    process, tunnel = listener
    assert backend._is_tunnel_healthy(tunnel)
    with mock.patch.object(handle, '_get_skylet_ssh_tunnel', return_value=tunnel), \
         mock.patch.object(handle, '_open_and_update_skylet_tunnel') as open_tunnel, \
         mock.patch.object(backend.grpc, 'insecure_channel') as channel:
        assert handle.get_grpc_channel() is channel.return_value
        open_tunnel.assert_not_called()
    handle._terminate_ssh_tunnel_process(tunnel)
    assert process.poll() is not None
    assert not backend._is_tunnel_healthy(tunnel)


def test_metadata_preserves_identity_and_reads_legacy_records(listener, handle):
    _, tunnel = listener
    with mock.patch.object(global_user_state,
                           'set_cluster_skylet_ssh_tunnel_metadata') as save:
        handle._set_skylet_ssh_tunnel(tunnel)
    metadata = save.call_args.args[1]
    assert metadata == dataclasses.astuple(tunnel)
    with mock.patch.object(global_user_state,
                           'get_cluster_skylet_ssh_tunnel_metadata',
                           return_value=metadata):
        assert handle._get_skylet_ssh_tunnel() == tunnel
    with mock.patch.object(global_user_state,
                           'get_cluster_skylet_ssh_tunnel_metadata',
                           return_value=metadata[:2]):
        assert handle._get_skylet_ssh_tunnel().get_process() is None


def test_opened_tunnel_persists_process_identity(listener, handle):
    process, expected = listener
    with mock.patch.object(handle, 'get_command_runners', return_value=[mock.Mock()]), \
         mock.patch.object(backend.random, 'randint', return_value=expected.port), \
         mock.patch.object(backend.backend_utils, 'open_ssh_tunnel', return_value=process), \
         mock.patch.object(backend.grpc, 'channel_ready_future'), \
         mock.patch.object(handle, '_get_skylet_ssh_tunnel', return_value=None), \
         mock.patch.object(handle, '_set_skylet_ssh_tunnel') as save:
        assert handle._open_and_update_skylet_tunnel() == expected
        save.assert_called_once_with(expected)


@pytest.mark.parametrize('error', [psutil.NoSuchProcess, psutil.AccessDenied])
def test_unavailable_process_is_untrusted(listener, error):
    _, tunnel = listener
    with mock.patch.object(psutil, 'Process', side_effect=error(tunnel.pid)):
        assert tunnel.get_process() is None


def test_cleanup_preserves_verified_process_object(listener):
    _, tunnel = listener
    process = tunnel.get_process()
    with mock.patch.object(subprocess_utils, '_safe_children', return_value=[]), \
         mock.patch.object(subprocess_utils, 'kill_process_with_grace_period') as kill:
        subprocess_utils.kill_children_processes(process)
        assert kill.call_args.args[0] is process
