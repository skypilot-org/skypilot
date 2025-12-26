"""Unit tests for sky.utils.command_runner."""

import os
import select
import socket
import tempfile
import threading
import time
from unittest import mock

import paramiko
import pytest

from sky.utils import auth_utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import interactive_utils


def test_docker_runner_passes_proxy_command_to_inner_hop() -> None:
    """Ensure docker-mode runners reuse user proxy for the host hop."""
    proxy_cmd = 'ssh -W %h:%p jump@host'
    user_hash = common_utils.get_user_hash()
    private_key_path, _, _ = auth_utils.get_ssh_key_and_lock_path(user_hash)

    runner = command_runner.SSHCommandRunner(
        node=('10.0.0.5', 22),
        ssh_user='ubuntu',
        ssh_private_key=os.path.expanduser(private_key_path),
        ssh_proxy_command=proxy_cmd,
        docker_user='container',
        ssh_control_name='unit-test-control',
    )

    # Proxy command should be consumed by the docker bridge, not the outer hop.
    assert runner._ssh_proxy_command is None  # type: ignore[attr-defined]

    # Inner hop must include the user proxy command before targeting the host VM.
    inner_cmd = runner._docker_ssh_proxy_command(
        ['ssh', '-T'])  # type: ignore[attr-defined]
    assert "ProxyCommand='ssh -W 10.0.0.5:22 jump@host'" in inner_cmd
    assert inner_cmd.endswith('ubuntu@10.0.0.5')

    outer_cmd = runner.ssh_base_command(
        ssh_mode=command_runner.SshMode.NON_INTERACTIVE,
        port_forward=None,
        connect_timeout=None,
    )
    assert outer_cmd[-1] == 'container@localhost'


class MockSSHServer(paramiko.ServerInterface):
    """Mock SSH server requiring keyboard-interactive auth."""

    def __init__(self, expected_code: str = '123456'):
        self.expected_code = expected_code
        self.auth_attempts = []

    def check_channel_request(self, kind, chanid):
        if kind == 'session':
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_none(self, username):
        return paramiko.AUTH_FAILED

    def check_auth_password(self, username, password):
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_FAILED

    def check_auth_interactive(self, username, submethods):
        return paramiko.InteractiveQuery(
            '',
            '',
            ('Verification code: ', False),
        )

    def check_auth_interactive_response(self, responses):
        self.auth_attempts.append(list(responses))
        if responses and responses[0] == self.expected_code:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return 'keyboard-interactive'

    def check_channel_exec_request(self, channel, command):
        # Echo back the command to show auth worked
        channel.send(f'executed: {command.decode()}\n'.encode())
        channel.send_exit_status(0)
        return True

    def check_channel_pty_request(self, channel, term, width, height,
                                  pixelwidth, pixelheight, modes):
        return True

    def check_channel_shell_request(self, channel):
        return True


def _generate_host_key():
    """Generate a temporary RSA host key."""
    return paramiko.RSAKey.generate(2048)


