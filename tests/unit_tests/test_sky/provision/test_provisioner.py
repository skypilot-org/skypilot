"""Unit tests for sky/provision/provisioner.py."""

import os
from unittest import mock

import pytest

from sky.provision import provisioner


class TestSshProbeCommand:
    """Tests for _ssh_probe_command() function."""

    def test_basic_command_structure(self):
        """Test that basic SSH probe command has correct structure."""
        command = provisioner._ssh_probe_command(ip='192.168.1.1',
                                                 ssh_port=22,
                                                 ssh_user='testuser',
                                                 ssh_private_key='/path/to/key',
                                                 ssh_probe_timeout=10)

        assert 'ssh' in command
        assert '-T' in command
        assert '-i' in command
        assert '/path/to/key' in command
        assert 'testuser@192.168.1.1' in command
        assert '-p' in command
        assert '22' in command
        assert 'uptime' in command

    def test_contains_security_options(self):
        """Test that SSH command contains security options."""
        command = provisioner._ssh_probe_command(ip='10.0.0.1',
                                                 ssh_port=2222,
                                                 ssh_user='admin',
                                                 ssh_private_key='/key',
                                                 ssh_probe_timeout=5)

        # Check security options
        assert 'StrictHostKeyChecking=no' in ' '.join(command)
        assert 'PasswordAuthentication=no' in ' '.join(command)
        assert 'IdentitiesOnly=yes' in ' '.join(command)
        assert f'UserKnownHostsFile={os.devnull}' in ' '.join(command)

    def test_timeout_option(self):
        """Test that SSH command contains timeout option."""
        command = provisioner._ssh_probe_command(ip='192.168.1.1',
                                                 ssh_port=22,
                                                 ssh_user='user',
                                                 ssh_private_key='/key',
                                                 ssh_probe_timeout=30)

        assert 'ConnectTimeout=30s' in ' '.join(command)

    def test_with_proxy_command(self):
        """Test SSH command with proxy command."""
        proxy_cmd = 'ssh -W %h:%p bastion.example.com'
        command = provisioner._ssh_probe_command(ip='192.168.1.1',
                                                 ssh_port=22,
                                                 ssh_user='user',
                                                 ssh_private_key='/key',
                                                 ssh_probe_timeout=10,
                                                 ssh_proxy_command=proxy_cmd)

        assert f'ProxyCommand={proxy_cmd}' in ' '.join(command)

    def test_without_proxy_command(self):
        """Test SSH command without proxy command."""
        command = provisioner._ssh_probe_command(ip='192.168.1.1',
                                                 ssh_port=22,
                                                 ssh_user='user',
                                                 ssh_private_key='/key',
                                                 ssh_probe_timeout=10,
                                                 ssh_proxy_command=None)

        # Should not contain ProxyCommand
        assert 'ProxyCommand' not in ' '.join(command)

    def test_custom_port(self):
        """Test SSH command with custom port."""
        command = provisioner._ssh_probe_command(ip='192.168.1.1',
                                                 ssh_port=3000,
                                                 ssh_user='user',
                                                 ssh_private_key='/key',
                                                 ssh_probe_timeout=10)

        idx = command.index('-p')
        assert command[idx + 1] == '3000'


class TestShlexJoin:
    """Tests for _shlex_join() function."""

    def test_simple_command(self):
        """Test joining a simple command."""
        result = provisioner._shlex_join(['ls', '-la', '/tmp'])
        assert result == 'ls -la /tmp'

    def test_command_with_spaces(self):
        """Test joining command with arguments containing spaces."""
        result = provisioner._shlex_join(['echo', 'hello world', 'test'])
        assert result == "echo 'hello world' test"

    def test_command_with_special_chars(self):
        """Test joining command with special characters."""
        result = provisioner._shlex_join(['echo', '$HOME', '${VAR}'])
        # Special chars should be quoted
        assert "'$HOME'" in result or '"$HOME"' in result

    def test_empty_command(self):
        """Test joining empty command list."""
        result = provisioner._shlex_join([])
        assert result == ''

    def test_single_command(self):
        """Test joining single command."""
        result = provisioner._shlex_join(['ls'])
        assert result == 'ls'


class TestMaxRetryConstant:
    """Tests for _MAX_RETRY constant."""

    def test_max_retry_is_positive(self):
        """Test that _MAX_RETRY is a positive integer."""
        assert provisioner._MAX_RETRY > 0
        assert isinstance(provisioner._MAX_RETRY, int)

    def test_max_retry_value(self):
        """Test the current value of _MAX_RETRY."""
        assert provisioner._MAX_RETRY == 3


