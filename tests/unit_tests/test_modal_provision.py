"""Unit tests for Modal provisioner (TEST-01, D-03).

All tests mock the Modal SDK via monkeypatch on 'sky.adaptors.modal.modal'.
NO live Modal API calls are made.

Critical import ordering: monkeypatch.setattr BEFORE importing
sky.provision.modal.utils or sky.provision.modal.instance, to avoid the
module-level `modal = modal_adaptor.modal` binding caching the LazyImport
proxy before the monkeypatch takes effect (see 04-PATTERNS.md).
"""
import importlib
import sys
from unittest.mock import MagicMock

import pytest


def _make_mock_modal():
    """Returns a fresh MagicMock with a plausible exception hierarchy."""
    mock_modal = MagicMock()
    # modal.exception.SandboxTimeoutError and modal.exception.NotFoundError
    # must be real exception classes so `except` clauses work correctly.
    mock_modal.exception.SandboxTimeoutError = type('SandboxTimeoutError',
                                                    (Exception,), {})
    mock_modal.exception.NotFoundError = type('NotFoundError', (Exception,), {})
    return mock_modal


def _reload_utils(monkeypatch, mock_modal):
    """Patches the adaptor attribute and returns a freshly-imported utils module.

    If sky.provision.modal.utils is already cached in sys.modules, reload it
    so the module-level `modal = modal_adaptor.modal` re-executes and picks up
    the monkeypatched value.
    """
    monkeypatch.setattr('sky.adaptors.modal.modal', mock_modal)
    # Remove cached module so module-level bindings re-execute.
    for mod_name in list(sys.modules):
        if mod_name in ('sky.provision.modal.utils',
                        'sky.provision.modal.instance', 'sky.provision.modal'):
            del sys.modules[mod_name]
    from sky.provision.modal import utils  # noqa: PLC0415
    return utils


def _reload_instance(monkeypatch, mock_modal):
    """Same as _reload_utils but returns the instance module."""
    monkeypatch.setattr('sky.adaptors.modal.modal', mock_modal)
    for mod_name in list(sys.modules):
        if mod_name in ('sky.provision.modal.utils',
                        'sky.provision.modal.instance', 'sky.provision.modal'):
            del sys.modules[mod_name]
    from sky.provision.modal import instance  # noqa: PLC0415
    return instance


class TestLaunch:
    """Tests for sky.provision.modal.utils.launch()."""

    def _make_sandbox_mock(self, host='ssh.modal.run', port=12345):
        """Returns a mock Sandbox with a working tunnels() callable."""
        mock_sandbox = MagicMock()
        mock_sandbox.object_id = 'sb-abc123'
        tunnel_mock = MagicMock()
        tunnel_mock.tcp_socket = (host, port)
        mock_sandbox.tunnels.return_value = {22: tunnel_mock}
        return mock_sandbox

    def test_create_kwargs(self, monkeypatch):
        """PROV-01 / D-03: launch() passes timeout=86400, unencrypted_ports=[22],
        idle_timeout=None, and tags include cluster/node keys."""
        mock_modal = _make_mock_modal()
        utils = _reload_utils(monkeypatch, mock_modal)

        mock_sandbox = self._make_sandbox_mock()
        mock_modal.Sandbox.create.return_value = mock_sandbox
        mock_modal.App.lookup.return_value = MagicMock()
        mock_modal.Image.debian_slim.return_value.apt_install.return_value = (
            MagicMock())

        utils.launch(
            cluster_name='test-cluster',
            node_type='head',
            instance_type='1x_T4',
            region='us',
            image_id='default',
            public_key='ssh-rsa AAAA test',
            gpu_str='T4',
            cpu=8.0,
            memory_mib=29696,
        )

        assert mock_modal.Sandbox.create.called, 'Sandbox.create must be called'
        call_kwargs = mock_modal.Sandbox.create.call_args[1]
        assert call_kwargs['timeout'] == 86400, (
            f'timeout must be 86400 (24h); got {call_kwargs["timeout"]}')
        assert call_kwargs['unencrypted_ports'] == [
            22
        ], (f'unencrypted_ports must be [22]; got {call_kwargs["unencrypted_ports"]}'
           )
        assert call_kwargs['idle_timeout'] is None, (
            f'idle_timeout must be None; got {call_kwargs["idle_timeout"]}')
        tags = call_kwargs['tags']
        assert 'skypilot-cluster' in tags, (
            f'tags must include skypilot-cluster; got {tags}')
        assert 'skypilot-node' in tags, (
            f'tags must include skypilot-node; got {tags}')
        assert tags['skypilot-cluster'] == 'test-cluster', (
            f'skypilot-cluster tag must equal cluster_name; got {tags}')

    def test_entrypoint_has_sudo_and_sleep_infinity(self, monkeypatch):
        """PROV-01 / D-03: _build_setup_cmd contains 'sleep infinity' and
        installs sudo (debian_slim has no sudo by default)."""
        mock_modal = _make_mock_modal()
        utils = _reload_utils(monkeypatch, mock_modal)

        cmd = utils._build_setup_cmd('ssh-rsa AAAA test')  # pylint: disable=protected-access
        assert 'sleep infinity' in cmd, (
            f'Entrypoint must contain "sleep infinity" to keep container alive. '
            f'Got: {cmd!r}')
        assert 'sudo' in cmd, (
            f'Setup cmd must install sudo (debian_slim has none). '
            f'Got: {cmd!r}')
        assert '/usr/sbin/sshd -D &' in cmd, (
            f'Setup cmd must start sshd in background. Got: {cmd!r}')

    def test_returns_object_id(self, monkeypatch):
        """launch() returns sandbox.object_id."""
        mock_modal = _make_mock_modal()
        utils = _reload_utils(monkeypatch, mock_modal)

        mock_sandbox = self._make_sandbox_mock()
        mock_sandbox.object_id = 'sb-return-me'
        mock_modal.Sandbox.create.return_value = mock_sandbox
        mock_modal.App.lookup.return_value = MagicMock()
        mock_modal.Image.debian_slim.return_value.apt_install.return_value = (
            MagicMock())

        result = utils.launch(
            cluster_name='c',
            node_type='head',
            instance_type='1x_T4',
            region='us',
            image_id='default',
            public_key='ssh-rsa AAAA test',
            gpu_str=None,
            cpu=None,
            memory_mib=None,
        )
        assert result == 'sb-return-me', (
            f'launch() must return sandbox.object_id; got {result!r}')


