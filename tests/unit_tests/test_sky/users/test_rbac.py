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


class TestGetRolePermissions:
    """GET /workspaces/config is admin-only unless opted out via
    rbac.restrict_config_to_admins: false; POST is always admin-only."""

    @staticmethod
    def _user_blocklist(restrict):
        # ('rbac', 'roles') -> {} (use defaults);
        # ('rbac', 'restrict_config_to_admins') -> the flag under test.
        def fake_get_nested(keys, default_value=None, *args, **kwargs):
            if keys == ('rbac', 'restrict_config_to_admins'):
                return restrict
            return {}

        with mock.patch('sky.skypilot_config.get_nested',
                        side_effect=fake_get_nested):
            permissions = rbac.get_role_permissions()
        return permissions['user']['permissions']['blocklist']

    def test_config_post_always_blocked_for_user(self):
        assert {
            'path': '/workspaces/config',
            'method': 'POST'
        } in self._user_blocklist(False)

    def test_config_get_not_blocked_when_opted_out(self):
        # rbac.restrict_config_to_admins=false: reads are allowed for the user
        # role.
        assert {
            'path': '/workspaces/config',
            'method': 'GET'
        } not in self._user_blocklist(False)

    def test_config_get_blocked_when_restricted(self):
        # rbac.restrict_config_to_admins=true: reads become admin-only.
        assert {
            'path': '/workspaces/config',
            'method': 'GET'
        } in self._user_blocklist(True)

    def test_restrict_config_to_admins_defaults_to_true(self):
        # With nothing set in config, reads default to admin-only.
        def fake_get_nested(keys, default_value=None, *args, **kwargs):
            return default_value

        with mock.patch('sky.skypilot_config.get_nested',
                        side_effect=fake_get_nested):
            assert rbac.restrict_config_to_admins() is True

    def test_config_get_blocked_even_when_user_role_customized(self):
        # Security: a custom user role in config must not bypass the
        # restriction. GET /workspaces/config is still appended, and the
        # custom entries are preserved.
        custom_roles = {
            'user': {
                'permissions': {
                    'blocklist': [{
                        'path': '/foo',
                        'method': 'POST'
                    }]
                }
            }
        }

        def fake_get_nested(keys, default_value=None, *args, **kwargs):
            if keys == ('rbac', 'restrict_config_to_admins'):
                return True
            if keys == ('rbac', 'roles'):
                return custom_roles
            return {}

        with mock.patch('sky.skypilot_config.get_nested',
                        side_effect=fake_get_nested):
            permissions = rbac.get_role_permissions()
        blocklist = permissions['user']['permissions']['blocklist']
        assert {'path': '/workspaces/config', 'method': 'GET'} in blocklist
        assert {'path': '/foo', 'method': 'POST'} in blocklist


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
        # `/debug/dump_create` is intentionally on the allowlist for
        # support diagnostics (see _DEFAULT_VIEWER_ALLOWLIST); only the
        # download endpoint is admin-only because the dump file itself
        # may contain sensitive state.
        assert {
            'path': '/debug/dump_download/{dump_filename}',
            'method': 'GET',
        } not in allowlist

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


class TestSSHNodePoolsBlocklist:
    """SSH Node Pool mutation routes must be admin-only for the `user` role.

    Regression guard for the missing-authorization gap (CWE-862): the default
    user blocklist must deny every mutating /ssh_node_pools route, while
    leaving the read routes (which are also on the viewer allowlist) allowed.
    The check mirrors the casbin matcher the RBAC middleware uses:
    keyMatch2 on the path and an exact method match.
    """

    @staticmethod
    def _user_role_blocks(path, method):
        from casbin.util import key_match2
        return any(
            entry['method'] == method and key_match2(path, entry['path'])
            for entry in rbac._DEFAULT_USER_BLOCKLIST)

    @pytest.mark.parametrize('path,method', [
        ('/ssh_node_pools', 'POST'),
        ('/ssh_node_pools/prod', 'DELETE'),
        ('/ssh_node_pools/keys', 'POST'),
        ('/ssh_node_pools/keys', 'GET'),
        ('/ssh_node_pools/deploy', 'POST'),
        ('/ssh_node_pools/prod/deploy', 'POST'),
        ('/ssh_node_pools/down', 'POST'),
        ('/ssh_node_pools/prod/down', 'POST'),
    ])
    def test_mutating_routes_blocked_for_user(self, path, method):
        assert self._user_role_blocks(
            path,
            method), (f'{method} {path} must be blocked for the user role')

    @pytest.mark.parametrize('path,method', [
        ('/ssh_node_pools', 'GET'),
        ('/ssh_node_pools/prod/status', 'GET'),
    ])
    def test_read_routes_allowed_for_user(self, path, method):
        assert not self._user_role_blocks(path, method), (
            f'{method} {path} must remain allowed for the user role')