class TestWaitSshConnectionDirect:
    """Tests for _wait_ssh_connection_direct() function."""

    @mock.patch('socket.create_connection')
    def test_returns_true_on_successful_connection(self, mock_socket):
        """Test that function returns True when SSH is ready."""
        mock_conn = mock.Mock()
        mock_conn.recv.return_value = b'SSH-2.0-OpenSSH_8.2'
        mock_socket.return_value.__enter__ = mock.Mock(return_value=mock_conn)
        mock_socket.return_value.__exit__ = mock.Mock(return_value=None)

        with mock.patch.object(provisioner,
                               '_wait_ssh_connection_indirect',
                               return_value=(True, '')):
            success, stderr = provisioner._wait_ssh_connection_direct(
                ip='192.168.1.1',
                ssh_port=22,
                ssh_user='user',
                ssh_private_key='/key',
                ssh_probe_timeout=10)

            assert success is True
            assert stderr == ''

    @mock.patch('socket.create_connection')
    def test_returns_false_on_timeout(self, mock_socket):
        """Test that function returns False on socket timeout."""
        import socket
        mock_socket.side_effect = socket.timeout('Connection timed out')

        success, stderr = provisioner._wait_ssh_connection_direct(
            ip='192.168.1.1',
            ssh_port=22,
            ssh_user='user',
            ssh_private_key='/key',
            ssh_probe_timeout=10)

        assert success is False
        assert 'Timeout' in stderr

    def test_raises_assertion_with_proxy_command(self):
        """Test that assertion is raised when proxy command is provided."""
        with pytest.raises(AssertionError):
            provisioner._wait_ssh_connection_direct(
                ip='192.168.1.1',
                ssh_port=22,
                ssh_user='user',
                ssh_private_key='/key',
                ssh_probe_timeout=10,
                ssh_proxy_command='ssh -W %h:%p bastion')


class TestWaitSshConnectionIndirect:
    """Tests for _wait_ssh_connection_indirect() function."""

    @mock.patch('subprocess.run')
    def test_returns_true_on_success(self, mock_run):
        """Test that function returns True on successful SSH."""
        mock_run.return_value = mock.Mock(returncode=0)

        success, stderr = provisioner._wait_ssh_connection_indirect(
            ip='192.168.1.1',
            ssh_port=22,
            ssh_user='user',
            ssh_private_key='/key',
            ssh_probe_timeout=10)

        assert success is True
        assert stderr == ''

    @mock.patch('subprocess.run')
    def test_returns_false_on_failure(self, mock_run):
        """Test that function returns False on SSH failure."""
        mock_run.return_value = mock.Mock(returncode=255,
                                          stderr=b'Connection refused')

        success, stderr = provisioner._wait_ssh_connection_indirect(
            ip='192.168.1.1',
            ssh_port=22,
            ssh_user='user',
            ssh_private_key='/key',
            ssh_probe_timeout=10)

        assert success is False
        assert 'Error' in stderr

    @mock.patch('subprocess.run')
    def test_returns_false_on_timeout(self, mock_run):
        """Test that function returns False on subprocess timeout."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=['ssh'],
                                                         timeout=10)

        success, stderr = provisioner._wait_ssh_connection_indirect(
            ip='192.168.1.1',
            ssh_port=22,
            ssh_user='user',
            ssh_private_key='/key',
            ssh_probe_timeout=10)

        assert success is False
        assert 'Error' in stderr


class TestTeardownCluster:
    """Tests for teardown_cluster() function."""

    @mock.patch(
        'sky.provision.provisioner.provision_volume.delete_ephemeral_volumes')
    @mock.patch(
        'sky.provision.provisioner.metadata_utils.remove_cluster_metadata')
    @mock.patch('sky.provision.provisioner.provision.terminate_instances')
    def test_terminate_calls_correct_functions(self, mock_terminate,
                                               mock_remove_metadata,
                                               mock_delete_volumes):
        """Test that terminate=True calls termination functions."""
        from sky.utils import resources_utils

        cluster_name = resources_utils.ClusterName(
            display_name='test-cluster', name_on_cloud='test-cluster-abc123')
        provider_config = {'type': 'aws'}

        provisioner.teardown_cluster(cloud_name='aws',
                                     cluster_name=cluster_name,
                                     terminate=True,
                                     provider_config=provider_config)

        mock_terminate.assert_called_once_with('aws', 'test-cluster-abc123',
                                               provider_config)
        mock_remove_metadata.assert_called_once_with('test-cluster-abc123')
        mock_delete_volumes.assert_called_once_with(provider_config)

    @mock.patch('sky.provision.provisioner.provision.stop_instances')
    def test_stop_calls_stop_instances(self, mock_stop):
        """Test that terminate=False calls stop function."""
        from sky.utils import resources_utils

        cluster_name = resources_utils.ClusterName(
            display_name='test-cluster', name_on_cloud='test-cluster-abc123')
        provider_config = {'type': 'gcp'}

        provisioner.teardown_cluster(cloud_name='gcp',
                                     cluster_name=cluster_name,
                                     terminate=False,
                                     provider_config=provider_config)

        mock_stop.assert_called_once_with('gcp', 'test-cluster-abc123',
                                          provider_config)
