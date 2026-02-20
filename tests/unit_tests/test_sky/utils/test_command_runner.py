"""Unit tests for sky.utils.command_runner."""

from contextlib import suppress
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

    def __init__(
        self,
        exec_event: threading.Event,
        expected_code: str = '123456',
    ):
        self.expected_code = expected_code
        self.auth_attempts = []
        self.exec_event = exec_event

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
        cmd_str = command.decode('utf-8', errors='replace')
        channel.send(f'executed: {cmd_str}\n'.encode())
        # Allow the command and signal that exec was received
        self.exec_event.set()
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
    """Test SSHCommandRunner with mock SSH server requiring interactive auth.

    This test verifies the `_retry_with_interactive_auth` workflow where:
    1. The client (runner) connects to a mock SSH server requiring keyboard-interactive auth.
    2. The runner spawns a PTY and passes the master fd to a handler via a Unix socket.
    3. The handler (simulated here) reads the auth prompt from the PTY and writes the response.
    4. The mock server authenticates the client, accepts the command execution, and returns exit status 0.

    Crucially, this test handles synchronization to prevent deadlocks:
    - The server waits for the 'exec' request event before sending an exit status.
    - The server immediately closes the channel after sending status 0 to ensure the OpenSSH client receives EOF and terminates.
    - The test waits for the client to finish successfully before signaling the server to shut down.
    """

    @pytest.fixture(autouse=True)
    def mock_ssh_server(self):
        """Start a mock SSH server and set attributes on the test class."""
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('127.0.0.1', 0))
        server_socket.listen(1)
        self.port = server_socket.getsockname()[1]

        host_key = _generate_host_key()
        self.server_done = threading.Event()
        self.ssh_session_finished = threading.Event()
        self.exec_event = threading.Event()
        self.ssh_server = MockSSHServer(exec_event=self.exec_event,
                                        expected_code='123456')

        # Ensure the server is ready before yielding
        server_ready = threading.Event()

        def run_server():
            server_ready.set()
            conn, _ = server_socket.accept()
            transport = paramiko.Transport(conn)
            transport.add_server_key(host_key)
            transport.start_server(server=self.ssh_server)

            # Wait for channel (session open)
            channel = transport.accept(timeout=30)
            if channel:
                try:
                    # 1. Wait for the client to send the 'exec' request.
                    # We wait on the event we added to MockSSHServer.
                    if self.exec_event.wait(timeout=10):
                        # 2. Command received. Send exit status 0 to Client.
                        # This unblocks runner.run() so it returns 0.
                        # Echo back the command to show auth worked
                        channel.send_exit_status(0)
                        # 3. CRITICAL FIX: Close the channel (send EOF) immediately.
                        # OpenSSH client waits for EOF before exiting.
                        channel.close()
                    else:
                        print("Timeout waiting for exec request")

                    # 3. Now wait for the test to signal it's done (cleanup phase)
                    self.ssh_session_finished.wait(timeout=15)
                except Exception as e:
                    print(f'Server logic error: {e}')
                finally:

                    # Ensure channel is closed with suppressing exceptions
                    with suppress(Exception):
                        channel.close()

                    transport.close()

            self.server_done.set()

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Wait for the server to be ready before yielding
        server_ready.wait(timeout=5)

        yield

        server_socket.close()
        server_thread.join(timeout=2)

    def test_interactive_auth_via_pty_and_unix_socket(self):
        """Test that SSHCommandRunner's interactive auth flow works.
        This tests the actual _retry_with_interactive_auth code path:
        1. SSH command is run with PTY for interactive auth
        2. PTY master fd is passed to handler via Unix socket
        3. Handler writes auth code to PTY
        4. SSH authenticates successfully
        """
        session_id = 'test-session-123'

        # Create SSHCommandRunner pointing to mock server
        runner = command_runner.SSHCommandRunner(
            node=('127.0.0.1', self.port),
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

                # Allow a brief moment for propagation
                time.sleep(0.5)

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

            # Signal to the mock server that the SSH session is complete
            self.ssh_session_finished.set()

            # Wait for everything to complete
            auth_handler_done.wait(timeout=10)
            self.server_done.wait(timeout=5)

            # Verify auth was attempted with correct code
            assert self.ssh_server.auth_attempts == [['123456']], \
                f'Expected auth with 123456, got {self.ssh_server.auth_attempts}'

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


class TestSSHCommandRunnerAuthFailureDetection:
    """Test SSHCommandRunner authentication failure detection logic."""

    @pytest.fixture
    def runner_with_interactive_auth(self):
        """Create an SSHCommandRunner with interactive auth enabled."""
        user_hash = common_utils.get_user_hash()
        private_key_path, _, _ = auth_utils.get_ssh_key_and_lock_path(user_hash)
        return command_runner.SSHCommandRunner(
            node=('127.0.0.1', 22),
            ssh_user='testuser',
            ssh_private_key=os.path.expanduser(private_key_path),
            ssh_control_name='test-control',
            enable_interactive_auth=True,
        )

    @pytest.mark.parametrize(
        'returncode,ssh_log_content,expect_retry',
        [
            # Auth failures that should trigger retry (rc=255 + auth pattern):
            (255, 'Permission denied (keyboard-interactive).', True),
            (255, 'Authentication failed.', True),
            # Cases that should NOT trigger retry:
            # - rc=255 but no auth pattern in log
            (255, 'Connection timed out', False),
            # - Successful command (rc=0)
            (0, '', False),
            # - Non-255 error code
            (1, 'Permission denied', False),
        ])
    def test_interactive_auth_retry_detection(self,
                                              runner_with_interactive_auth,
                                              returncode, ssh_log_content,
                                              expect_retry):
        """Test that auth failure detection correctly triggers or skips retry."""
        fd, tmp_path = tempfile.mkstemp()

        def write_log_and_return(*_args, **_kwargs):
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(ssh_log_content)
            return (returncode, '', '')

        try:
            with mock.patch('tempfile.mkstemp', return_value=(fd, tmp_path)), \
                 mock.patch.object(command_runner.log_lib, 'run_with_log',
                                   side_effect=write_log_and_return), \
                 mock.patch.object(runner_with_interactive_auth,
                                   '_retry_with_interactive_auth',
                                   return_value=(0, 'success', '')) as mock_retry:
                runner_with_interactive_auth.run('echo hello',
                                                 require_outputs=True,
                                                 separate_stderr=True)
                if expect_retry:
                    mock_retry.assert_called_once()
                else:
                    mock_retry.assert_not_called()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_interactive_auth_retry_on_log_read_failure(
            self, runner_with_interactive_auth):
        """Test retry fallback when SSH log file cannot be read."""
        fd, tmp_path = tempfile.mkstemp()
        real_open = open

        def mock_open_raise_on_log(path, *args, **kwargs):
            if path == tmp_path and args and 'r' in args[0]:
                raise IOError('mock read error')
            return real_open(path, *args, **kwargs)

        try:
            with mock.patch('tempfile.mkstemp', return_value=(fd, tmp_path)), \
                 mock.patch.object(command_runner.log_lib, 'run_with_log',
                                   return_value=(255, '', '')), \
                 mock.patch('builtins.open', side_effect=mock_open_raise_on_log), \
                 mock.patch.object(runner_with_interactive_auth,
                                   '_retry_with_interactive_auth',
                                   return_value=(0, 'success', '')) as mock_retry:
                runner_with_interactive_auth.run('echo hello',
                                                 require_outputs=True,
                                                 separate_stderr=True)
                mock_retry.assert_called_once()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_interactive_auth_disabled_does_not_retry(self):
        """When enable_interactive_auth=False, no retry even on auth failure."""
        user_hash = common_utils.get_user_hash()
        private_key_path, _, _ = auth_utils.get_ssh_key_and_lock_path(user_hash)
        runner = command_runner.SSHCommandRunner(
            node=('127.0.0.1', 22),
            ssh_user='testuser',
            ssh_private_key=os.path.expanduser(private_key_path),
            ssh_control_name='test-control',
            enable_interactive_auth=False,
        )

        with mock.patch.object(command_runner.log_lib,
                               'run_with_log',
                               return_value=(255, '', 'Permission denied.')):
            with mock.patch.object(runner,
                                   '_retry_with_interactive_auth',
                                   return_value=(0, '', '')) as mock_retry:
                runner.run('echo hello',
                           require_outputs=True,
                           separate_stderr=True)
                mock_retry.assert_not_called()


class TestProxyJumpToProxyCommand:
    """Test ProxyJump to ProxyCommand conversion."""

    @pytest.fixture
    def private_key_path(self) -> str:
        """Get the SSH private key path."""
        user_hash = common_utils.get_user_hash()
        key_path, _, _ = auth_utils.get_ssh_key_and_lock_path(user_hash)
        return os.path.expanduser(key_path)

    @pytest.mark.parametrize(
        'ssh_proxy_jump,expected_assertions',
        [
            # host
            (
                'bastion',
                {
                    'must_contain':
                        ['ProxyCommand=', '-W', '[%h]:%p', 'bastion'],
                    'must_not_contain': ['ProxyJump=', '-l '],
                },
            ),
            # user@host
            (
                'ubuntu@bastion',
                {
                    'must_contain': [
                        'ProxyCommand=', '-l ubuntu', '-W', '[%h]:%p', 'bastion'
                    ],
                    'must_not_contain': ['ProxyJump='],
                },
            ),
            # user@host:port
            (
                'admin@bastion:2222',
                {
                    'must_contain':
                        ['ProxyCommand=', '-l admin', '-p 2222', 'bastion'],
                    'must_not_contain': ['ProxyJump='],
                },
            ),
            # ipv4:port
            (
                '1.2.3.4:2222',
                {
                    'must_contain': [
                        'ProxyCommand=',
                        '-p 2222',
                        '1.2.3.4',
                    ],
                    'must_not_contain': ['ProxyJump='],
                },
            ),
            # user@[ipv6]:port
            (
                'admin@[2001:db8::1]:2222',
                {
                    'must_contain': [
                        'ProxyCommand=',
                        '-l admin',
                        '-p 2222',
                        # Brackets should not be included in the host argument.
                        '2001:db8::1',
                    ],
                    'must_not_contain': ['ProxyJump=', '[2001:db8::1]'],
                },
            ),
            # user@ipv6 (no port)
            (
                'root@2001:aaaa:bbbb:ffff::1',
                {
                    'must_contain': [
                        'ProxyCommand=',
                        '-l root',
                        '2001:aaaa:bbbb:ffff::1',
                    ],
                    'must_not_contain': ['ProxyJump=', '-p '],
                },
            ),
            # user@ipv6 (numeric last hextet; still no port)
            (
                'root@2001:db8::22',
                {
                    'must_contain': [
                        'ProxyCommand=',
                        '-l root',
                        '2001:db8::22',
                    ],
                    'must_not_contain': ['ProxyJump=', '-p '],
                },
            ),
            # Multi-hop
            (
                'user1@hop1,user2@hop2',
                {
                    'must_contain':
                        ['ProxyCommand=', '-J user1@hop1', '-l user2', 'hop2'],
                    'must_not_contain': ['ProxyJump='],
                },
            ),
        ],
    )
    def test_proxyjump_to_proxycommand_conversion(self, private_key_path,
                                                  ssh_proxy_jump,
                                                  expected_assertions) -> None:
        """ProxyJump should be converted to equivalent ProxyCommand."""
        runner = command_runner.SSHCommandRunner(
            node=('10.0.0.5', 22),
            ssh_user='ubuntu',
            ssh_private_key=private_key_path,
            ssh_proxy_jump=ssh_proxy_jump,
        )

        ssh_cmd = runner.ssh_base_command(
            ssh_mode=command_runner.SshMode.NON_INTERACTIVE,
            port_forward=None,
            connect_timeout=None,
        )
        ssh_cmd_str = ' '.join(ssh_cmd)

        for expected in expected_assertions['must_contain']:
            assert expected in ssh_cmd_str, (
                f"Expected '{expected}' in command: {ssh_cmd_str}")
        for unexpected in expected_assertions['must_not_contain']:
            assert unexpected not in ssh_cmd_str, (
                f"Unexpected '{unexpected}' in command: {ssh_cmd_str}")

    @pytest.mark.parametrize(
        'ssh_proxy_jump',
        [
            # Invalid bracketed IPv6 port.
            ('admin@[::1]:ssh'),
            # Invalid port.
            ('admin@bastion:notaport'),
            ('bastion:notaport'),
            # Empty host.
            (':22'),
            # Empty user.
            ('@bastion'),
            # Missing host.
            ('admin@'),
            # Malformed bracketed IPv6 (no closing bracket).
            ('admin@[::1'),
            # Malformed bracketed IPv6 (unexpected suffix after bracket).
            ('admin@[::1]foo'),
            # Empty bracketed host.
            ('admin@[]:22'),
        ],
    )
    def test_proxyjump_to_proxycommand_invalid_jump_spec_raises(
            self, private_key_path, ssh_proxy_jump) -> None:
        runner = command_runner.SSHCommandRunner(
            node=('10.0.0.5', 22),
            ssh_user='ubuntu',
            ssh_private_key=private_key_path,
            ssh_proxy_jump=ssh_proxy_jump,
        )

        with pytest.raises(ValueError, match='Invalid'):
            runner.ssh_base_command(
                ssh_mode=command_runner.SshMode.NON_INTERACTIVE,
                port_forward=None,
                connect_timeout=None,
            )