class TestListInstances:
    """Tests for sky.provision.modal.utils.list_instances()."""

    def test_node_name_from_tag_not_name_attr(self, monkeypatch):
        """PROV-02 / D-03: list_instances builds node name from the
        skypilot-node tag, NOT from a .name attribute (Modal Sandbox has
        none)."""
        mock_modal = _make_mock_modal()
        utils = _reload_utils(monkeypatch, mock_modal)

        mock_sandbox = MagicMock()
        mock_sandbox.object_id = 'sb-abc'
        mock_sandbox.get_tags.return_value = {
            'skypilot-cluster': 'my-cluster',
            'skypilot-node': 'head',
        }
        # Ensure .name is NOT used by deleting it from the mock spec
        if hasattr(mock_sandbox, 'name'):
            del mock_sandbox.name

        fresh_sandbox = MagicMock()
        tunnel_mock = MagicMock()
        tunnel_mock.tcp_socket = ('h.modal.run', 9001)
        fresh_sandbox.tunnels.return_value = {22: tunnel_mock}
        mock_modal.Sandbox.list.return_value = [mock_sandbox]
        mock_modal.Sandbox.from_id.return_value = fresh_sandbox

        result = utils.list_instances('my-cluster')
        assert 'sb-abc' in result, (
            f'Expected sandbox sb-abc in result; got {list(result.keys())}')
        name = result['sb-abc']['name']
        assert name == 'my-cluster-head', (
            f'Node name must be "<cluster>-<role>" from tag; got {name!r}')

    def test_fresh_sandbox_from_id_each_call(self, monkeypatch):
        """PROV-02 / D-03: list_instances calls Sandbox.from_id() to force
        a fresh tunnel re-query per D-03."""
        mock_modal = _make_mock_modal()
        utils = _reload_utils(monkeypatch, mock_modal)

        mock_sandbox = MagicMock()
        mock_sandbox.object_id = 'sb-fresh'
        mock_sandbox.get_tags.return_value = {
            'skypilot-cluster': 'c1',
            'skypilot-node': 'head',
        }
        fresh = MagicMock()
        tunnel_mock = MagicMock()
        tunnel_mock.tcp_socket = ('t.modal.run', 54321)
        fresh.tunnels.return_value = {22: tunnel_mock}
        mock_modal.Sandbox.list.return_value = [mock_sandbox]
        mock_modal.Sandbox.from_id.return_value = fresh

        utils.list_instances('c1')

        mock_modal.Sandbox.from_id.assert_called_once_with('sb-fresh')

    def test_ssh_port_from_tunnel_not_hardcoded(self, monkeypatch):
        """PROV-02: ssh_port in the returned dict is the tunnel tcp_socket port
        (e.g. 12345), NOT the hardcoded value 22."""
        mock_modal = _make_mock_modal()
        utils = _reload_utils(monkeypatch, mock_modal)

        mock_sandbox = MagicMock()
        mock_sandbox.object_id = 'sb-port'
        mock_sandbox.get_tags.return_value = {
            'skypilot-cluster': 'c2',
            'skypilot-node': 'head',
        }
        fresh = MagicMock()
        tunnel_mock = MagicMock()
        tunnel_mock.tcp_socket = ('tunnel.modal.run', 55555)
        fresh.tunnels.return_value = {22: tunnel_mock}
        mock_modal.Sandbox.list.return_value = [mock_sandbox]
        mock_modal.Sandbox.from_id.return_value = fresh

        result = utils.list_instances('c2')
        ssh_port = result['sb-port']['ssh_port']
        assert ssh_port == 55555, (
            f'ssh_port must come from tunnel (55555), NOT hardcoded 22. '
            f'Got: {ssh_port}')
        assert ssh_port != 22, 'ssh_port must NOT be hardcoded 22'


class TestRemove:
    """Tests for sky.provision.modal.utils.remove()."""

    def test_calls_terminate_via_from_id(self, monkeypatch):
        """PROV-03 / D-03: remove() calls Sandbox.from_id(id).terminate()."""
        mock_modal = _make_mock_modal()
        utils = _reload_utils(monkeypatch, mock_modal)

        mock_sb = MagicMock()
        mock_modal.Sandbox.from_id.return_value = mock_sb

        utils.remove('sb-xyz')

        mock_modal.Sandbox.from_id.assert_called_once_with('sb-xyz')
        mock_sb.terminate.assert_called_once()
