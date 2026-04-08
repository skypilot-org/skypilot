"""Tests for sky.utils.kubernetes.kubernetes_deploy_utils.

Covers the new PATH-merging logic introduced in
'[Core] Enhance local_up to respect PATH environment variable in current
shell', as well as the existing generate_kind_config and _get_port_range
utilities.
"""
import os
import subprocess
from unittest import mock

import pytest

from sky.utils.kubernetes import kubernetes_deploy_utils


# ---------------------------------------------------------------------------
# Tests for _merge_given_path_with_shell_path
# ---------------------------------------------------------------------------
class TestMergeGivenPathWithShellPath:
    """Tests for _merge_given_path_with_shell_path."""

    @mock.patch.dict(os.environ, {'PATH': '/usr/bin:/usr/local/bin'},
                     clear=False)
    @mock.patch('subprocess.run')
    def test_no_given_path_uses_current_env(self, mock_run):
        """When given_path is None the current PATH should be kept."""
        mock_run.return_value = subprocess.CompletedProcess(args=[],
                                                            returncode=0,
                                                            stdout='/usr/bin\n')
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path(None)
        assert '/usr/bin' in env['PATH'].split(os.pathsep)
        assert '/usr/local/bin' in env['PATH'].split(os.pathsep)

    @mock.patch.dict(os.environ, {'PATH': '/usr/bin:/usr/local/bin'},
                     clear=False)
    @mock.patch('subprocess.run')
    def test_given_path_prepended(self, mock_run):
        """given_path entries must appear before current PATH entries."""
        mock_run.return_value = subprocess.CompletedProcess(args=[],
                                                            returncode=0,
                                                            stdout='/usr/bin\n')
        given = '/my/custom/bin:/another/bin'
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path(given)
        parts = env['PATH'].split(os.pathsep)
        assert parts[0] == '/my/custom/bin'
        assert parts[1] == '/another/bin'

    @mock.patch.dict(os.environ, {'PATH': '/usr/bin:/usr/local/bin'},
                     clear=False)
    @mock.patch('subprocess.run')
    def test_given_path_deduplicates_current(self, mock_run):
        """Entries already in given_path should not be duplicated from
        the current PATH."""
        mock_run.return_value = subprocess.CompletedProcess(args=[],
                                                            returncode=0,
                                                            stdout='/usr/bin\n')
        given = '/usr/bin:/my/bin'
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path(given)
        parts = env['PATH'].split(os.pathsep)
        # /usr/bin should appear only once (from given_path)
        assert parts.count('/usr/bin') == 1
        # /usr/local/bin from current env should be added
        assert '/usr/local/bin' in parts

    @mock.patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=False)
    @mock.patch('subprocess.run')
    def test_login_shell_entries_appended(self, mock_run):
        """New entries from the login shell PATH should be appended."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='/usr/bin:/home/user/.local/bin\n')
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path(None)
        parts = env['PATH'].split(os.pathsep)
        assert '/home/user/.local/bin' in parts

    @mock.patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=False)
    @mock.patch('subprocess.run')
    def test_login_shell_entries_not_duplicated(self, mock_run):
        """Entries already present should not be added again from the login
        shell."""
        mock_run.return_value = subprocess.CompletedProcess(args=[],
                                                            returncode=0,
                                                            stdout='/usr/bin\n')
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path(None)
        parts = env['PATH'].split(os.pathsep)
        assert parts.count('/usr/bin') == 1

    @mock.patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=False)
    @mock.patch('subprocess.run',
                side_effect=subprocess.TimeoutExpired(cmd='bash', timeout=10))
    def test_login_shell_timeout_falls_back(self, mock_run):
        """A timeout when querying the login shell should be silently
        ignored and the PATH should still contain current entries."""
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path(None)
        assert '/usr/bin' in env['PATH'].split(os.pathsep)

    @mock.patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=False)
    @mock.patch('subprocess.run', side_effect=FileNotFoundError('no shell'))
    def test_login_shell_not_found_falls_back(self, mock_run):
        """If the login shell executable is not found the error should be
        suppressed and the PATH should still be usable."""
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path(None)
        assert '/usr/bin' in env['PATH'].split(os.pathsep)

    @mock.patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=False)
    @mock.patch('subprocess.run',
                side_effect=subprocess.CalledProcessError(1, 'bash'))
    def test_login_shell_nonzero_exit_falls_back(self, mock_run):
        """A non-zero exit from the login shell should be suppressed."""
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path(None)
        assert '/usr/bin' in env['PATH'].split(os.pathsep)

    @mock.patch.dict(os.environ, {
        'PATH': '/a:/b',
        'SHELL': '/bin/zsh'
    },
                     clear=False)
    @mock.patch('subprocess.run')
    def test_uses_shell_env_variable(self, mock_run):
        """The function should use the SHELL env var for the login shell."""
        mock_run.return_value = subprocess.CompletedProcess(args=[],
                                                            returncode=0,
                                                            stdout='/a\n')
        kubernetes_deploy_utils._merge_given_path_with_shell_path(None)
        call_args = mock_run.call_args
        assert call_args[0][0][0] == '/bin/zsh'

    @mock.patch.dict(os.environ, {'PATH': '/a:/b'}, clear=False)
    @mock.patch('subprocess.run')
    def test_defaults_to_bash_when_no_shell_env(self, mock_run):
        """If SHELL is not set, /bin/bash should be the default."""
        mock_run.return_value = subprocess.CompletedProcess(args=[],
                                                            returncode=0,
                                                            stdout='/a\n')
        env_without_shell = os.environ.copy()
        env_without_shell.pop('SHELL', None)
        with mock.patch.dict(os.environ, env_without_shell, clear=True):
            kubernetes_deploy_utils._merge_given_path_with_shell_path(None)
        call_args = mock_run.call_args
        assert call_args[0][0][0] == '/bin/bash'

    @mock.patch.dict(os.environ, {'PATH': ''}, clear=False)
    @mock.patch('subprocess.run')
    def test_empty_current_path(self, mock_run):
        """An empty current PATH should still produce a valid env dict."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='/login/bin\n')
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path(None)
        assert '/login/bin' in env['PATH'].split(os.pathsep)

    @mock.patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=False)
    @mock.patch('subprocess.run')
    def test_given_path_with_login_shell(self, mock_run):
        """given_path, current PATH, and login shell entries should all
        be merged with correct priority order."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='/usr/bin:/login/only\n')
        given = '/given/bin'
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path(given)
        parts = env['PATH'].split(os.pathsep)
        # Priority: given > current > login
        assert parts.index('/given/bin') < parts.index('/usr/bin')
        assert '/login/only' in parts

    @mock.patch.dict(os.environ, {
        'PATH': '/usr/bin',
        'HOME': '/home/test'
    },
                     clear=False)
    @mock.patch('subprocess.run')
    def test_returns_full_env_copy(self, mock_run):
        """The returned dict should contain all env vars, not just PATH."""
        mock_run.return_value = subprocess.CompletedProcess(args=[],
                                                            returncode=0,
                                                            stdout='/usr/bin\n')
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path(None)
        assert env.get('HOME') == '/home/test'

    @mock.patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=False)
    @mock.patch('subprocess.run')
    def test_empty_given_path_string(self, mock_run):
        """An empty string given_path should be treated as falsy (no
        given_path)."""
        mock_run.return_value = subprocess.CompletedProcess(args=[],
                                                            returncode=0,
                                                            stdout='/usr/bin\n')
        env = kubernetes_deploy_utils._merge_given_path_with_shell_path('')
        # '' is falsy, so should behave like None
        assert '/usr/bin' in env['PATH'].split(os.pathsep)


# ---------------------------------------------------------------------------
# Tests for generate_kind_config
# ---------------------------------------------------------------------------
class TestGenerateKindConfig:
    """Tests for generate_kind_config."""

    def test_basic_config_structure(self):
        config = kubernetes_deploy_utils.generate_kind_config(30000)
        assert 'kind: Cluster' in config
        assert 'apiVersion: kind.x-k8s.io/v1alpha4' in config
        assert 'role: control-plane' in config

    def test_port_mappings_count(self):
        config = kubernetes_deploy_utils.generate_kind_config(30000)
        # Should have LOCAL_CLUSTER_PORT_RANGE port mappings
        assert config.count('containerPort:') == \
            kubernetes_deploy_utils.LOCAL_CLUSTER_PORT_RANGE

    def test_port_mapping_values(self):
        port_start = 40000
        config = kubernetes_deploy_utils.generate_kind_config(port_start)
        assert f'containerPort: {kubernetes_deploy_utils.LOCAL_CLUSTER_INTERNAL_PORT_START}' in config
        assert f'hostPort: {port_start}' in config

    def test_gpu_support(self):
        config = kubernetes_deploy_utils.generate_kind_config(30000, gpus=True)
        assert '/dev/null' in config
        assert 'nvidia-container-devices' in config

    def test_no_gpu_support(self):
        config = kubernetes_deploy_utils.generate_kind_config(30000, gpus=False)
        assert 'nvidia-container-devices' not in config

    def test_multi_node(self):
        config = kubernetes_deploy_utils.generate_kind_config(30000,
                                                              num_nodes=3)
        # Should have 2 worker nodes (num_nodes - 1)
        assert config.count('role: worker') == 2

    def test_single_node_no_workers(self):
        config = kubernetes_deploy_utils.generate_kind_config(30000,
                                                              num_nodes=1)
        assert 'role: worker' not in config

    def test_service_node_port_range(self):
        config = kubernetes_deploy_utils.generate_kind_config(30000)
        expected_range = (
            f'{kubernetes_deploy_utils.LOCAL_CLUSTER_INTERNAL_PORT_START}-'
            f'{kubernetes_deploy_utils.LOCAL_CLUSTER_INTERNAL_PORT_END}')
        assert expected_range in config


# ---------------------------------------------------------------------------
# Tests for _get_port_range
# ---------------------------------------------------------------------------
class TestGetPortRange:
    """Tests for _get_port_range."""

    def test_default_cluster_default_port(self):
        start, end = kubernetes_deploy_utils._get_port_range('skypilot', None)
        assert start == kubernetes_deploy_utils.LOCAL_CLUSTER_INTERNAL_PORT_START
        assert end == start + kubernetes_deploy_utils.LOCAL_CLUSTER_PORT_RANGE - 1

    def test_default_cluster_correct_port(self):
        start, end = kubernetes_deploy_utils._get_port_range('skypilot', 30000)
        assert start == 30000
        assert end == 30099

    def test_default_cluster_wrong_port_raises(self):
        with pytest.raises(ValueError, match='30000 to 30099'):
            kubernetes_deploy_utils._get_port_range('skypilot', 40000)

    def test_non_default_cluster_with_reserved_port_raises(self):
        with pytest.raises(ValueError, match='reserved'):
            kubernetes_deploy_utils._get_port_range('my-cluster', 30000)

    def test_non_default_cluster_non_multiple_of_100_raises(self):
        with pytest.raises(ValueError, match='multiple of 100'):
            kubernetes_deploy_utils._get_port_range('my-cluster', 30050)

    def test_non_default_cluster_valid_port(self):
        start, end = kubernetes_deploy_utils._get_port_range(
            'my-cluster', 40000)
        assert start == 40000
        assert end == 40099

    def test_non_default_cluster_random_port(self):
        """When port_start is None for a non-default cluster, a random
        port in the 301xx-399xx range should be chosen."""
        start, end = kubernetes_deploy_utils._get_port_range('my-cluster', None)
        assert start % 100 == 0
        assert 30100 <= start <= 39900
        assert end == start + kubernetes_deploy_utils.LOCAL_CLUSTER_PORT_RANGE - 1


# ---------------------------------------------------------------------------
# Tests for LocalUpBody / LocalDownBody payloads
# ---------------------------------------------------------------------------
class TestLocalUpBody:
    """Tests for LocalUpBody with the new path field."""

    def test_path_field_defaults_to_none(self):
        from sky.server.requests import payloads
        body = payloads.LocalUpBody(gpus=True)
        assert body.path is None

    def test_path_field_set(self):
        from sky.server.requests import payloads
        body = payloads.LocalUpBody(gpus=True, path='/usr/bin:/custom/bin')
        assert body.path == '/usr/bin:/custom/bin'

    def test_path_field_serialization(self):
        import json

        from sky.server.requests import payloads
        body = payloads.LocalUpBody(gpus=True,
                                    name='test',
                                    port_start=40000,
                                    path='/my/path')
        data = json.loads(body.model_dump_json())
        assert data['path'] == '/my/path'
        assert data['gpus'] is True
        assert data['name'] == 'test'
        assert data['port_start'] == 40000

    def test_local_down_body_has_path(self):
        from sky.server.requests import payloads
        body = payloads.LocalDownBody(name='test', path='/usr/bin')
        assert hasattr(body, 'path') and \
            'path' in body.model_fields
        assert body.path == '/usr/bin'
