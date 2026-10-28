"""Unit tests for the SSH proxy websocket cluster validation.

These verify that an already-accepted SSH proxy websocket is closed
gracefully (rather than raising an HTTPException, which would surface as an
unhandled RuntimeError traceback) when cluster validation fails.
"""

import ast
import asyncio
import contextlib
import gc
import inspect
import logging
import os
import shlex
import subprocess
import sys
import textwrap
from unittest import mock

import fastapi
import pytest

from sky import clouds
from sky.metrics import utils as metrics_utils
from sky.server import server


def _make_websocket():
    websocket = mock.MagicMock(spec=fastapi.WebSocket)
    websocket.close = mock.AsyncMock()
    return websocket


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
    assert argv[:5] == ['runuser', '-u', 'alice', '--', 'srun']
    assert 'AuthorizedKeysFile=~alice/.ssh/authorized_keys' in argv


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
    assert argv[:7] == [
        'sudo', '--non-interactive', '-H', '-u', 'alice', '--', 'srun'
    ]
    assert '/bin/bash' not in argv


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
    assert argv[:5] == ['runuser', '-u', 'alice', '--', 'srun']
    assert '--container-remap-root' in argv
    assert argv[-3:-1] == ['/bin/bash', '-c']


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


# ---------------------------------------------------------------------------
# kubernetes_pod_ssh_proxy: kubectl stdout pipe ownership and child reaping.
#
# These drive the real handler with a fake `kubectl` (a small Python script)
# and a stubbed websocket proxy, on asyncio and on uvloop when installed. They
# guard the fd lifecycle: the stdout pipe fd has exactly one owner
# (`proc.stdout`), is closed exactly once, is unregistered from the loop before
# that close, and the kubectl child is always reaped -- including when kubectl
# exits before the port-forward is up.
# ---------------------------------------------------------------------------

_FAKE_KUBECTL = """\
import os
import signal
import socket
import sys
import time

mode = sys.argv[-1]
if mode == 'exit':
    print('Error from server (NotFound): pods "ghost" not found', flush=True)
    sys.exit(1)

srv = socket.socket()
srv.bind(('127.0.0.1', 0))
srv.listen(8)
port = srv.getsockname()[1]
print(f'Forwarding from 127.0.0.1:{port} -> 22', flush=True)
print(f'Forwarding from [::1]:{port} -> 22', flush=True)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
conn, _ = srv.accept()
print(f'Handling connection for {port}', flush=True)
if mode == 'die':
    conn.close()
    print('E0908 lost connection to pod', flush=True)
    sys.exit(1)
while True:
    data = conn.recv(1024)
    if not data:
        sys.exit(0)
    conn.sendall(data)
"""


def _loop_factories():
    factories = [asyncio.new_event_loop]
    try:
        import uvloop  # pylint: disable=import-outside-toplevel
        factories.append(uvloop.new_event_loop)
    except ImportError:
        pass
    return factories


@pytest.fixture(params=_loop_factories(),
                ids=lambda f: f.__module__.split('.')[0])
def loop(request):
    the_loop = request.param()
    asyncio.set_event_loop(the_loop)
    try:
        yield the_loop
    finally:
        the_loop.run_until_complete(asyncio.sleep(0))
        the_loop.close()
        asyncio.set_event_loop(None)


@contextlib.contextmanager
def _capture_sky_logs(caplog, name: str, level: int):
    """Capture records emitted by the `sky` logger `name` in `caplog`.

    pytest installs the caplog handler on the root logger, but `sky_logging`
    sets `propagate = False` on the `sky` logger and the API server's logging
    setup (`sky.server.uvicorn.add_timestamp_prefix_for_server_logs`, run
    when another test starts the app in-process in the same worker) does the
    same for `sky.server`. Records from `sky.server.*` never reach the root
    logger, so `caplog.records` stays empty. pytest >= 9.1 also attaches its
    handler to non-propagating loggers (pytest-dev/pytest#3697); the 8.x that
    Python 3.9 still resolves to does not. Attach the handler to the emitting
    logger itself so every pytest version and worker history behaves the
    same. Enter this from the test body: pytest swaps `caplog.handler`
    between the setup and call phases, so a fixture would attach the wrong
    one.
    """
    emitter = logging.getLogger(name)
    with caplog.at_level(level, logger=name):
        emitter.addHandler(caplog.handler)
        try:
            yield caplog
        finally:
            emitter.removeHandler(caplog.handler)


@pytest.fixture
def fake_kubectl(tmp_path):
    path = tmp_path / 'kubectl'
    path.write_text(f'#!{sys.executable}\n' + _FAKE_KUBECTL)
    path.chmod(0o755)
    return str(path)


def _fd_is_open(fd: int) -> bool:
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


def _closed_total(reason: str) -> float:
    return metrics_utils.SKY_APISERVER_WEBSOCKET_CLOSED_TOTAL.labels(
        pid=os.getpid(), reason=reason)._value.get()  # pylint: disable=protected-access


class _Captured:
    """What the patched Popen and proxy stub saw."""

    def __init__(self):
        self.procs = []
        self.echoed = None


