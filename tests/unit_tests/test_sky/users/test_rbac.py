"""Unit tests for sky.users.rbac (viewer role helpers)."""

from unittest import mock

import pytest

from sky.users import rbac


class TestRoleEnum:
    """The RoleName enum is the source of truth for accepted role strings."""

    def test_viewer_is_supported(self):
        assert 'viewer' in rbac.get_supported_roles()

    def test_admin_and_user_still_supported(self):
        roles = rbac.get_supported_roles()
        assert 'admin' in roles
        assert 'user' in roles


class TestGetViewerAllowlist:
    """rbac.get_viewer_allowlist composes defaults + config + plugin entries."""

    def test_default_is_returned_when_config_empty(self):
        with mock.patch('sky.skypilot_config.get_nested', return_value={}):
            allowlist = rbac.get_viewer_allowlist()
        assert allowlist == rbac._DEFAULT_VIEWER_ALLOWLIST  # pylint: disable=protected-access

    def test_status_post_is_on_default_allowlist(self):
        with mock.patch('sky.skypilot_config.get_nested', return_value={}):
            allowlist = rbac.get_viewer_allowlist()
        assert {'path': '/status', 'method': 'POST'} in allowlist

    def test_launch_is_NOT_on_default_allowlist(self):
        with mock.patch('sky.skypilot_config.get_nested', return_value={}):
            allowlist = rbac.get_viewer_allowlist()
        assert {'path': '/launch', 'method': 'POST'} not in allowlist
        assert {'path': '/down', 'method': 'POST'} not in allowlist
        assert {
            'path': '/users/service-account-tokens',
            'method': 'POST'
        } not in allowlist

    def test_sensitive_reads_NOT_on_default_allowlist(self):
        with mock.patch('sky.skypilot_config.get_nested', return_value={}):
            allowlist = rbac.get_viewer_allowlist()
        # Workspace config GET exposes provider tokens.
        assert {'path': '/workspaces/config', 'method': 'GET'} not in allowlist
        # SSH node-pool keys GET exposes private-key paths.
        assert {
            'path': '/ssh_node_pools/keys',
            'method': 'GET'
        } not in allowlist
        # User export exposes password hashes.
        assert {'path': '/users/export', 'method': 'GET'} not in allowlist
        # Debug dump endpoints expose internal state.
        assert {'path': '/debug/dump_create', 'method': 'POST'} not in allowlist

    def test_websocket_ssh_proxies_NOT_on_default_allowlist(self):
        with mock.patch('sky.skypilot_config.get_nested', return_value={}):
            allowlist = rbac.get_viewer_allowlist()
        for path in ('/kubernetes-pod-ssh-proxy', '/slurm-job-ssh-proxy',
                     '/ssh-interactive-auth'):
            assert {'path': path, 'method': 'GET'} not in allowlist

    def test_operator_overrides_are_additive(self):
        extra = {
            'viewer': {
                'permissions': {
                    'allowlist': [{
                        'path': '/plugins/api/cron/list',
                        'method': 'POST'
                    }]
                }
            }
        }
        # rbac.get_viewer_allowlist calls skypilot_config.get_nested with
        # ('rbac', 'roles'); we mock that one call.
        with mock.patch('sky.skypilot_config.get_nested', return_value=extra):
            allowlist = rbac.get_viewer_allowlist()
        # The default is still present...
        assert {'path': '/status', 'method': 'POST'} in allowlist
        # ...and the operator-supplied entry is added on top.
        assert {'path': '/plugins/api/cron/list', 'method': 'POST'} in allowlist

    def test_operator_overrides_dedupe(self):
        # Operator re-asserts an entry that's already in the default;
        # it should appear once.
        extra = {
            'viewer': {
                'permissions': {
                    'allowlist': [{
                        'path': '/status',
                        'method': 'POST'
                    }]
                }
            }
        }
        with mock.patch('sky.skypilot_config.get_nested', return_value=extra):
            allowlist = rbac.get_viewer_allowlist()
        count = sum(1 for r in allowlist if r == {
            'path': '/status',
            'method': 'POST'
        })
        assert count == 1

    def test_plugin_entries_merged(self):
        with mock.patch('sky.skypilot_config.get_nested', return_value={}):
            allowlist = rbac.get_viewer_allowlist(plugin_allowlist=[{
                'path': '/plugins/api/foo/*',
                'method': 'GET'
            }])
        assert {'path': '/plugins/api/foo/*', 'method': 'GET'} in allowlist

    def test_malformed_operator_entry_is_skipped(self):
        # Missing 'method' field -> should be logged and skipped, not crash.
        extra = {
            'viewer': {
                'permissions': {
                    'allowlist': [{
                        'path': '/foo'
                    }, 'not-a-dict']
                }
            }
        }
        with mock.patch('sky.skypilot_config.get_nested', return_value=extra):
            allowlist = rbac.get_viewer_allowlist()
        # No crash, defaults preserved.
        assert {'path': '/status', 'method': 'POST'} in allowlist


class TestDefaultRoleConfig:

    def test_default_role_falls_back_to_admin(self):
        with mock.patch('sky.skypilot_config.get_nested', return_value='admin'):
            assert rbac.get_default_role() == 'admin'

    def test_default_role_can_be_viewer(self):
        with mock.patch('sky.skypilot_config.get_nested',
                        return_value='viewer'):
            assert rbac.get_default_role() == 'viewer'