class TestSSHCommandRunnerInteractiveAuth:
    """Test SSHCommandRunner with mock SSH server requiring interactive auth."""

    @pytest.fixture
    def mock_ssh_server(self):
        """Start a mock SSH server and return (port, server_instance)."""
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('127.0.0.1', 0))
        server_socket.listen(1)
        port = server_socket.getsockname()[1]

        host_key = _generate_host_key()
        ssh_server = MockSSHServer(expected_code='123456')
        server_ready = threading.Event()
        server_done = threading.Event()

        def run_server():
            server_ready.set()
            try:
                server_socket.settimeout(30)
                conn, _ = server_socket.accept()
                transport = paramiko.Transport(conn)
                transport.add_server_key(host_key)
                transport.start_server(server=ssh_server)

                # Wait for channel (exec request)
                channel = transport.accept(timeout=30)
                if channel:
                    time.sleep(0.5)
                    channel.close()
                transport.close()
            except Exception as e:
                print(f'Server error: {e}')
            finally:
                server_done.set()

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        server_ready.wait(timeout=5)

        yield port, ssh_server, server_done

        server_socket.close()
        server_thread.join(timeout=2)

    def test_interactive_auth_via_pty_and_unix_socket(self, mock_ssh_server):
        """Test that SSHCommandRunner's interactive auth flow works.

        This tests the actual _retry_with_interactive_auth code path:
        1. SSH command is run with PTY for interactive auth
        2. PTY master fd is passed to handler via Unix socket
        3. Handler writes auth code to PTY
        4. SSH authenticates successfully
        """
        port, ssh_server, server_done = mock_ssh_server
        session_id = 'test-session-123'

        # Create SSHCommandRunner pointing to mock server
        runner = command_runner.SSHCommandRunner(
            node=('127.0.0.1', port),
            ssh_user='testuser',
            ssh_private_key=None,
            ssh_control_name=None,
        )

        # Thread to simulate websocket handler:
        # - Connect to Unix socket
        # - Receive PTY master fd
        # - Write auth code to PTY
        # - Send OK signal
        auth_handler_done = threading.Event()
        auth_error = []

        def simulate_websocket_handler():
            sock = None
            pty_master_fd = None
            try:
                fd_socket_path = interactive_utils.get_pty_socket_path(
                    session_id)

                # Wait for socket to be created
                for _ in range(50):
                    if os.path.exists(fd_socket_path):
                        break
                    time.sleep(0.1)
                else:
                    auth_error.append('Unix socket not created')
                    return

                # Connect to Unix socket and receive PTY master fd
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(fd_socket_path)
                pty_master_fd = interactive_utils.recv_fd(sock)

                # Wait for and read prompt from SSH
                # Use select to wait for data to be available
                prompt_data = b''
                deadline = time.time() + 5.0  # 5 second timeout

                while time.time() < deadline:
                    # Check if data is available to read
                    readable, _, _ = select.select([pty_master_fd], [], [], 0.1)
                    if readable:
                        try:
                            chunk = os.read(pty_master_fd, 1024)
                            if chunk:
                                prompt_data += chunk
                                # Check if we got the full prompt
                                if b'Verification code:' in prompt_data:
                                    break
                        except OSError:
                            break

                assert prompt_data == b'\r(testuser@127.0.0.1) Verification code: ', \
                    f'Expected exact prompt, got: {prompt_data!r}'

                # Write auth code
                os.write(pty_master_fd, b'123456\n')

                # Wait for auth to complete
                time.sleep(1.0)

            except Exception as e:
                auth_error.append(str(e))
            finally:
                # Cleanup - fd may already be closed if SSH exited
                try:
                    if sock is not None:
                        sock.close()
                except OSError:
                    pass
                try:
                    if pty_master_fd is not None:
                        os.close(pty_master_fd)
                except OSError:
                    pass
                auth_handler_done.set()

        handler_thread = threading.Thread(target=simulate_websocket_handler,
                                          daemon=True)
        handler_thread.start()

        # Build SSH command that would trigger interactive auth
        # We call _retry_with_interactive_auth directly
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log',
                                         delete=False) as f:
            log_path = f.name

        try:
            # Build SSH command using the actual runner's method
            ssh_command = runner.ssh_base_command(
                ssh_mode=command_runner.SshMode.INTERACTIVE,
                port_forward=None,
                connect_timeout=30,
            )

            # Add options to skip host key checking and force keyboard-interactive
            ssh_command = (ssh_command[:1] + [
                '-o',
                'StrictHostKeyChecking=no',
                '-o',
                'UserKnownHostsFile=/dev/null',
                '-o',
                'PreferredAuthentications=keyboard-interactive',
                '-o',
                'NumberOfPasswordPrompts=1',
            ] + ssh_command[1:] + ['echo', 'hello'])

            result = runner._retry_with_interactive_auth(  # pylint: disable=protected-access
                session_id=session_id,
                command=ssh_command,
                log_path=log_path,
                require_outputs=False,
                process_stream=False,
                stream_logs=False,
                executable='/bin/bash',
            )

            # Wait for everything to complete
            auth_handler_done.wait(timeout=10)
            server_done.wait(timeout=5)

            # Verify auth was attempted with correct code
            assert ssh_server.auth_attempts == [['123456']], \
                f'Expected auth with 123456, got {ssh_server.auth_attempts}'

            # Check no errors in auth handler
            assert not auth_error, f'Auth handler errors: {auth_error}'

            # Result is just the return code when require_outputs=False
            assert result == 0, f'SSH failed with code {result}'

        finally:
            handler_thread.join(timeout=2)
            if os.path.exists(log_path):
                os.unlink(log_path)