def _run_handler(loop, fake_kubectl, mode, proxy_stub):
    captured = _Captured()
    real_popen = subprocess.Popen

    def capturing_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        captured.procs.append(proc)
        return proc

    handle = mock.MagicMock()
    handle.head_ssh_port = 22
    # argv[0] is the fake kubectl itself: `spawn_without_fork` resolves argv[0]
    # and an absolute path is taken as given, so nothing inside the server
    # needs to be patched to redirect the spawn.
    handle.get_command_runners.return_value[0].port_forward_command.\
        return_value = [fake_kubectl, 'port-forward', 'pod/x', ':22', mode]
    websocket = _make_websocket()
    websocket.accept = mock.AsyncMock()

    async def stub(websocket,
                   read_from_backend,
                   write_to_backend,
                   close_backend,
                   timestamps_supported,
                   path=None):
        del websocket, timestamps_supported, path
        return await proxy_stub(captured, read_from_backend, write_to_backend,
                                close_backend)

    with mock.patch.object(server, '_validate_cluster_for_ssh_proxy_ws',
                              new=mock.AsyncMock(return_value=handle)), \
            mock.patch.object(server.websocket_utils, 'run_websocket_proxy',
                              new=stub), \
            mock.patch.object(subprocess, 'Popen', capturing_popen):
        loop.run_until_complete(
            server.kubernetes_pod_ssh_proxy(websocket, 'my-cluster'))
    assert len(captured.procs) == 1
    return websocket, captured


async def _echo_once(captured, read_from_backend, write_to_backend,
                     close_backend):
    await write_to_backend(b'ping')
    captured.echoed = await read_from_backend()
    await close_backend()
    return False


async def _wait_for_kubectl_death(captured, read_from_backend, write_to_backend,
                                  close_backend):
    del write_to_backend
    captured.echoed = await read_from_backend()  # b'' once kubectl closes
    proc = captured.procs[0]
    for _ in range(500):
        if proc.poll() is not None:
            break
        await asyncio.sleep(0.01)
    await close_backend()
    return True


def _assert_pipe_fd_released(loop, proc):
    """The stdout fd is closed once, unwatched, and safe to reuse."""
    assert proc.stdout.closed
    fd = proc.stdout.raw.fileno() if not proc.stdout.raw.closed else None
    assert fd is None
    # Not registered with the loop any more (must not raise on either loop).
    # We do not know the number any more; instead reuse the lowest free
    # numbers and check nothing closes them behind our back.
    canary_r, canary_w = os.pipe()

    async def spin():
        for _ in range(50):
            await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0.02)

    loop.run_until_complete(spin())
    os.write(canary_w, b'x')
    assert os.read(canary_r, 1) == b'x'
    os.close(canary_r)
    os.close(canary_w)


def test_ssh_proxy_forwarding_session_releases_pipe_once(loop, fake_kubectl):
    before = _closed_total('ClientClosed')
    websocket, captured = _run_handler(loop, fake_kubectl, 'forward',
                                       _echo_once)
    proc = captured.procs[0]
    assert captured.echoed == b'ping'
    assert proc.returncode is not None  # reaped
    websocket.close.assert_not_awaited()
    assert _closed_total('ClientClosed') == before + 1
    _assert_pipe_fd_released(loop, proc)


def test_ssh_proxy_kubectl_exit_before_forwarding_reaps_child(
        loop, fake_kubectl):
    before = _closed_total('KubectlPortForwardExit')
    websocket, captured = _run_handler(loop, fake_kubectl, 'exit', _echo_once)
    proc = captured.procs[0]
    websocket.close.assert_awaited_once()
    # Previously left as a zombie until the next Popen(); now reaped.
    assert proc.returncode == 1
    assert _closed_total('KubectlPortForwardExit') == before + 1
    _assert_pipe_fd_released(loop, proc)


def test_ssh_proxy_kubectl_death_mid_session_logs_leftover(
        loop, fake_kubectl, caplog):
    before = _closed_total('KubectlPortForwardExit')
    with _capture_sky_logs(caplog, 'sky.server.server', logging.ERROR):
        _, captured = _run_handler(loop, fake_kubectl, 'die',
                                   _wait_for_kubectl_death)
    proc = captured.procs[0]
    assert captured.echoed == b''
    # Reaped by poll(), not terminated (the fake exits 0 on SIGTERM).
    assert proc.returncode == 1
    assert _closed_total('KubectlPortForwardExit') == before + 1
    # The kubectl-died-first branch logged what kubectl printed after the
    # port-forward came up, including its last line before exiting.
    exit_messages = [
        rec.getMessage()
        for rec in caplog.records
        if 'kubectl port-forward exited before' in rec.getMessage()
    ]
    assert exit_messages, caplog.text
    assert all(
        'lost connection to pod' in msg for msg in exit_messages), exit_messages
    _assert_pipe_fd_released(loop, proc)


def test_slurm_ssh_proxy_teardown_does_not_await():
    """`slurm_job_ssh_proxy` must not await inside its `finally`.

    That block is the only code that takes the loop's watchers off the srun
    pipes before `subprocess.Popen` closes them, and the only code that
    dispatches the reap. A cancellation delivered into an await there skips
    everything after it, leaving a watcher registered on an fd number the
    kernel is then free to hand to another connection.
    """
    tree = ast.parse(
        textwrap.dedent(inspect.getsource(server.slurm_job_ssh_proxy)))
    func = tree.body[0]
    assert isinstance(func, ast.AsyncFunctionDef)

    finalbodies = [
        node.finalbody
        for node in ast.walk(func)
        if isinstance(node, ast.Try) and node.finalbody
    ]
    assert finalbodies, 'expected a try/finally in slurm_job_ssh_proxy'

    for finalbody in finalbodies:
        for stmt in finalbody:
            awaits = [
                node for node in ast.walk(stmt) if isinstance(node, ast.Await)
            ]
            assert not awaits, (
                'found `await` at line(s) '
                f'{sorted(node.lineno for node in awaits)} of the function, '
                'inside the `finally` of slurm_job_ssh_proxy')