def test_kubernetes_runner_adds_container_flag_to_kubectl_exec() -> None:
    captured = {}

    def fake_run_with_log(command: str, *args, **kwargs):
        captured['command'] = command
        require_outputs = kwargs.get('require_outputs', False)
        if require_outputs:
            return 0, '', ''
        return 0

    with mock.patch.object(command_runner.log_lib,
                           'run_with_log',
                           side_effect=fake_run_with_log):
        runner = command_runner.KubernetesCommandRunner((('ns', 'ctx'), 'pod'),
                                                        container='ray-node')
        runner.run('echo hello', require_outputs=True, stream_logs=False)

    assert 'kubectl exec' in captured['command']
    assert 'pod/pod' in captured['command']
    assert '-c ray-node' in captured['command']


def test_kubernetes_runner_rsync_sets_exec_container_envvar() -> None:
    captured = {}

    def fake_run_with_log(command: str, *args, **kwargs):
        captured['command'] = command
        return 0, '', ''

    with mock.patch.object(command_runner.log_lib,
                           'run_with_log',
                           side_effect=fake_run_with_log):
        runner = command_runner.KubernetesCommandRunner((('ns', 'ctx'), 'pod'),
                                                        container='sidecar0')
        runner.rsync('/tmp/src', '/tmp/dst', up=True, stream_logs=False)

    assert 'SKYPILOT_K8S_EXEC_CONTAINER=sidecar0' in captured['command']
    assert '--rsync-path=rsync' in captured['command']
    assert 'rsync' in captured['command']


def test_kubernetes_runner_rsync_does_not_set_exec_container_envvar_by_default(
) -> None:
    captured = {}

    def fake_run_with_log(command: str, *args, **kwargs):
        captured['command'] = command
        return 0, '', ''

    with mock.patch.object(command_runner.log_lib,
                           'run_with_log',
                           side_effect=fake_run_with_log):
        runner = command_runner.KubernetesCommandRunner((('ns', 'ctx'), 'pod'))
        runner.rsync('/tmp/src', '/tmp/dst', up=True, stream_logs=False)

    assert 'SKYPILOT_K8S_EXEC_CONTAINER=' not in captured['command']
    assert '--rsync-path=rsync' in captured['command']


def test_get_pod_primary_container_prefers_ray_node() -> None:
    from sky.provision.kubernetes import utils as kubernetes_utils

    sidecar = mock.MagicMock()
    sidecar.name = 'sidecar'
    primary = mock.MagicMock()
    primary.name = 'ray-node'

    pod = mock.MagicMock()
    pod.metadata.name = 'p'
    pod.spec.containers = [sidecar, primary]

    assert kubernetes_utils.get_pod_primary_container(pod) is primary


def test_get_pod_primary_container_falls_back_to_first_container() -> None:
    from sky.provision.kubernetes import utils as kubernetes_utils

    c0 = mock.MagicMock()
    c0.name = 'not-ray-node'
    c1 = mock.MagicMock()
    c1.name = 'also-not-ray-node'

    pod = mock.MagicMock()
    pod.metadata.name = 'p'
    pod.spec.containers = [c0, c1]

    assert kubernetes_utils.get_pod_primary_container(pod) is c0


def test_get_pod_primary_container_raises_on_empty_container_list() -> None:
    from sky.provision.kubernetes import utils as kubernetes_utils

    pod = mock.MagicMock()
    pod.metadata.name = 'p'
    pod.spec.containers = []

    with pytest.raises(ValueError):
        kubernetes_utils.get_pod_primary_container(pod)
