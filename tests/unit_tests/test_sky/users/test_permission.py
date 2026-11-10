"""Unit tests for permission service using pytest."""

import os
import threading
import time
from unittest import mock

import casbin
import pytest
import sqlalchemy
import sqlalchemy_adapter

from sky import models
from sky.skylet import constants
from sky.users import permission
from sky.users import rbac
from sky.utils import common
from sky.utils import locks
from sky.utils.db import migration_utils

_MODEL_PATH = os.path.join(os.path.dirname(permission.__file__), 'model.conf')


@pytest.fixture
def mock_users():
    """Create mock users for testing."""
    user1 = models.User(id='user1', name='Alice')
    user2 = models.User(id='user2', name='Bob')
    user3 = models.User(id='user3', name='Charlie')
    return [user1, user2, user3]


@pytest.fixture
def reset_permission_singleton():
    """Reset permission singleton before and after each test."""
    # Reset before test
    permission._enforcer_instance = None
    yield
    # Reset after test
    permission._enforcer_instance = None


@pytest.fixture
def cleanup_env_vars():
    """Clean up environment variables after each test."""
    yield
    env_vars_to_remove = [
        constants.ENV_VAR_IS_SKYPILOT_SERVER,
        constants.SKYPILOT_INITIAL_BASIC_AUTH
    ]
    for env_var in env_vars_to_remove:
        if env_var in os.environ:
            del os.environ[env_var]


@pytest.fixture
def policy_engine(tmp_path, monkeypatch):
    """A state database migrated by the real migrations.

    `initialize_and_get_db()` points here too, so services built by
    `_lazy_initialize` use it.
    """
    url = f'sqlite:///{tmp_path}/state.db'
    migration_utils.safe_alembic_upgrade(
        sqlalchemy.create_engine(url),
        migration_utils.GLOBAL_USER_STATE_DB_NAME,
        migration_utils.GLOBAL_USER_STATE_VERSION)
    engine = sqlalchemy.create_engine(url)
    monkeypatch.setattr(permission.global_user_state, 'initialize_and_get_db',
                        lambda: engine)
    yield engine
    engine.dispose()


def _service_over(engine):
    """A PermissionService with its own enforcer over `engine`."""
    service = permission.PermissionService()
    service.enforcer = casbin.SyncedEnforcer(
        _MODEL_PATH,
        sqlalchemy_adapter.Adapter(engine,
                                   db_class=sqlalchemy_adapter.CasbinRule))
    return service


def _stored(engine, ptype):
    """Stored rules of `ptype`, sorted, duplicates kept."""
    fields = 'v0, v1' if ptype == 'g' else 'v0, v1, v2'
    with engine.connect() as conn:
        rows = conn.execute(
            sqlalchemy.text(
                f'SELECT {fields} FROM casbin_rule WHERE ptype = :ptype'),
            {'ptype': ptype})
        return sorted(tuple(row) for row in rows)


def _insert(engine, ptype, *rules):
    """Stores rules directly, as another process would."""
    fields = ['v0', 'v1', 'v2']
    with engine.begin() as conn:
        for rule in rules:
            names = fields[:len(rule)]
            conn.execute(
                sqlalchemy.text(
                    f'INSERT INTO casbin_rule (ptype, {", ".join(names)}) '
                    f'VALUES (:ptype, {", ".join(":" + n for n in names)})'), {
                        'ptype': ptype,
                        **dict(zip(names, rule))
                    })


def _add_user_row(engine, user_id):
    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.text('INSERT INTO users (id, name) '
                            'VALUES (:id, :id)'), {'id': user_id})


def _user_ids(engine):
    with engine.connect() as conn:
        return sorted(
            row[0]
            for row in conn.execute(sqlalchemy.text('SELECT id FROM users')))


class _PolicyWrites:
    """Counts the casbin_rule writes issued on an engine."""

    def __init__(self, engine):
        self.count = 0
        self._engine = engine

    def _before(self, conn, cursor, statement, *args):  # pylint: disable=unused-argument
        if ('casbin_rule' in statement and
                statement.lstrip().upper().startswith(
                    ('INSERT', 'DELETE', 'UPDATE'))):
            self.count += 1

    def __enter__(self):
        sqlalchemy.event.listen(self._engine, 'before_cursor_execute',
                                self._before)
        return self

    def __exit__(self, *args):
        sqlalchemy.event.remove(self._engine, 'before_cursor_execute',
                                self._before)


@pytest.mark.usefixtures("reset_permission_singleton", "cleanup_env_vars")
class TestPermissionService:
    """Test permission service functionality."""

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission._PolicyAdapter')
    @mock.patch('sky.users.permission.casbin.SyncedEnforcer')
    @mock.patch('sky.global_user_state.initialize_and_get_db')
    def test_permission_service_initialization(self, mock_init_db,
                                               mock_enforcer_class,
                                               mock_adapter_class,
                                               mock_policy_lock):
        """Test permission service initialization."""
        mock_engine = mock.Mock()
        mock_init_db.return_value = mock_engine
        mock_enforcer = mock.Mock()
        mock_enforcer_class.return_value = mock_enforcer
        # Nobody holds a role yet; read once at the end of the setup.
        mock_enforcer.get_grouping_policy.return_value = []
        mock_adapter = mock.Mock()
        mock_adapter_class.return_value = mock_adapter
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        service = permission.PermissionService()
        service._lazy_initialize()

        # Verify database was initialized
        mock_init_db.assert_called_once()

        # Verify SQLAlchemy adapter was created with the correct engine
        mock_adapter_class.assert_called_once_with(mock_engine)

        # Verify Casbin enforcer was created with correct model path
        args, kwargs = mock_enforcer_class.call_args
        assert args[0].endswith('model.conf')
        assert args[1] == mock_adapter

        # Verify the enforcer is stored
        assert service.enforcer == mock_enforcer

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_maybe_initialize_policies_new_setup(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users, policy_engine):
        """Test policy initialization for a new setup."""
        mock_get_plugin_rules.return_value = {}
        mock_get_role_perms.return_value = {
            'admin': {
                'permissions': {
                    'blocklist': []
                }
            },
            'user': {
                'permissions': {
                    'blocklist': [{
                        'path': '/workspaces/config',
                        'method': 'POST'
                    }]
                }
            }
        }
        mock_get_workspace_perms.return_value = {
            'default': ['*'],
            'private-ws': ['user1', 'user2']
        }
        mock_get_users.return_value = mock_users

        service = _service_over(policy_engine)
        service._maybe_initialize_policies()

        mock_get_plugin_rules.assert_called_once()
        # The user role's blocklist and the workspace grants.
        assert _stored(policy_engine, 'p') == sorted([
            ('user', '/workspaces/config', 'POST'),
            ('*', 'default', '*'),
            ('user1', 'private-ws', '*'),
            ('user2', 'private-ws', '*'),
        ])
        # Every user gets the default role; the built-in system users get
        # their own: SERVER_ID and the system user admin, the system viewer
        # viewer.
        assert _stored(policy_engine, 'g') == sorted(
            [(user.id, rbac.get_default_role()) for user in mock_users] + [
                (common.SERVER_ID, rbac.RoleName.ADMIN.value),
                (constants.SKYPILOT_SYSTEM_USER_ID, rbac.RoleName.ADMIN.value),
                (constants.SKYPILOT_SYSTEM_VIEWER_USER_ID,
                 rbac.RoleName.VIEWER.value),
            ])

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_maybe_initialize_policies_existing_policies(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users, policy_engine):
        """Test policy initialization when policies already exist (idempotent)."""
        existing_policies = [('user', '/workspaces/config', 'POST'),
                             ('*', 'default', '*'),
                             ('user1', 'private-ws', '*'),
                             ('user2', 'private-ws', '*')]
        _insert(policy_engine, 'p', *existing_policies)
        mock_get_plugin_rules.return_value = {}
        mock_get_role_perms.return_value = {
            'admin': {
                'permissions': {
                    'blocklist': []
                }
            },
            'user': {
                'permissions': {
                    'blocklist': [{
                        'path': '/workspaces/config',
                        'method': 'POST'
                    }]
                }
            }
        }
        mock_get_workspace_perms.return_value = {
            'default': ['*'],
            'private-ws': ['user1', 'user2']
        }
        mock_get_users.return_value = mock_users

        service = _service_over(policy_engine)
        service._maybe_initialize_policies()

        # Nothing added twice, nothing dropped.
        assert _stored(policy_engine, 'p') == sorted(existing_policies)

    def test_add_user_if_not_exists_new_user(self):
        """Test adding a new user that doesn't exist."""
        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = []  # No existing roles
        mock_enforcer.add_grouping_policy.return_value = True

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        result = service._add_user_if_not_exists_no_lock('new_user')

        assert result is True
        mock_enforcer.add_grouping_policy.assert_called_once_with(
            'new_user', rbac.get_default_role())

    def test_add_user_if_not_exists_existing_user(self):
        """Test adding a user that already exists."""
        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = [
            'user'
        ]  # User already has roles

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        result = service._add_user_if_not_exists_no_lock('existing_user')

        assert result is False
        mock_enforcer.add_grouping_policy.assert_not_called()

    @mock.patch('sky.users.permission._policy_lock')
    def test_update_role_new_role(self, mock_policy_lock, policy_engine):
        """Test updating user role to a new role."""
        _insert(policy_engine, 'g', ('user1', 'user'))
        service = _service_over(policy_engine)

        service.update_role('user1', 'admin')

        # The old role is dropped, not kept beside the new one.
        assert _stored(policy_engine, 'g') == [('user1', 'admin')]
        assert service.get_user_roles('user1') == ['admin']

    @mock.patch('sky.users.permission._policy_lock')
    def test_update_role_no_existing_roles(self, mock_policy_lock,
                                           policy_engine):
        """Test updating role for a user who has no existing roles."""
        service = _service_over(policy_engine)

        service.update_role('user1', 'user')

        assert _stored(policy_engine, 'g') == [('user1', 'user')]

    @mock.patch('sky.users.permission._policy_lock')
    def test_update_role_same_role(self, mock_policy_lock, policy_engine):
        """Test updating user role to the same role (no-op)."""
        _insert(policy_engine, 'g', ('user1', 'admin'))
        service = _service_over(policy_engine)

        with _PolicyWrites(policy_engine) as writes:
            service.update_role('user1', 'admin')

        assert writes.count == 0
        assert _stored(policy_engine, 'g') == [('user1', 'admin')]

    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    def test_update_role_invalidates_cache_after_the_write(
            self, mock_policy_lock, mock_kv_cache, policy_engine):
        """The cache must not be cleared before the new role is persisted.

        Clearing first opens a window where a reader repopulates it from the
        old role and the entry survives the update.
        """
        _insert(policy_engine, 'g', ('user1', 'user'))
        # What another process reads at the moment the cache is cleared.
        seen_at_invalidation = []
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.side_effect = (
            lambda *a, **kw: seen_at_invalidation.append(
                _stored(policy_engine, 'g')))
        service = _service_over(policy_engine)

        service.update_role('user1', 'admin')

        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_called_once()
        call_kwargs = (
            mock_kv_cache.delete_cache_entries_by_prefix_suffix.call_args[1])
        assert 'user1' in call_kwargs['suffix']
        assert seen_at_invalidation == [[('user1', 'admin')]]

    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    def test_update_role_no_existing_role_still_invalidates(
            self, mock_policy_lock, mock_kv_cache, policy_engine):
        """Test that update_role invalidates even when user had no prior role.

        A first role assignment can grant workspace access that was previously
        denied and cached as '0', so we must always invalidate.
        """
        service = _service_over(policy_engine)

        service.update_role('user1', 'user')

        assert _stored(policy_engine, 'g') == [('user1', 'user')]
        # Cache must be invalidated even for first role assignment
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_called_once()
        call_kwargs = (
            mock_kv_cache.delete_cache_entries_by_prefix_suffix.call_args[1])
        assert 'user1' in call_kwargs['suffix']

    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    def test_add_user_if_not_exists_invalidates_cache_after_the_write(
            self, mock_policy_lock, mock_kv_cache):
        """Seeding a role-less user clears their cached workspace denials.

        A user checked while role-less may have a cached '0' for a workspace
        that their new role grants; it must not outlive the seed.
        """
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        call_order = []
        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = []
        mock_enforcer.add_grouping_policy.side_effect = (
            lambda *a: call_order.append('add_grouping_policy'))
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.side_effect = (
            lambda *a, **kw: call_order.append('invalidate'))

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._load_policy_no_lock = mock.Mock()

        service.add_user_if_not_exists('user1')

        call_kwargs = (
            mock_kv_cache.delete_cache_entries_by_prefix_suffix.call_args[1])
        assert 'user1' in call_kwargs['suffix']
        assert call_order == ['add_grouping_policy', 'invalidate']

    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    def test_add_user_if_not_exists_survives_cache_clear_failure(
            self, mock_policy_lock, mock_kv_cache):
        """The role is saved even if clearing the cache then fails."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = []
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.side_effect = (
            RuntimeError('cache db unavailable'))

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._load_policy_no_lock = mock.Mock()

        service.add_user_if_not_exists('user1')

        mock_enforcer.add_grouping_policy.assert_called_once()

    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    def test_add_user_if_not_exists_keeps_cache_for_existing_role(
            self, mock_policy_lock, mock_kv_cache):
        """No role written, nothing to invalidate."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = ['user']

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._load_policy_no_lock = mock.Mock()

        service.add_user_if_not_exists('user1')

        mock_enforcer.add_grouping_policy.assert_not_called()
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_not_called()

    def test_get_user_roles(self):
        """Test getting user roles."""
        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = ['admin', 'user']

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._load_policy_no_lock = mock.Mock()

        roles = service.get_user_roles('user1')

        assert roles == ['admin', 'user']
        mock_enforcer.get_roles_for_user.assert_called_once_with('user1')

    def test_check_endpoint_permission(self):
        """Test checking endpoint permissions (admin/user blocklist path)."""
        mock_enforcer = mock.Mock()
        mock_enforcer.enforce.return_value = True
        # New dispatch reads roles first; return a non-viewer role so the
        # call falls through to the Casbin blocklist check.
        mock_enforcer.get_roles_for_user.return_value = ['user']

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        result = service.check_endpoint_permission('user1', '/api/test', 'GET')

        assert result is True
        mock_enforcer.enforce.assert_called_once_with('user1', '/api/test',
                                                      'GET')

    def test_check_endpoint_permission_viewer_allowed(self):
        """Viewer role: allowlist hit returns False (request not blocked)."""
        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = ['viewer']

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._viewer_allowlist = [('/status', 'POST')]

        result = service.check_endpoint_permission('user1', '/status', 'POST')

        assert result is False
        # When the viewer dispatch handles the request, the underlying
        # blocklist enforce() must NOT be called.
        mock_enforcer.enforce.assert_not_called()

    def test_check_endpoint_permission_viewer_denied(self):
        """Viewer role: no allowlist hit returns True (request blocked)."""
        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = ['viewer']

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._viewer_allowlist = [('/status', 'POST')]

        result = service.check_endpoint_permission('user1', '/launch', 'POST')

        assert result is True
        mock_enforcer.enforce.assert_not_called()

    def test_check_endpoint_permission_viewer_keymatch2(self):
        """Viewer allowlist uses Casbin keyMatch2 path patterns."""
        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = ['viewer']

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._viewer_allowlist = [
            ('/ssh_node_pools/:pool_name/status', 'GET'),
            ('/plugins/api/foo/*', 'POST'),
        ]

        # Placeholder match.
        assert service.check_endpoint_permission(
            'u', '/ssh_node_pools/myPool/status', 'GET') is False
        # Wildcard match.
        assert service.check_endpoint_permission('u', '/plugins/api/foo/bar',
                                                 'POST') is False
        # Method mismatch -> denied.
        assert service.check_endpoint_permission(
            'u', '/ssh_node_pools/myPool/status', 'POST') is True
        # Sibling path -> denied.
        assert service.check_endpoint_permission(
            'u', '/ssh_node_pools/myPool/keys', 'GET') is True

    def test_check_endpoint_permission_admin_wins_over_viewer(self):
        """A user with both admin and viewer roles uses admin semantics."""
        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = ['viewer', 'admin']
        # enforce returns False (allowed) for admin under blocklist semantics.
        mock_enforcer.enforce.return_value = False

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        # Empty allowlist would deny everything if viewer semantics applied.
        service._viewer_allowlist = []

        # /launch is not on any allowlist; viewer-only would block it,
        # but admin+viewer must fall through to enforce() -> allowed.
        assert service.check_endpoint_permission('u', '/launch',
                                                 'POST') is False
        mock_enforcer.enforce.assert_called_once_with('u', '/launch', 'POST')

    def test_check_workspace_permission_non_server(self):
        """Test workspace permission check when not on API server."""
        # Ensure ENV_VAR_IS_SKYPILOT_SERVER is not set
        if constants.ENV_VAR_IS_SKYPILOT_SERVER in os.environ:
            del os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER]

        service = permission.PermissionService()
        service._lazy_initialize()

        result = service.check_workspace_permission('user1', 'test-workspace')

        # Should always return True when not on API server
        assert result is True

    def test_check_workspace_permission_admin_user(self):
        """Test workspace permission check for admin user."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'

        mock_enforcer = mock.Mock()

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service.get_user_roles = mock.Mock(return_value=['admin'])

        result = service.check_workspace_permission('admin_user',
                                                    'test-workspace')

        # Admin should always have access
        assert result is True

    def test_check_workspace_permission_regular_user(self):
        """Test workspace permission check for regular user."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'

        mock_enforcer = mock.Mock()
        mock_enforcer.enforce.return_value = True

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service.get_user_roles = mock.Mock(return_value=['user'])

        result = service.check_workspace_permission('user1', 'test-workspace')

        assert result is True
        mock_enforcer.enforce.assert_called_once_with('user1', 'test-workspace',
                                                      '*')

    @mock.patch('sky.users.permission.kv_cache')
    def test_check_workspace_permission_cache_hit_allowed(self, mock_kv_cache):
        """Test cache hit returning allowed ('1') skips enforcer."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        mock_kv_cache.get_cache_entry.return_value = '1'

        mock_enforcer = mock.Mock()
        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        result = service.check_workspace_permission('user1', 'test-workspace')

        assert result is True
        # Enforcer should NOT be called on cache hit
        mock_enforcer.enforce.assert_not_called()

    @mock.patch('sky.users.permission.kv_cache')
    def test_check_workspace_permission_cache_hit_denied(self, mock_kv_cache):
        """Test cache hit returning denied ('0') skips enforcer."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        mock_kv_cache.get_cache_entry.return_value = '0'

        mock_enforcer = mock.Mock()
        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        result = service.check_workspace_permission('user1', 'test-workspace')

        assert result is False
        mock_enforcer.enforce.assert_not_called()

    @mock.patch('sky.users.permission.kv_cache')
    def test_check_workspace_permission_cache_write_failure(
            self, mock_kv_cache):
        """Test that permission is still returned when cache write fails."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        # Cache miss
        mock_kv_cache.get_cache_entry.return_value = None
        # Cache write raises
        mock_kv_cache.add_or_update_cache_entry.side_effect = RuntimeError(
            'db locked')

        mock_enforcer = mock.Mock()
        mock_enforcer.enforce.return_value = True

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service.get_user_roles = mock.Mock(return_value=['user'])

        # Should still return the correct result despite cache write failure
        result = service.check_workspace_permission('user1', 'test-workspace')

        assert result is True
        mock_enforcer.enforce.assert_called_once()

    @mock.patch('sky.users.permission._policy_lock')
    def test_add_workspace_policy(self, mock_policy_lock, policy_engine):
        """Test adding workspace policy."""
        service = _service_over(policy_engine)

        service.add_workspace_policy('test-workspace', ['user1', 'user2'])

        assert _stored(policy_engine, 'p') == [
            ('user1', 'test-workspace', '*'),
            ('user2', 'test-workspace', '*'),
        ]

    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    def test_add_workspace_policy_invalidates_cache(self, mock_policy_lock,
                                                    mock_kv_cache,
                                                    policy_engine):
        """Test that add_workspace_policy invalidates stale cached denials."""
        service = _service_over(policy_engine)

        service.add_workspace_policy('test-workspace', ['user1', 'user2'])

        # Verify cache invalidation was called
        mock_kv_cache.delete_cache_entries_by_prefix.assert_called_once()
        prefix_arg = mock_kv_cache.delete_cache_entries_by_prefix.call_args[0][
            0]
        assert 'test-workspace' in prefix_arg

    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    def test_update_workspace_policy_invalidates_cache(self, mock_policy_lock,
                                                       mock_kv_cache,
                                                       policy_engine):
        """Test that update_workspace_policy invalidates permission cache."""
        _insert(policy_engine, 'p', ('user0', 'test-workspace', '*'))
        service = _service_over(policy_engine)

        service.update_workspace_policy('test-workspace', ['user1', 'user2'])

        assert _stored(policy_engine, 'p') == [
            ('user1', 'test-workspace', '*'),
            ('user2', 'test-workspace', '*'),
        ]
        # Verify cache invalidation was called
        mock_kv_cache.delete_cache_entries_by_prefix.assert_called_once()
        prefix_arg = mock_kv_cache.delete_cache_entries_by_prefix.call_args[0][
            0]
        assert 'test-workspace' in prefix_arg

    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    def test_remove_workspace_policy_invalidates_cache(self, mock_policy_lock,
                                                       mock_kv_cache):
        """Test that remove_workspace_policy invalidates permission cache."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        mock_enforcer = mock.Mock()

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        service.remove_workspace_policy('test-workspace')

        # Verify enforcer policy removal
        mock_enforcer.remove_filtered_policy.assert_called_once_with(
            1, 'test-workspace')
        # Verify cache invalidation was called
        mock_kv_cache.delete_cache_entries_by_prefix.assert_called_once()
        prefix_arg = mock_kv_cache.delete_cache_entries_by_prefix.call_args[0][
            0]
        assert 'test-workspace' in prefix_arg

    @mock.patch('sky.users.permission.locks.get_lock')
    def test_policy_lock_context_manager(self, mock_get_lock):
        """Test the policy lock context manager."""
        mock_lock = mock.Mock()
        mock_lock.__enter__ = mock.Mock(return_value=mock.Mock())
        mock_lock.__exit__ = mock.Mock(return_value=None)
        mock_get_lock.return_value = mock_lock

        with permission._policy_lock():
            pass

        mock_get_lock.assert_called_once_with(
            permission.POLICY_UPDATE_LOCK_ID,
            permission.POLICY_UPDATE_LOCK_TIMEOUT_SECONDS,
            poll_interval=permission.POLICY_UPDATE_LOCK_POLL_INTERVAL_SECONDS)
        mock_lock.__enter__.assert_called_once()
        mock_lock.__exit__.assert_called_once()

    @mock.patch('sky.users.permission.locks.get_lock')
    def test_policy_lock_timeout_exception(self, mock_get_lock):
        """Test policy lock timeout exception handling."""
        mock_lock = mock.Mock()
        mock_lock.__enter__ = mock.Mock(
            side_effect=locks.LockTimeout('test_lock'))
        mock_lock.__exit__ = mock.Mock(return_value=None)
        mock_get_lock.return_value = mock_lock

        with pytest.raises(RuntimeError) as exc_info:
            with permission._policy_lock():
                pass

        assert 'Failed to update policy due to a timeout' in str(exc_info.value)
        assert 'casbin policy lock' in str(exc_info.value)

    @mock.patch('sky.users.permission.kv_cache')
    def test_delete_user_with_role(self, mock_kv_cache, policy_engine):
        """Test deleting a user who has a role."""
        _add_user_row(policy_engine, 'user1')
        _add_user_row(policy_engine, 'user2')
        _insert(policy_engine, 'g', ('user1', 'user'), ('user2', 'user'))
        service = _service_over(policy_engine)

        with mock.patch('sky.users.permission._policy_lock'):
            service.delete_user_and_record('user1')

        # The record and the role go; the other user's stay.
        assert _user_ids(policy_engine) == ['user2']
        assert _stored(policy_engine, 'g') == [('user2', 'user')]
        # Verify cache invalidation
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_called_once()
        call_kwargs = (
            mock_kv_cache.delete_cache_entries_by_prefix_suffix.call_args[1])
        assert 'user1' in call_kwargs['suffix']

    @mock.patch('sky.users.permission.kv_cache')
    def test_delete_user_without_role(self, mock_kv_cache, policy_engine):
        """Test deleting a user who has no roles."""
        _add_user_row(policy_engine, 'user2')
        service = _service_over(policy_engine)

        with mock.patch('sky.users.permission._policy_lock'):
            service.delete_user_and_record('user2')

        assert _user_ids(policy_engine) == []
        # No policy removed, so nothing to invalidate.
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_not_called()

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission._PolicyAdapter')
    @mock.patch('sky.users.permission.casbin.SyncedEnforcer')
    @mock.patch('sky.users.permission.db_utils.add_all_tables_to_db_sqlalchemy')
    @mock.patch('sky.global_user_state.initialize_and_get_db')
    def test_lazy_initialize_full_initialize_true(self, mock_init_db,
                                                  mock_add_tables,
                                                  mock_enforcer_class,
                                                  mock_adapter_class,
                                                  mock_policy_lock):
        """Test _lazy_initialize with full_initialize=True."""
        mock_engine = mock.Mock()
        mock_init_db.return_value = mock_engine
        mock_enforcer = mock.Mock()
        mock_enforcer_class.return_value = mock_enforcer
        # Nobody holds a role yet; read once at the end of the setup.
        mock_enforcer.get_grouping_policy.return_value = []
        mock_adapter = mock.Mock()
        mock_adapter_class.return_value = mock_adapter
        mock_context = mock.Mock()
        mock_context.__enter__ = mock.Mock(return_value=None)
        mock_context.__exit__ = mock.Mock(return_value=None)
        mock_policy_lock.return_value = mock_context

        with mock.patch.object(permission.PermissionService,
                               '_maybe_initialize_policies') as mock_init_policies, \
             mock.patch.object(permission.PermissionService,
                               '_maybe_initialize_basic_auth_user') as mock_init_basic_auth:
            service = permission.PermissionService()
            service._lazy_initialize(full_initialize=True)

            # Verify database tables were added
            mock_add_tables.assert_called_once()
            # Verify policies were initialized
            mock_init_policies.assert_called_once()
            # Verify basic auth user was initialized
            mock_init_basic_auth.assert_called_once()

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission._PolicyAdapter')
    @mock.patch('sky.users.permission.casbin.SyncedEnforcer')
    @mock.patch('sky.global_user_state.initialize_and_get_db')
    def test_lazy_initialize_full_initialize_false(self, mock_init_db,
                                                   mock_enforcer_class,
                                                   mock_adapter_class,
                                                   mock_policy_lock):
        """Test _lazy_initialize with full_initialize=False (default)."""
        mock_engine = mock.Mock()
        mock_init_db.return_value = mock_engine
        mock_enforcer = mock.Mock()
        mock_enforcer_class.return_value = mock_enforcer
        # Nobody holds a role yet; read once at the end of the setup.
        mock_enforcer.get_grouping_policy.return_value = []
        mock_adapter = mock.Mock()
        mock_adapter_class.return_value = mock_adapter
        mock_context = mock.Mock()
        mock_context.__enter__ = mock.Mock(return_value=None)
        mock_context.__exit__ = mock.Mock(return_value=None)
        mock_policy_lock.return_value = mock_context

        with mock.patch.object(permission.PermissionService,
                               '_maybe_initialize_policies') as mock_init_policies, \
             mock.patch.object(permission.PermissionService,
                               '_maybe_initialize_basic_auth_user') as mock_init_basic_auth, \
             mock.patch('sky.users.permission.db_utils.add_all_tables_to_db_sqlalchemy') as mock_add_tables:
            service = permission.PermissionService()
            service._lazy_initialize(full_initialize=False)

            # Verify database tables were NOT added
            mock_add_tables.assert_not_called()
            # Verify policies were NOT initialized
            mock_init_policies.assert_not_called()
            # Verify basic auth user was NOT initialized
            mock_init_basic_auth.assert_not_called()

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission._PolicyAdapter')
    @mock.patch('sky.users.permission.casbin.SyncedEnforcer')
    @mock.patch('sky.global_user_state.initialize_and_get_db')
    def test_lazy_initialize_singleton_behavior(self, mock_init_db,
                                                mock_enforcer_class,
                                                mock_adapter_class,
                                                mock_policy_lock):
        """Test that _lazy_initialize uses singleton pattern."""
        mock_engine = mock.Mock()
        mock_init_db.return_value = mock_engine
        mock_enforcer = mock.Mock()
        mock_enforcer_class.return_value = mock_enforcer
        # Nobody holds a role yet; read once at the end of the setup.
        mock_enforcer.get_grouping_policy.return_value = []
        mock_adapter = mock.Mock()
        mock_adapter_class.return_value = mock_adapter
        mock_context = mock.Mock()
        mock_context.__enter__ = mock.Mock(return_value=None)
        mock_context.__exit__ = mock.Mock(return_value=None)
        mock_policy_lock.return_value = mock_context

        # Create first service instance
        service1 = permission.PermissionService()
        service1._lazy_initialize()

        # Create second service instance
        service2 = permission.PermissionService()
        service2._lazy_initialize()

        # Both should share the same enforcer instance
        assert service1.enforcer is service2.enforcer
        assert service1.enforcer is mock_enforcer
        # Enforcer should only be created once
        assert mock_enforcer_class.call_count == 1

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.global_user_state.get_user')
    def test_maybe_initialize_basic_auth_user_new_user(self, mock_get_user,
                                                       mock_add_user,
                                                       mock_policy_lock):
        """Test basic auth user initialization for a new user."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # Set environment variable
        os.environ[constants.SKYPILOT_INITIAL_BASIC_AUTH] = 'admin:password123'

        # User doesn't exist yet
        mock_get_user.return_value = None

        mock_enforcer = mock.Mock()
        mock_enforcer.add_grouping_policy.return_value = True

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        service._maybe_initialize_basic_auth_user()

        # Verify user was created
        assert mock_add_user.called
        call_args = mock_add_user.call_args[0][0]
        assert call_args.name == 'admin'
        assert call_args.password == 'password123'
        # Verify admin role was assigned
        mock_enforcer.add_grouping_policy.assert_called_once()

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.global_user_state.get_user')
    def test_maybe_initialize_basic_auth_user_existing_user(
            self, mock_get_user, mock_policy_lock):
        """Test basic auth user initialization when user already exists."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # Set environment variable
        os.environ[constants.SKYPILOT_INITIAL_BASIC_AUTH] = 'admin:password123'

        # User already exists
        existing_user = models.User(id='user_hash',
                                    name='admin',
                                    password='password123')
        mock_get_user.return_value = existing_user

        mock_enforcer = mock.Mock()

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        service._maybe_initialize_basic_auth_user()

        # Verify no new user was created and no role was assigned
        mock_enforcer.add_grouping_policy.assert_not_called()

    @mock.patch('sky.users.permission._policy_lock')
    def test_maybe_initialize_basic_auth_user_no_env_var(
            self, mock_policy_lock):
        """Test basic auth user initialization when env var is not set."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # Ensure env var is not set
        if constants.SKYPILOT_INITIAL_BASIC_AUTH in os.environ:
            del os.environ[constants.SKYPILOT_INITIAL_BASIC_AUTH]

        mock_enforcer = mock.Mock()

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        service._maybe_initialize_basic_auth_user()

        # Verify nothing was called
        mock_enforcer.add_grouping_policy.assert_not_called()

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_maybe_initialize_policies_with_plugin_rules(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users, policy_engine):
        """Test policy initialization with plugin RBAC rules."""
        mock_get_plugin_rules.return_value = {
            'user': [{
                'path': '/plugins/api/test',
                'method': 'POST'
            }, {
                'path': '/plugins/api/test',
                'method': 'DELETE'
            }]
        }
        # Mock role permissions (should include plugin rules)
        mock_get_role_perms.return_value = {
            'user': {
                'permissions': {
                    'blocklist': [{
                        'path': '/workspaces/config',
                        'method': 'POST'
                    }, {
                        'path': '/plugins/api/test',
                        'method': 'POST'
                    }, {
                        'path': '/plugins/api/test',
                        'method': 'DELETE'
                    }]
                }
            }
        }
        mock_get_workspace_perms.return_value = {'default': ['*']}
        mock_get_users.return_value = mock_users

        service = _service_over(policy_engine)
        service._maybe_initialize_policies()

        mock_get_plugin_rules.assert_called_once()
        stored = _stored(policy_engine, 'p')
        assert ('user', '/workspaces/config', 'POST') in stored
        assert ('user', '/plugins/api/test', 'POST') in stored
        assert ('user', '/plugins/api/test', 'DELETE') in stored

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_maybe_initialize_policies_plugin_rules_import_error(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users, policy_engine):
        """Test policy initialization when plugin module is not available."""
        # Simulate ImportError - return empty dict (as the actual method would)
        mock_get_plugin_rules.return_value = {}
        mock_get_role_perms.return_value = {
            'user': {
                'permissions': {
                    'blocklist': [{
                        'path': '/workspaces/config',
                        'method': 'POST'
                    }]
                }
            }
        }
        mock_get_workspace_perms.return_value = {'default': ['*']}
        mock_get_users.return_value = mock_users

        service = _service_over(policy_engine)
        # Should not raise an exception, should handle gracefully
        service._maybe_initialize_policies()

        mock_get_plugin_rules.assert_called_once()
        # Verify policies were still initialized correctly
        assert ('user', '/workspaces/config',
                'POST') in _stored(policy_engine, 'p')

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_maybe_initialize_policies_writes_nothing_when_in_sync(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users, policy_engine):
        """A boot that finds the policy already correct must not write."""
        _insert(policy_engine, 'p', ('user', '/workspaces/config', 'POST'),
                ('*', 'default', '*'))
        _insert(policy_engine, 'g', ('user1', 'user'), ('user2', 'user'),
                ('user3', 'user'), (common.SERVER_ID, 'admin'),
                (constants.SKYPILOT_SYSTEM_USER_ID, 'admin'),
                (constants.SKYPILOT_SYSTEM_VIEWER_USER_ID, 'viewer'))
        mock_get_plugin_rules.return_value = {}
        mock_get_role_perms.return_value = {
            'user': {
                'permissions': {
                    'blocklist': [{
                        'path': '/workspaces/config',
                        'method': 'POST'
                    }]
                }
            }
        }
        mock_get_workspace_perms.return_value = {'default': ['*']}
        mock_get_users.return_value = mock_users

        service = _service_over(policy_engine)
        with _PolicyWrites(policy_engine) as writes:
            service._maybe_initialize_policies()

        assert writes.count == 0

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_maybe_initialize_policies_remove_redundant(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users, policy_engine):
        """Test policy initialization removes redundant policies."""
        _insert(policy_engine, 'p', ('user', '/workspaces/config', 'POST'),
                ('*', 'default', '*'),
                ('user-to-be-removed', 'some-workspace', '*'))
        mock_get_plugin_rules.return_value = {}
        mock_get_role_perms.return_value = {
            'user': {
                'permissions': {
                    'blocklist': [{
                        'path': '/workspaces/config',
                        'method': 'POST'
                    }]
                }
            }
        }
        mock_get_workspace_perms.return_value = {'default': ['*']}
        mock_get_users.return_value = mock_users

        service = _service_over(policy_engine)
        service._maybe_initialize_policies()

        assert _stored(policy_engine, 'p') == sorted([
            ('user', '/workspaces/config', 'POST'),
            ('*', 'default', '*'),
        ])

    def test_cache_key_format(self):
        """Test that cache keys use the expected prefix and separator."""
        service = permission.PermissionService()
        service.enforcer = mock.Mock()

        key = service._workspace_perm_cache_key('my-workspace', 'user123')
        # Only the action-agnostic member result is cached, so the key has no
        # action dimension: perm:ws:<workspace>:<user>. It keeps the
        # by-workspace prefix and by-user suffix so invalidation still matches.
        assert key == 'perm:ws:my-workspace:user123'
        assert key.startswith('perm:ws:my-workspace:')  # prefix invalidation
        assert key.endswith(':user123')  # suffix invalidation

    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    def test_update_role_same_role_no_invalidation(self, mock_policy_lock,
                                                   mock_kv_cache,
                                                   policy_engine):
        """Test that update_role does not invalidate when role is unchanged."""
        _insert(policy_engine, 'g', ('user1', 'admin'))
        service = _service_over(policy_engine)

        service.update_role('user1', 'admin')

        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_not_called()

    def test_sa_token_permission_viewer_denied_for_own(self):
        """Viewers cannot manage even their own SA tokens."""
        service = permission.PermissionService()
        service.get_user_roles = mock.Mock(return_value=['viewer'])

        # Viewer attempting to delete a token they own:
        assert service.check_service_account_token_permission(
            'viewer-bob', 'viewer-bob', 'delete') is False

    def test_sa_token_permission_user_can_manage_own(self):
        """Regular user can still manage their own SA tokens."""
        service = permission.PermissionService()
        service.get_user_roles = mock.Mock(return_value=['user'])

        assert service.check_service_account_token_permission(
            'alice', 'alice', 'delete') is True

    def test_sa_token_permission_admin_can_manage_others(self):
        """Admins can manage anyone's tokens."""
        service = permission.PermissionService()
        service.get_user_roles = mock.Mock(return_value=['admin'])

        assert service.check_service_account_token_permission(
            'eve', 'someone-else', 'delete') is True

    def test_sa_token_permission_admin_wins_over_viewer(self):
        """A user holding both admin and viewer can manage any token."""
        service = permission.PermissionService()
        service.get_user_roles = mock.Mock(return_value=['admin', 'viewer'])

        # Without admin precedence, the viewer rule would deny this.
        assert service.check_service_account_token_permission(
            'dual', 'someone-else', 'delete') is True


@pytest.mark.usefixtures("reset_permission_singleton", "cleanup_env_vars")
class TestPermissionServiceMultiProcess:
    """Test permission service behavior in multi-process scenarios."""

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_concurrent_initialization_no_duplicate_policies(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users, policy_engine):
        """Test that concurrent initialization doesn't create duplicate policies."""
        # Callers hold `_policy_lock()`; a thread lock stands in for it.
        thread_lock = threading.Lock()
        mock_policy_lock.return_value = thread_lock
        mock_get_plugin_rules.return_value = {}
        mock_get_role_perms.return_value = {
            'user': {
                'permissions': {
                    'blocklist': [{
                        'path': '/workspaces/config',
                        'method': 'POST'
                    }]
                }
            }
        }
        mock_get_workspace_perms.return_value = {
            'workspace1': ['user1', 'user2']
        }
        mock_get_users.return_value = mock_users

        errors = []

        def create_service():
            # Each thread is its own server process, with its own enforcer.
            try:
                service = _service_over(policy_engine)
                with permission._policy_lock():
                    service._maybe_initialize_policies()
            except Exception as e:  # pylint: disable=broad-except
                errors.append(e)

        threads = [threading.Thread(target=create_service) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors, f'Errors during concurrent initialization: {errors}'
        # Each rule stored exactly once, however many processes initialized.
        assert _stored(policy_engine, 'p') == sorted([
            ('user', '/workspaces/config', 'POST'),
            ('user1', 'workspace1', '*'),
            ('user2', 'workspace1', '*'),
        ])
        # One role per user, plus the three built-in system users: SERVER_ID
        # and the system admin user with admin, the system viewer with viewer.
        assert _stored(policy_engine, 'g') == sorted([
            ('user1', rbac.get_default_role()),
            ('user2', rbac.get_default_role()),
            ('user3', rbac.get_default_role()),
            (common.SERVER_ID, 'admin'),
            (constants.SKYPILOT_SYSTEM_USER_ID, 'admin'),
            (constants.SKYPILOT_SYSTEM_VIEWER_USER_ID, 'viewer'),
        ])

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_idempotent_user_addition(self, mock_get_users, mock_get_role_perms,
                                      mock_get_workspace_perms,
                                      mock_get_plugin_rules, mock_policy_lock,
                                      mock_users, policy_engine):
        """Test that adding the same user multiple times is idempotent."""
        mock_get_plugin_rules.return_value = {}
        mock_get_role_perms.return_value = {
            'user': {
                'permissions': {
                    'blocklist': []
                }
            }
        }
        mock_get_workspace_perms.return_value = {}
        mock_get_users.return_value = mock_users

        service = _service_over(policy_engine)

        # Initialize policies multiple times
        service._maybe_initialize_policies()
        service._maybe_initialize_policies()
        service._maybe_initialize_policies()

        # Each user holds exactly one role (3 mock users + 3 system users).
        expected = [(user.id, rbac.get_default_role()) for user in mock_users]
        expected += [(common.SERVER_ID, 'admin'),
                     (constants.SKYPILOT_SYSTEM_USER_ID, 'admin'),
                     (constants.SKYPILOT_SYSTEM_VIEWER_USER_ID, 'viewer')]
        assert _stored(policy_engine, 'g') == sorted(expected)

    @mock.patch('sky.users.permission._policy_lock')
    def test_concurrent_policy_updates_use_lock(self, mock_policy_lock,
                                                policy_engine):
        """Test that concurrent policy updates properly use locks."""
        lock_calls = []

        # Create a context manager that tracks calls
        class MockContextManager:

            def __enter__(self):
                lock_calls.append('enter')
                return mock.Mock()

            def __exit__(self, *args):
                lock_calls.append('exit')
                return None

        mock_policy_lock.return_value = MockContextManager()

        service = _service_over(policy_engine)

        # Test a single workspace policy update to verify lock is used
        service.add_workspace_policy('test-workspace', ['user1'])

        # Verify that lock was acquired and released
        assert 'enter' in lock_calls
        assert 'exit' in lock_calls
        assert len([call for call in lock_calls if call == 'enter']) == 1
        assert len([call for call in lock_calls if call == 'exit']) == 1

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch(
        'sky.users.permission.PermissionService._maybe_initialize_policies')
    @mock.patch(
        'sky.users.permission.PermissionService._maybe_initialize_basic_auth_user'
    )
    @mock.patch('sky.users.permission.casbin.SyncedEnforcer')
    @mock.patch('sky.users.permission._PolicyAdapter')
    @mock.patch('sky.users.permission.db_utils.add_all_tables_to_db_sqlalchemy')
    @mock.patch('sky.global_user_state.initialize_and_get_db')
    def test_lazy_initialize_db_failure_recovery(
            self, mock_init_db, mock_add_tables, mock_adapter_class,
            mock_enforcer_class, mock_init_basic_auth, mock_init_policies,
            mock_policy_lock):
        """Test that _lazy_initialize doesn't permanently set
        enforcer to None after DB failure on retry."""
        # Configure context manager to allow exceptions to propagate
        mock_context = mock.Mock()
        mock_context.__enter__ = mock.Mock(return_value=None)
        mock_context.__exit__ = mock.Mock(
            return_value=False)  # Don't suppress exceptions
        mock_policy_lock.return_value = mock_context

        mock_engine = mock.Mock()
        mock_init_db.return_value = mock_engine

        mock_adapter = mock.Mock()
        mock_adapter_class.return_value = mock_adapter

        mock_enforcer = mock.Mock()
        mock_enforcer_class.return_value = mock_enforcer
        # Nobody holds a role yet; read once at the end of the setup.
        mock_enforcer.get_grouping_policy.return_value = []

        # First call: add_all_tables_to_db_sqlalchemy raises an error
        # Second call: it succeeds
        mock_add_tables.side_effect = [
            Exception("Database connection failed"),
            None  # Success on second call
        ]

        service = permission.PermissionService()

        # First call with full_initialize=True - should raise an exception
        with pytest.raises(Exception) as exc_info:
            service._lazy_initialize(full_initialize=True)
        assert "Database connection failed" in str(exc_info.value)

        # Second call with full_initialize=True - should succeed
        service._lazy_initialize(full_initialize=True)

        # Verify that self.enforcer is not None after successful initialization
        assert service.enforcer is not None
        assert service.enforcer == mock_enforcer
        # Verify that full_initialize calls were made
        mock_add_tables.assert_called()
        mock_init_policies.assert_called()
        mock_init_basic_auth.assert_called()


class TestSeedNewUserRole:
    """`seed_new_user_role` reloads config before seeding the default role."""

    def test_reloads_config_before_add(self, monkeypatch):
        # Order matters: the reload must happen before `add_user_if_not_exists`
        # resolves `rbac.default_role` and before
        # `resync_workspace_policies_for_new_user` reads the `workspaces`
        # config, so runtime changes to either are honored. Reordering or
        # dropping the reload makes this fail.
        calls = []
        monkeypatch.setattr(permission.skypilot_config, 'safe_reload_config',
                            lambda: calls.append('reload'))
        monkeypatch.setattr(
            permission.permission_service,
            'add_user_if_not_exists',
            lambda user_id, role=None: calls.append(f'add:{user_id}:{role}'))
        monkeypatch.setattr(permission.permission_service,
                            'resync_workspace_policies_for_new_user',
                            lambda user_id: calls.append(f'resync:{user_id}'))

        permission.seed_new_user_role('u1')

        assert calls == ['reload', 'add:u1:None', 'resync:u1']

        # An explicit role overrides the default and is forwarded as-is.
        calls.clear()
        permission.seed_new_user_role('u2', role='viewer')
        assert calls == ['reload', 'add:u2:viewer', 'resync:u2']

    def test_resync_failure_does_not_fail_seed(self, monkeypatch):
        # A transient re-sync failure (lock timeout, DB error) must not fail
        # the user's first request: the grant is retried on the
        # zero-accessible path in `resolve_workspace_for_user`. The role
        # seeding itself must have completed by then.
        calls = []
        monkeypatch.setattr(permission.skypilot_config, 'safe_reload_config',
                            lambda: calls.append('reload'))
        monkeypatch.setattr(
            permission.permission_service,
            'add_user_if_not_exists',
            lambda user_id, role=None: calls.append(f'add:{user_id}:{role}'))

        def _boom(user_id):
            raise RuntimeError('lock timeout')

        monkeypatch.setattr(permission.permission_service,
                            'resync_workspace_policies_for_new_user', _boom)

        permission.seed_new_user_role('u1')  # Must not raise.

        assert calls == ['reload', 'add:u1:None']


@pytest.mark.usefixtures("cleanup_env_vars")
class TestResyncWorkspacePoliciesForNewUser:
    """Grant a newly-created user any workspace access owed by config.

    Covers the case where an admin adds a user to a private workspace's
    ``allowed_users`` before that user has logged in: the user record does
    not exist yet, so the startup / config-update sync drops the entry.
    When the user record is created, this re-sync must add the policy.
    """

    @pytest.fixture(autouse=True)
    def _engine(self, policy_engine):
        self.engine = policy_engine

    def _make_service(self):
        return _service_over(self.engine)

    def _grants(self):
        return _stored(self.engine, 'p')

    @mock.patch('sky.users.permission.skypilot_config.reload_config')
    @mock.patch('sky.users.permission.skypilot_config.get_skypilot_config_lock')
    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.skypilot_config.get_nested')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_new_user_matched_by_username_gets_policy(
            self, mock_get_users, mock_get_nested, mock_policy_lock,
            mock_kv_cache, mock_config_lock, mock_reload):
        """A pre-login allowed_users entry (username) grants access now."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # The user record now exists (created on first login).
        mock_get_users.return_value = [
            models.User(id='u-alice', name='alice@corp.com')
        ]
        # Config lists the user by username in a private workspace.
        mock_get_nested.return_value = {
            'default': {},
            'private-ws': {
                'private': True,
                'allowed_users': ['alice@corp.com'],
            },
        }

        service = self._make_service()
        service.resync_workspace_policies_for_new_user('u-alice')

        mock_config_lock.assert_called_once()
        mock_reload.assert_called_once()
        assert self._grants() == [('u-alice', 'private-ws', '*')]
        # The user's cached (stale) denial must be invalidated.
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_called_once()
        call_kwargs = (
            mock_kv_cache.delete_cache_entries_by_prefix_suffix.call_args[1])
        assert 'u-alice' in call_kwargs['suffix']

    @mock.patch('sky.users.permission.skypilot_config.reload_config')
    @mock.patch('sky.users.permission.skypilot_config.get_skypilot_config_lock')
    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.skypilot_config.get_nested')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_new_user_matched_by_user_id_gets_policy(
            self, mock_get_users, mock_get_nested, mock_policy_lock,
            mock_kv_cache, mock_config_lock, mock_reload):
        """A pre-login allowed_users entry (user_id) grants access now."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        mock_get_users.return_value = [
            models.User(id='u-alice', name='alice@corp.com')
        ]
        mock_get_nested.return_value = {
            'private-ws': {
                'private': True,
                'allowed_users': ['u-alice'],
            },
        }

        service = self._make_service()
        service.resync_workspace_policies_for_new_user('u-alice')

        mock_config_lock.assert_called_once()
        mock_reload.assert_called_once()
        assert self._grants() == [('u-alice', 'private-ws', '*')]

    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.skypilot_config.get_nested')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_user_not_in_any_allowed_users_gets_no_policy(
            self, mock_get_users, mock_get_nested, mock_policy_lock,
            mock_kv_cache):
        """A user listed in no workspace gets no policy and no save/invalidate."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        mock_get_users.return_value = [
            models.User(id='u-bob', name='bob@corp.com'),
            models.User(id='u-alice', name='alice@corp.com'),
        ]
        mock_get_nested.return_value = {
            'private-ws': {
                'private': True,
                'allowed_users': ['alice@corp.com'],
            },
        }

        service = self._make_service()
        service.resync_workspace_policies_for_new_user('u-bob')

        assert self._grants() == []
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_not_called()

    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.skypilot_config.get_nested')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_public_workspace_is_skipped(self, mock_get_users, mock_get_nested,
                                         mock_policy_lock, mock_kv_cache):
        """Only private workspaces are re-synced; public ones are ignored."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        mock_get_users.return_value = [
            models.User(id='u-alice', name='alice@corp.com')
        ]
        # A non-private workspace should not be considered even if it somehow
        # carries an allowed_users list.
        mock_get_nested.return_value = {
            'public-ws': {
                'allowed_users': ['alice@corp.com'],
            },
        }

        service = self._make_service()
        service.resync_workspace_policies_for_new_user('u-alice')

        assert self._grants() == []

    @mock.patch('sky.users.permission.skypilot_config.reload_config')
    @mock.patch('sky.users.permission.skypilot_config.get_skypilot_config_lock')
    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.skypilot_config.get_nested')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_idempotent_when_policy_already_exists(
            self, mock_get_users, mock_get_nested, mock_policy_lock,
            mock_kv_cache, mock_config_lock, mock_reload):
        """If the policy already exists, no save or cache invalidation."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        mock_get_users.return_value = [
            models.User(id='u-alice', name='alice@corp.com')
        ]
        mock_get_nested.return_value = {
            'private-ws': {
                'private': True,
                'allowed_users': ['alice@corp.com'],
            },
        }
        # The grant is already stored.
        _insert(self.engine, 'p', ('u-alice', 'private-ws', '*'))

        service = self._make_service()
        service.resync_workspace_policies_for_new_user('u-alice')

        mock_config_lock.assert_called_once()
        mock_reload.assert_called_once()
        assert self._grants() == [('u-alice', 'private-ws', '*')]
        # Nothing changed, so no invalidation.
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_not_called()

    @mock.patch('sky.users.permission.skypilot_config.reload_config')
    @mock.patch('sky.users.permission.skypilot_config.get_skypilot_config_lock')
    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.skypilot_config.get_nested')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_no_grant_when_user_removed_before_lock(
            self, mock_get_users, mock_get_nested, mock_policy_lock,
            mock_kv_cache, mock_config_lock, mock_reload):
        """An admin removal that lands before the lock must win.

        The pre-lock match is only an early-exit optimization; matches are
        recomputed from a fresh config read under the policy lock. If an
        admin removes the user from ``allowed_users`` between the two reads
        (their update runs under the same lock), the re-sync must not
        re-grant the revoked access.
        """
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        mock_get_users.return_value = [
            models.User(id='u-alice', name='alice@corp.com')
        ]
        # First (pre-lock) read still lists the user; the post-lock re-read
        # reflects the admin's concurrent removal.
        mock_get_nested.side_effect = [
            {
                'private-ws': {
                    'private': True,
                    'allowed_users': ['alice@corp.com'],
                },
            },
            {
                'private-ws': {
                    'private': True,
                    'allowed_users': [],
                },
            },
        ]

        service = self._make_service()
        service.resync_workspace_policies_for_new_user('u-alice')

        mock_config_lock.assert_called_once()
        mock_reload.assert_called_once()
        assert mock_get_nested.call_count == 2
        assert self._grants() == []
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_not_called()

    @mock.patch('sky.users.permission.skypilot_config.reload_config')
    @mock.patch('sky.users.permission.skypilot_config.get_skypilot_config_lock')
    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.skypilot_config.get_nested')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_config_lock_timeout_skips_grant(self, mock_get_users,
                                             mock_get_nested, mock_policy_lock,
                                             mock_kv_cache, mock_config_lock,
                                             mock_reload):
        """A config-lock timeout skips the grant instead of using stale
        config (the zero-accessible resolver retry picks it up later)."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        mock_config_lock.return_value.__enter__ = mock.Mock(
            side_effect=locks.LockTimeout('busy'))

        mock_get_users.return_value = [
            models.User(id='u-alice', name='alice@corp.com')
        ]
        mock_get_nested.return_value = {
            'private-ws': {
                'private': True,
                'allowed_users': ['alice@corp.com'],
            },
        }

        service = self._make_service()
        service.resync_workspace_policies_for_new_user('u-alice')  # No raise.

        mock_policy_lock.assert_not_called()
        mock_reload.assert_not_called()
        assert self._grants() == []
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_not_called()

    @mock.patch('sky.users.permission.skypilot_config.reload_config')
    @mock.patch('sky.users.permission.skypilot_config.get_skypilot_config_lock')
    @mock.patch('sky.users.permission.kv_cache')
    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.skypilot_config.get_nested')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_config_lock_acquired_before_policy_lock(
            self, mock_get_users, mock_get_nested, mock_policy_lock,
            mock_kv_cache, mock_config_lock, mock_reload):
        """Lock order must match update_workspace: config lock, THEN policy
        lock — the reverse order deadlocks against a concurrent workspace
        update (AB-BA)."""
        os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        order = []
        mock_config_lock.return_value.__enter__ = mock.Mock(
            side_effect=lambda *a: order.append('config'))
        mock_config_lock.return_value.__exit__ = mock.Mock(return_value=False)
        mock_policy_lock.return_value.__enter__ = mock.Mock(
            side_effect=lambda *a: order.append('policy'))
        mock_policy_lock.return_value.__exit__ = mock.Mock(return_value=False)

        mock_get_users.return_value = [
            models.User(id='u-alice', name='alice@corp.com')
        ]
        mock_get_nested.return_value = {
            'private-ws': {
                'private': True,
                'allowed_users': ['alice@corp.com'],
            },
        }

        service = self._make_service()
        service.resync_workspace_policies_for_new_user('u-alice')

        assert order == ['config', 'policy']
        mock_reload.assert_called_once()
        mock_kv_cache.delete_cache_entries_by_prefix_suffix.assert_called_once()

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.skypilot_config.get_nested')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_no_op_when_not_on_server(self, mock_get_users, mock_get_nested,
                                      mock_policy_lock):
        """Off the API server, the re-sync is a no-op."""
        if constants.ENV_VAR_IS_SKYPILOT_SERVER in os.environ:
            del os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER]

        service = self._make_service()
        service.resync_workspace_policies_for_new_user('u-alice')

        mock_get_nested.assert_not_called()
        mock_get_users.assert_not_called()
        mock_policy_lock.assert_not_called()
        assert self._grants() == []


class TestWorkspaceReadOnlyAccess:
    """Read-only workspace visibility: read vs write action semantics.

    Read-only visibility is NOT a materialized casbin grant; it is evaluated
    live from config (``workspaces_utils.is_read_only_workspace`` / the effective
    ``read_access``) at check time. A read check accepts it; a write
    check never does. The casbin enforce only ever checks the member '*' grant.
    """

    @pytest.fixture(autouse=True)
    def _server_mode(self, monkeypatch):
        """These checks are server-side, so the variable has to be set.

        Set here rather than per-test because assigning to os.environ
        directly leaks it for the life of the process: a later test that
        expects the client's permissive schema then gets the server's
        strict one, and which test that is depends on how the suite
        shards.
        """
        monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, 'true')

    def _service(self, member=False, roles=('user',)):
        service = permission.PermissionService()
        mock_enforcer = mock.Mock()
        # The only casbin check performed is the member '*' grant.
        mock_enforcer.enforce.side_effect = lambda u, w, act: (member and act ==
                                                               '*')
        service.enforcer = mock_enforcer
        service.get_user_roles = mock.Mock(return_value=list(roles))
        return service, mock_enforcer

    @mock.patch('sky.workspaces.utils.is_read_only_workspace')
    @mock.patch('sky.users.permission.kv_cache')
    def test_read_allows_live_readonly_workspace(self, mock_kv_cache,
                                                 mock_is_ro):
        mock_kv_cache.get_cache_entry.return_value = None
        mock_is_ro.return_value = True  # config says ws is read-only
        service, _ = self._service(member=False)  # non-member
        assert service.check_workspace_permission('u1', 'ws-ro',
                                                  action='read') is True

    @mock.patch('sky.workspaces.utils.is_read_only_workspace')
    @mock.patch('sky.users.permission.kv_cache')
    def test_read_denies_non_readonly_workspace(self, mock_kv_cache,
                                                mock_is_ro):
        mock_kv_cache.get_cache_entry.return_value = None
        mock_is_ro.return_value = False  # config says not read-only
        service, _ = self._service(member=False)
        assert service.check_workspace_permission(
            'u1', 'ws-priv', action='read') is False

    @mock.patch('sky.workspaces.utils.is_read_only_workspace')
    @mock.patch('sky.users.permission.kv_cache')
    def test_write_ignores_readonly(self, mock_kv_cache, mock_is_ro):
        mock_kv_cache.get_cache_entry.return_value = None
        mock_is_ro.return_value = True  # even though ws is read-only
        service, _ = self._service(member=False)
        # A non-member write is denied regardless of read-only visibility...
        assert service.check_workspace_permission('u1', 'ws-ro',
                                                  action='write') is False
        # ...and the read-only config is not even consulted for a write.
        mock_is_ro.assert_not_called()

    @mock.patch('sky.workspaces.utils.is_read_only_workspace')
    @mock.patch('sky.users.permission.kv_cache')
    def test_member_allowed_without_consulting_readonly(self, mock_kv_cache,
                                                        mock_is_ro):
        mock_kv_cache.get_cache_entry.return_value = None
        service, _ = self._service(member=True)  # member
        assert service.check_workspace_permission('u1', 'ws', 'read') is True
        assert service.check_workspace_permission('u1', 'ws', 'write') is True
        # A member short-circuits before the read-only branch.
        mock_is_ro.assert_not_called()

    @mock.patch('sky.workspaces.utils.is_read_only_workspace')
    @mock.patch('sky.users.permission.kv_cache')
    def test_member_cache_key_is_action_agnostic(self, mock_kv_cache,
                                                 mock_is_ro):
        mock_kv_cache.get_cache_entry.return_value = None
        mock_is_ro.return_value = True
        service, _ = self._service(member=False)
        service.check_workspace_permission('u1', 'ws-ro', action='read')
        service.check_workspace_permission('u1', 'ws-ro', action='write')
        keys = [
            c.args[0]
            for c in mock_kv_cache.add_or_update_cache_entry.call_args_list
        ]
        # Only the (action-agnostic) member result is cached, under one key.
        assert keys, 'member result should be cached'
        assert len(set(keys)) == 1
        assert all(':read:' not in k and ':write:' not in k for k in keys)

    @mock.patch('sky.workspaces.utils.get_read_only_workspace_names')
    def test_get_accessible_read_includes_live_readonly_write_excludes(
            self, mock_ro_names):
        service, enf = self._service()
        enf.get_policy.return_value = [
            ('u1', 'ws-mine', '*'),
            ('*', 'ws-pub', '*'),
        ]
        # Read-only set comes live from config, not from a casbin 'read' rule.
        mock_ro_names.return_value = {'ws-ro'}
        names = {'ws-mine', 'ws-ro', 'ws-pub', 'ws-other'}
        assert service.get_accessible_workspace_names(
            'u1', names, action='read') == {'ws-mine', 'ws-ro', 'ws-pub'}
        # Write excludes the read-only workspace (and never consults config).
        assert service.get_accessible_workspace_names(
            'u1', names, action='write') == {'ws-mine', 'ws-pub'}

    @mock.patch('sky.workspaces.utils.get_read_only_workspace_names')
    def test_get_workspace_access_sets_single_scan(self, mock_ro_names):
        # get_workspace_access_sets returns (readable, writable) in one policy
        # scan: readable = member/open + read-only-visible, writable = member/
        # open only. Used by the hot GET /workspaces path.
        service, enf = self._service()
        enf.get_policy.return_value = [
            ('u1', 'ws-mine', '*'),
            ('*', 'ws-pub', '*'),
        ]
        mock_ro_names.return_value = {'ws-ro'}
        names = {'ws-mine', 'ws-ro', 'ws-pub', 'ws-other'}
        readable, writable = service.get_workspace_access_sets('u1', names)
        assert readable == {'ws-mine', 'ws-ro', 'ws-pub'}
        assert writable == {'ws-mine', 'ws-pub'}
        # A single scan of the policy backs both sets.
        assert enf.get_policy.call_count == 1

    @mock.patch('sky.workspaces.utils.get_read_only_workspace_names')
    def test_get_readable_workspace_names(self, mock_ro_names, monkeypatch):
        service, enf = self._service()
        enf.get_policy.return_value = [
            ('u1', 'ws-mine', '*'),
            ('*', 'ws-pub', '*'),
        ]
        mock_ro_names.return_value = {'ws-ro'}
        names = {'ws-mine', 'ws-ro', 'ws-pub', 'ws-other'}
        assert service.get_readable_workspace_names(
            'u1', names) == {'ws-mine', 'ws-ro', 'ws-pub'}
        # Unrestricted is None, not every requested workspace.
        service.get_user_roles.return_value = ['admin']
        assert service.get_readable_workspace_names('u1', names) is None
        monkeypatch.delenv(constants.ENV_VAR_IS_SKYPILOT_SERVER)
        service.get_user_roles.return_value = ['user']
        assert service.get_readable_workspace_names('u1', names) is None

    def test_add_workspace_policy_only_member_grants(self, policy_engine):
        service = _service_over(policy_engine)
        with mock.patch('sky.users.permission._policy_lock'), \
             mock.patch('sky.users.permission.kv_cache'):
            service.add_workspace_policy('ws', ['u1', 'u2'])
        # Only the member grants; no materialized read-only wildcard.
        assert _stored(policy_engine, 'p') == [('u1', 'ws', '*'),
                                               ('u2', 'ws', '*')]


@pytest.fixture
def two_workers(policy_engine):
    """Two PermissionServices over one database, as two API-server workers.

    Each uvicorn worker holds its own in-memory casbin model, so a write by one
    is invisible to the other until it reloads. Two enforcers over one SQLite
    file reproduce that exactly, without needing two processes.
    """
    with mock.patch('sky.users.permission._policy_lock'), \
         mock.patch.object(permission.PermissionService,
                           'invalidate_user_permission_cache'), \
         mock.patch.object(permission.PermissionService,
                           'invalidate_workspace_permission_cache'):
        yield _service_over(policy_engine), _service_over(policy_engine)


def _grouping_rows(service, user_id):
    """The user's grouping rows as persisted, read through a fresh model."""
    service.enforcer.load_policy()
    return sorted(service.enforcer.get_roles_for_user(user_id))


class TestStalePolicyWrites:
    """A worker must decide a write against the stored policy, not its model.

    Worker B's in-memory model is deliberately older than worker A's write, so
    each of these fails if B's write is decided against that model.
    """

    def test_add_user_if_not_exists_does_not_duplicate_role(self, two_workers):
        worker_a, worker_b = two_workers
        # B snapshots the empty policy, then A assigns a role behind its back.
        worker_b.enforcer.load_policy()
        worker_a.update_role('victim', 'viewer')

        worker_b.add_user_if_not_exists('victim')

        # Without the reload, B believes victim is role-less and adds the
        # default on top, leaving ['admin', 'viewer'] -- which reads as admin.
        assert _grouping_rows(worker_a, 'victim') == ['viewer']

    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.global_user_state.get_all_users', return_value=[])
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions',
                return_value={})
    @mock.patch('sky.users.rbac.get_role_permissions',
                return_value={'user': {
                    'permissions': {
                        'blocklist': []
                    }
                }})
    @mock.patch.object(permission.PermissionService,
                       '_get_plugin_rbac_rules',
                       return_value={})
    def test_startup_initialization_keeps_other_writes(self, _rules, _perms,
                                                       _ws, _users, _add,
                                                       two_workers):
        """Startup rewrites the whole table, so it must refresh first.

        The enforcer loads in its constructor, before `_policy_lock()` is
        acquired -- and waiting for that lock can take seconds. A replica that
        booted earlier can write in the window, and this path both saves the
        whole table and deletes what it thinks is redundant.
        """
        worker_a, worker_b = two_workers
        # B is mid-boot: model loaded, still waiting for the lock.
        worker_b.enforcer.load_policy()
        worker_a.update_role('early', 'admin')

        worker_b._maybe_initialize_policies()

        # Role assignments are not derived from config, so this method has no
        # basis for dropping one -- it only does so by rewriting the table from
        # a model that never saw it. (Workspace `p` rows are a different
        # matter: reconciling those against config is this method's job, so a
        # grant the config no longer lists is *meant* to go.)
        assert _grouping_rows(worker_a, 'early') == ['admin']

    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.global_user_state.get_user', return_value=None)
    def test_basic_auth_initialization_keeps_other_writes(
            self, _get_user, _add_user, monkeypatch, two_workers):
        """The basic-auth seed also ends in a whole-table rewrite."""
        monkeypatch.setenv(constants.SKYPILOT_INITIAL_BASIC_AUTH,
                           'admin:password123')
        worker_a, worker_b = two_workers
        worker_b.enforcer.load_policy()
        worker_a.update_role('early', 'viewer')

        worker_b._maybe_initialize_basic_auth_user()

        assert _grouping_rows(worker_a, 'early') == ['viewer']

    def test_remove_workspace_policy_keeps_other_writes(self, two_workers):
        worker_a, worker_b = two_workers
        worker_a.add_workspace_policy('ws_victim', ['bob'])
        worker_b.enforcer.load_policy()
        # A demotes alice and grants her a workspace, after B's snapshot.
        worker_a.update_role('alice', 'viewer')
        worker_a.add_workspace_policy('ws_keep', ['alice'])

        worker_b.remove_workspace_policy('ws_victim')

        # Without the reload, B's `remove_filtered_policy` runs against a
        # model that predates A's writes -- and casbin skips the adapter for
        # rows its model does not have, so B would also report success.
        worker_a.enforcer.load_policy()
        remaining = worker_a.enforcer.get_policy()
        assert ['alice', 'ws_keep', '*'] in remaining
        assert ['bob', 'ws_victim', '*'] not in remaining
        assert _grouping_rows(worker_a, 'alice') == ['viewer']


class TestRoleReplacement:
    """A user is only ever meant to hold one role."""

    def test_update_role_replaces_every_role(self, two_workers):
        worker_a, _ = two_workers
        worker_a.enforcer.add_grouping_policy('multi', 'admin')
        worker_a.enforcer.add_grouping_policy('multi', 'user')

        worker_a.update_role('multi', 'viewer')

        # A leftover 'admin' would silently undo the demotion.
        assert _grouping_rows(worker_a, 'multi') == ['viewer']

    def test_update_role_to_the_only_held_role_is_a_noop(self, two_workers):
        worker_a, _ = two_workers
        worker_a.update_role('solo', 'user')
        worker_a.update_role('solo', 'user')
        assert _grouping_rows(worker_a, 'solo') == ['user']

    def test_delete_user_removes_every_role(self, two_workers):
        worker_a, _ = two_workers
        worker_a.enforcer.add_grouping_policy('multi', 'admin')
        worker_a.enforcer.add_grouping_policy('multi', 'user')

        worker_a.delete_user('multi')

        # An orphan grant would be inherited by whoever next takes this id.
        assert _grouping_rows(worker_a, 'multi') == []

    def test_delete_user_removes_workspace_grants(self, two_workers):
        """Deleting a user must revoke their private-workspace membership.

        User ids are derived from the username, so recreating a deleted user
        reuses the id and would silently inherit whatever was left behind.
        """
        worker_a, _ = two_workers
        worker_a.update_role('leaver', 'user')
        worker_a.add_workspace_policy('team_private', ['leaver', 'stayer'])

        worker_a.delete_user('leaver')

        worker_a.enforcer.load_policy()
        remaining = worker_a.enforcer.get_policy()
        assert ['leaver', 'team_private', '*'] not in remaining
        # Only that user's grant goes; the workspace's other members stay.
        assert ['stayer', 'team_private', '*'] in remaining

    def test_delete_user_keeps_a_blocklist_of_the_same_name(self, two_workers):
        """A user id equal to a role name must not take the role's rules.

        Nothing produces such an id today, but the failure is fail-open: a role
        with no blocklist rows is permitted everywhere.
        """
        worker_a, _ = two_workers
        worker_a.enforcer.add_policy('user', '/workspaces/update', 'POST')
        worker_a.enforcer.add_policy('user', 'team_private', '*')

        worker_a.delete_user('user')

        worker_a.enforcer.load_policy()
        remaining = worker_a.enforcer.get_policy()
        assert ['user', '/workspaces/update', 'POST'] in remaining
        assert ['user', 'team_private', '*'] not in remaining


@pytest.fixture
def policy_db(policy_engine):
    """A real sqlite-backed enforcer, so blocklist semantics are the real ones.

    Yields a factory: each call returns an independent PermissionService over
    the same database, which is what two API-server worker processes are.
    """

    def _worker():
        service = _service_over(policy_engine)
        # The viewer allowlist is in-process state, not casbin policy. Without
        # it every path looks disallowed and the viewer branch would block
        # everything for the wrong reason.
        service._build_viewer_allowlist_no_lock()
        return service

    seed = _worker()
    # The `user` role's blocklist, as `_maybe_initialize_policies` would write.
    seed.enforcer.add_policy('user', '/users/export', 'GET')
    seed.enforcer.add_policy('user', '/users/create', 'POST')
    with mock.patch('sky.users.permission._policy_lock'):
        yield _worker


class TestRolelessPrincipalIsDenied:
    """A principal with no role this model recognizes must not be unrestricted.

    Blocklist semantics make the unconfigured case the permissive one: nothing
    mentions an unknown or absent role, so `enforce` permits it everywhere.
    """

    @pytest.mark.parametrize('roles', [[], ['pwned'], ['Admin'], ['']])
    def test_unrecognized_or_absent_role_is_denied(self, policy_db, roles):
        worker = policy_db()
        for role in roles:
            worker.enforcer.add_grouping_policy('subject', role)
        worker.enforcer.load_policy()

        assert worker.check_endpoint_permission('subject', '/users/export',
                                                'GET')
        # Denied outright, not confined: nothing grants this principal access.
        assert worker.check_endpoint_permission('subject', '/status', 'POST')

    def test_recognized_roles_are_unaffected(self, policy_db):
        worker = policy_db()
        worker.enforcer.add_grouping_policy('an_admin', 'admin')
        worker.enforcer.add_grouping_policy('a_user', 'user')
        worker.enforcer.load_policy()

        assert not worker.check_endpoint_permission('an_admin', '/users/export',
                                                    'GET')
        assert worker.check_endpoint_permission('a_user', '/users/export',
                                                'GET')

    def test_a_role_assigned_elsewhere_is_picked_up(self, policy_db):
        """Otherwise a brand-new admin is refused until something else reloads."""
        worker, writer = policy_db(), policy_db()
        worker.enforcer.load_policy()  # stale from here on
        writer.enforcer.add_grouping_policy('fresh_admin', 'admin')

        assert not worker.check_endpoint_permission('fresh_admin',
                                                    '/users/export', 'GET')

    def test_reload_is_not_repeated_per_request(self, policy_db):
        """A genuinely role-less principal must not reload on every request."""
        worker = policy_db()
        worker.enforcer.load_policy()
        loads = []
        original = worker._load_policy_no_lock
        worker._load_policy_no_lock = lambda: (loads.append(1), original())[1]

        for _ in range(5):
            worker.check_endpoint_permission('ghost', '/users/export', 'GET')

        assert len(loads) == 1, f'reloaded {len(loads)} times, expected 1'


class TestRoleSeedMissing:
    """Is a seed owed? Asked by the drain worker before it takes the lock."""

    def test_true_for_a_roleless_principal(self, policy_db):
        worker = policy_db()
        worker.enforcer.load_policy()
        assert worker.role_seed_missing('stranded')

    def test_false_for_a_principal_with_a_role(self, policy_db):
        worker = policy_db()
        worker.enforcer.add_grouping_policy('known', 'user')
        worker.enforcer.load_policy()
        assert not worker.role_seed_missing('known')

    def test_false_when_only_this_worker_is_stale(self, policy_db):
        """A stale worker must not take the policy lock for every request."""
        worker, writer = policy_db(), policy_db()
        worker.enforcer.load_policy()
        writer.enforcer.add_grouping_policy('elsewhere', 'user')

        assert not worker.role_seed_missing('elsewhere')

    def test_false_for_an_unrecognized_role(self, policy_db):
        """Seeding cannot repair this, so asking for it is a pure lock storm.

        `_add_user_if_not_exists_no_lock` only adds a role when there is none,
        so it is a no-op for a principal holding `pwned` -- every request would
        take the distributed lock and change nothing. Confinement in
        `check_endpoint_permission` handles them instead.
        """
        worker = policy_db()
        worker.enforcer.add_grouping_policy('weird', 'pwned')
        worker.enforcer.load_policy()

        assert not worker.role_seed_missing('weird')

    def test_attempts_are_rate_limited(self, policy_db):
        """A seed that keeps failing must not be retried on every request.

        The query itself is idempotent -- it answers from the policy, so it
        keeps saying yes. The backoff is the claim beside it.
        """
        worker = policy_db()
        worker.enforcer.load_policy()
        assert worker.role_seed_missing('stranded')
        assert worker.claim_role_seed_attempt('stranded')
        assert not worker.claim_role_seed_attempt('stranded')
        assert not worker.claim_role_seed_attempt('stranded')

    def test_probably_has_role_never_initializes(self):
        """Safe on the event loop: no enforcer, no database, no answer but False."""
        service = permission.PermissionService()
        assert service.enforcer is None
        assert not service.probably_has_role('anyone')
        assert service.enforcer is None

    def test_probably_has_role_never_touches_the_enforcer(self, policy_db):
        """It runs on the event loop, where the enforcer's read lock can block.

        `load_policy()` holds the write lock for a whole reload, so consulting
        the enforcer here would stall every request on this worker.
        """
        worker = policy_db()
        worker.enforcer.add_grouping_policy('known', 'user')
        worker.enforcer.load_policy()
        worker.enforcer = mock.Mock(wraps=worker.enforcer)

        assert not worker.probably_has_role('known')
        worker.enforcer.get_roles_for_user.assert_not_called()

    def test_a_seen_role_is_remembered_for_the_loop_side_guard(self, policy_db):
        worker = policy_db()
        worker.enforcer.add_grouping_policy('known', 'user')
        worker.enforcer.load_policy()

        assert not worker.probably_has_role('known')
        assert not worker.role_seed_missing('known')
        assert worker.probably_has_role('known')


class TestDenialScope:
    """Who the denial must not catch, and how loudly it reports.

    The server's own identities get their role rows from one write, at boot,
    under the policy lock. If that write failed, denying them would take
    internal calls (the HA replica-to-replica authorize included) from degraded
    to dead, so they are judged by the role that seed would have given them.
    """

    def test_system_identities_keep_their_seeded_role(self, policy_db):
        worker = policy_db()
        worker.enforcer.load_policy()

        for admin_id in (common.SERVER_ID, constants.SKYPILOT_SYSTEM_USER_ID):
            assert not worker.check_endpoint_permission(admin_id,
                                                        '/users/export', 'GET')
        # And the read-only one is still held to the viewer allowlist.
        viewer_id = constants.SKYPILOT_SYSTEM_VIEWER_USER_ID
        assert not worker.check_endpoint_permission(viewer_id, '/users', 'GET')
        assert worker.check_endpoint_permission(viewer_id, '/users/create',
                                                'POST')

    def test_every_reader_agrees_about_a_system_identity(self, policy_db):
        """The role is a property of the identity, not a row that may be gone.

        `sky.users.server` refuses to delete these users or change their role,
        so a decision that reads casbin and finds nothing must not conclude
        they are role-less -- which is what left the endpoint gate and the
        workspace checks disagreeing about the same principal.
        """
        worker = policy_db()
        worker.enforcer.load_policy()

        # The fixture seeds no rows for them, standing in for a boot-time seed
        # that never landed.
        assert worker.enforcer.get_roles_for_user(common.SERVER_ID) == []
        assert worker.get_user_roles(common.SERVER_ID) == ['admin']
        assert worker.roles_in_memory(common.SERVER_ID) == ['admin']
        assert worker.roles_in_memory(
            constants.SKYPILOT_SYSTEM_VIEWER_USER_ID) == ['viewer']

    def test_an_ordinary_principal_is_untouched(self, policy_db):
        worker = policy_db()
        worker.enforcer.add_grouping_policy('someone', 'user')
        worker.enforcer.load_policy()

        assert worker.get_user_roles('someone') == ['user']
        assert worker.get_user_roles('ghost') == []

    def test_the_denial_is_logged_once_per_principal(self, policy_db):
        """Persistent state, so an unrated log would repeat on every request."""
        worker = policy_db()
        worker.enforcer.load_policy()

        # The repair the denial queues logs too, and would seed a role midway
        # through the loop; this test is about the denial's own line.
        with mock.patch.object(worker, '_schedule_role_repair'), \
             mock.patch.object(permission.logger, 'warning') as warn:
            for _ in range(5):
                worker.check_endpoint_permission('ghost', '/users/export',
                                                 'GET')
        denials = [c for c in warn.call_args_list if 'Denying' in c[0][0]]
        assert len(denials) == 1
        assert 'ghost' in denials[0][0][0]


class TestScheduledRoleRepair:
    """The denial queues the repair instead of performing it.

    `check_endpoint_permission` runs inside the auth executor's 5s deadline
    while the policy lock waits up to 20s, so seeding inline would answer 503
    under exactly the contention that strands principals -- and hold an auth
    thread for the duration.
    """

    def test_a_denial_queues_a_repair(self, policy_db):
        worker = policy_db()
        worker.enforcer.load_policy()
        queued = []
        with mock.patch.object(worker,
                               '_schedule_role_repair',
                               side_effect=queued.append):
            assert worker.check_endpoint_permission('ghost', '/users/export',
                                                    'GET')
        assert queued == ['ghost']

    def test_the_repair_runs_off_the_request(self, policy_db):
        """Nothing blocking happens on the caller's thread."""
        worker = policy_db()
        worker.enforcer.load_policy()
        submitted = []
        with mock.patch.object(worker, '_run_role_repair', submitted.append):
            worker._schedule_role_repair('ghost')
            worker._repair_queue.join()
        assert submitted == ['ghost']
        assert worker._repair_worker.daemon, (
            'a non-daemon worker delays interpreter exit by however long the '
            'policy lock makes a repair wait')

    def test_queueing_is_claimed_once_per_ttl(self, policy_db):
        """Draining between attempts, so the in-flight dedup cannot stand in.

        With the queue emptied each time, only the per-principal claim stops the
        next attempt -- which is what this pins.
        """
        worker = policy_db()
        worker.enforcer.load_policy()
        with mock.patch.object(worker, '_run_role_repair') as run:
            for _ in range(5):
                worker._schedule_role_repair('ghost')
                worker._repair_queue.join()
        assert run.call_count == 1

    def test_a_burst_is_bounded(self, policy_db):
        """The per-principal claim bounds the rate; this bounds the burst."""
        worker = policy_db()
        worker.enforcer.load_policy()
        release = threading.Event()
        # Hold the worker on the first repair so the queue cannot drain while
        # the burst is being measured.
        with mock.patch.object(worker, '_run_role_repair',
                               lambda _: release.wait(10)):
            try:
                for i in range(permission._REPAIR_MAX_IN_FLIGHT + 10):
                    worker._schedule_role_repair(f'ghost{i}')
                in_flight = len(worker._repair_in_flight)
            finally:
                release.set()
                worker._repair_queue.join()
        assert in_flight <= permission._REPAIR_MAX_IN_FLIGHT

    def test_a_claim_is_not_spent_when_the_queue_is_full(self, policy_db):
        """Otherwise that principal waits out a TTL for a repair never run."""
        worker = policy_db()
        worker.enforcer.load_policy()
        worker._repair_in_flight = {
            f'other{i}' for i in range(permission._REPAIR_MAX_IN_FLIGHT)
        }

        worker._schedule_role_repair('ghost')

        assert worker.claim_role_seed_attempt('ghost'), (
            'the claim was consumed by a queueing attempt that did nothing')

    def test_an_unrecognized_role_is_not_queued(self, policy_db):
        """A seed fills a gap, and a name nobody recognizes is not one.

        Queueing it would spin in the worker and spend a slot the genuinely
        role-less need.
        """
        worker = policy_db()
        worker.enforcer.add_grouping_policy('weird', 'pwned')
        worker.enforcer.load_policy()

        assert worker.check_endpoint_permission('weird', '/users/export', 'GET')

        assert worker._repair_in_flight == set()
        assert worker._repair_queue.empty()
        assert worker._repair_worker is None

    @pytest.mark.parametrize(
        'in_flight,asking',
        [
            # Waiting its turn. The stranded set is usually small and fixed, so
            # a brand-new principal may never arrive to trigger the restart.
            ({'ghost'}, 'ghost'),
            # Full, which is the state a dead worker produces: every caller
            # stops at the cap, the symptom a restart exists to end.
            ({f'u{i}' for i in range(permission._REPAIR_MAX_IN_FLIGHT)}, 'new'),
        ],
        ids=['already-queued', 'cap-full'])
    def test_a_dead_worker_is_replaced(self, policy_db, in_flight, asking):
        """Otherwise every later repair queues into a thread nobody reads.

        Both callers below decline to queue, so a restart that sits after those
        returns never runs -- and nothing else ever reads the queue.
        """
        worker = policy_db()
        worker.enforcer.load_policy()
        with mock.patch.object(worker, '_run_role_repair'):
            worker._schedule_role_repair('seed')
            worker._repair_queue.join()
        dead = worker._repair_worker
        assert dead is not None

        worker._repair_in_flight = set(in_flight)
        with mock.patch.object(dead, 'is_alive', return_value=False):
            worker._schedule_role_repair(asking)

        assert worker._repair_worker is not dead
        assert worker._repair_worker.is_alive()

    def test_a_queued_principal_is_not_logged_as_turned_away(self, policy_db):
        """A full queue must not report the principals already in it as dropped.

        Reading that warning is how an operator learns repairs are being lost,
        so it has to mean that.
        """
        worker = policy_db()
        worker.enforcer.load_policy()
        worker._repair_in_flight = {'ghost'} | {
            f'other{i}' for i in range(permission._REPAIR_MAX_IN_FLIGHT)
        }

        with mock.patch.object(permission.logger, 'warning') as warn:
            worker._schedule_role_repair('ghost')

        assert not [c for c in warn.call_args_list if 'Not queueing' in c[0][0]]

    def test_a_healthy_principal_warms_the_loop_side_guard(self, policy_db):
        """Otherwise the guard is cold for everyone until a repair has run.

        The login paths queue a repair when it answers False, so a memo written
        only by repairs made every login after a restart queue a pointless one
        -- enough of them to fire the in-flight cap warning on warm-up.
        """
        worker = policy_db()
        worker.enforcer.add_grouping_policy('healthy', 'admin')
        worker.enforcer.load_policy()
        assert not worker.probably_has_role('healthy')

        worker.check_endpoint_permission('healthy', '/users', 'GET')

        assert worker.probably_has_role('healthy')

    def test_the_cap_warning_is_rate_limited(self, policy_db):
        """It is the line that says repairs are being dropped. Once per TTL.

        Unrated, a burst repeats it per request and it reads as a worsening
        incident rather than one condition.
        """
        worker = policy_db()
        worker._repair_in_flight = {
            f'u{i}' for i in range(permission._REPAIR_MAX_IN_FLIGHT)
        }
        with mock.patch.object(permission.logger, 'warning') as warn:
            for i in range(5):
                worker._schedule_role_repair(f'late{i}')
        assert len(
            [c for c in warn.call_args_list if 'Not queueing' in c[0][0]]) == 1

    def test_a_failed_thread_start_leaves_nothing_stranded(self, policy_db):
        """The in-flight entry is what dedups later attempts, so it must not
        outlive a failed queueing.

        Only the drain worker removes it -- and if the worker could not start,
        there is no worker. The principal would be unrepairable for the life of
        the process, which is worse than the exception that got them there.
        """
        worker = policy_db()
        worker.enforcer.load_policy()
        with mock.patch.object(worker,
                               '_start_repair_worker',
                               side_effect=RuntimeError('out of threads')):
            worker.queue_role_repair('ghost')

        assert 'ghost' not in worker._repair_in_flight
        # And a later attempt is not merely accepted but actually run. Asserting
        # only the empty set, or joining the queue, proves neither: `join()`
        # returns at once on a queue nothing was ever put on.
        worker._seed_attempt_cache.clear()
        with mock.patch.object(worker, '_run_role_repair') as run:
            worker.queue_role_repair('ghost')
            worker._repair_queue.join()
        run.assert_called_once_with('ghost')

    def test_the_gate_survives_a_queueing_failure(self, policy_db):
        """The gate runs inside the RBAC middleware, where an exception is a
        bare 500 rather than a denial.

        Reverting it to the unguarded private method leaves the denial path
        raising instead of answering.
        """
        worker = policy_db()
        worker.enforcer.load_policy()
        with mock.patch.object(worker,
                               '_start_repair_worker',
                               side_effect=RuntimeError('out of threads')):
            assert worker.check_endpoint_permission('ghost', '/users/export',
                                                    'GET') is True

    def test_queueing_never_raises(self, policy_db):
        """A repair nobody waits for must not turn a login into a 500.

        `Thread.start()` raises when the process is out of threads, and the
        login and provisioning paths call this with no guard of their own.
        """
        worker = policy_db()
        with mock.patch.object(worker,
                               '_schedule_role_repair',
                               side_effect=RuntimeError('out of threads')):
            worker.queue_role_repair('ghost')

    def test_no_thread_until_one_is_needed(self):
        """The CLI imports this module; it must not start a thread."""
        assert permission.PermissionService()._repair_worker is None

    def test_a_denial_reaches_the_seed(self, policy_db):
        """Gate -> queue -> worker -> seed, in one process."""
        worker = policy_db()
        worker.enforcer.load_policy()

        with mock.patch.object(permission, 'seed_new_user_role') as seed:
            assert worker.check_endpoint_permission('ghost', '/users/export',
                                                    'GET')
            worker._repair_queue.join()
        seed.assert_called_once_with('ghost')

    def test_a_failed_repair_is_swallowed(self, policy_db):
        """Nothing awaits it, and the next request tries again."""
        worker = policy_db()
        worker.enforcer.load_policy()
        with mock.patch.object(permission,
                               'seed_new_user_role',
                               side_effect=RuntimeError('lock timeout')):
            worker._run_role_repair('ghost')  # must not raise

    def test_a_drained_repair_leaves_no_claim_on_the_queue(self, policy_db):
        """The drain loop clears in-flight, so a later attempt can queue again.

        Keeping that in the repair body meant a stubbed body left the principal
        marked as queued forever -- which silently turned the claim's rate limit
        into dedup, and hid a missing rate limit from its own test.
        """
        worker = policy_db()
        worker.enforcer.load_policy()
        with mock.patch.object(worker, '_run_role_repair'):
            worker._schedule_role_repair('ghost')
            worker._repair_queue.join()
        assert worker._repair_in_flight == set()


class TestProbeThrottle:
    """Reload-on-miss is bounded per worker, not only per principal.

    The per-principal memo is cleared wholesale on overflow and expires by TTL,
    and the failure this feature exists for strands many principals at once --
    so without a global bound, N of them means N reloads per TTL, each holding
    the enforcer's write lock.
    """

    def test_a_second_principal_does_not_reload_immediately(self, policy_db):
        worker = policy_db()
        worker.enforcer.load_policy()
        loads = []

        def slow_load():
            # The cooldown is 9x the reload, so an instant stub buys a few
            # microseconds and a slow runner reaches the second probe after.
            loads.append(1)
            time.sleep(0.05)

        with mock.patch.object(worker, '_load_policy_no_lock', slow_load):
            worker._probe_unknown_principal('ghost1')
            worker._probe_unknown_principal('ghost2')
        assert len(loads) == 1, f'reloaded {len(loads)} times, expected 1'

    def test_the_cooldown_scales_with_the_reload(self, policy_db):
        worker = policy_db()
        worker.enforcer.load_policy()

        def slow_load():
            time.sleep(0.05)

        before = time.time()
        with mock.patch.object(worker, '_load_policy_no_lock', slow_load):
            worker._probe_unknown_principal('ghost1')
        # duty cycle 0.1 => a 50ms reload buys ~450ms of cooldown.
        assert worker._probe_cooldown_until - before > 0.3

    def test_a_failed_reload_answers_role_less(self, policy_db):
        """Raising would leave the middleware with a bare 500."""
        worker = policy_db()
        worker.enforcer.load_policy()
        with mock.patch.object(worker,
                               '_load_policy_no_lock',
                               side_effect=RuntimeError('db down')):
            assert worker._probe_unknown_principal('ghost') == []

    def test_a_failed_reload_still_sets_the_cooldown(self, policy_db):
        """Otherwise a broken database is retried on every request."""
        worker = policy_db()
        worker.enforcer.load_policy()
        with mock.patch.object(worker,
                               '_load_policy_no_lock',
                               side_effect=RuntimeError('db down')):
            worker._probe_unknown_principal('ghost')
        assert worker._probe_cooldown_until > time.time() - 1


class TestBoundedClaimCache:
    """Over the cap, drop what expired -- never the whole cache.

    These entries all expire, so clearing them wholesale would send every
    principal back through the work the rate limit exists to space out, at the
    same moment.
    """

    def test_an_expired_entry_is_dropped_before_a_live_one(self):
        cache = {'old': time.time() - 10.0, 'fresh': time.time()}
        assert permission._take_ttl_permit(cache, 'new', ttl=5.0, cap=2)
        assert 'old' not in cache
        assert 'fresh' in cache, 'a live entry was dropped'
        assert 'new' in cache

    def test_a_live_entry_survives_an_eviction(self):
        """`fresh` keeping its stamp is what stops the herd."""
        now = time.time()
        cache = {f'u{i}': now for i in range(4)}
        permission._take_ttl_permit(cache, 'new', ttl=60.0, cap=4)
        # All live and over the cap: half go, the rest keep their rate limit.
        assert 2 <= len(cache) <= 4
        assert not permission._take_ttl_permit(cache, 'new', ttl=60.0, cap=4)

    def test_a_seen_role_set_drops_half_when_full(self):
        """Same argument as the TTL caches: clearing it stampedes every one.

        Every principal whose entry vanished pays another confirmation off the
        loop, and after a clear that is all of them at once.
        """
        service = permission.PermissionService()
        cap = permission._NO_ROLE_PROBE_CACHE_MAX
        for i in range(cap):
            service.remember_role_seen(f'u{i}')

        service.remember_role_seen('one-more')

        assert len(service._role_seen) > cap // 4, (
            f'{len(service._role_seen)} left: the set was cleared, not halved')
        assert 'one-more' in service._role_seen

    def test_the_claim_is_consumed(self):
        cache: dict = {}
        assert permission._take_ttl_permit(cache, 'u', ttl=60.0, cap=8)
        assert not permission._take_ttl_permit(cache, 'u', ttl=60.0, cap=8)


class TestDefaultRoleCasing:
    """`default_role` is validated case-insensitively but stored verbatim.

    So `default_role: Viewer` is a legal config whose literal string would be
    seeded as a role name, and an unrecognized role is now denied everywhere --
    turning one operator's capitalization into a deployment-wide lockout.
    """

    @pytest.mark.parametrize('configured,expected', [('Viewer', 'viewer'),
                                                     ('ADMIN', 'admin'),
                                                     ('user', 'user')])
    def test_get_default_role_is_normalized(self, configured, expected):
        with mock.patch('sky.users.rbac.skypilot_config.get_nested',
                        return_value=configured):
            assert rbac.get_default_role() == expected


class TestWritesPersistWithoutSavePolicy:
    """Every write path must reach the database on the mutator alone.

    Casbin's auto-save calls the adapter from inside `add_policy` /
    `remove_filtered_policy` / etc., so the `save_policy()` rewrite that used to
    follow each mutation was persisting what had already been persisted. These
    read back through a *second* service over the same database -- what one
    service holds in memory proves nothing about what reached storage.
    """

    @staticmethod
    def _reader(policy_db):
        reader = policy_db()
        reader.enforcer.load_policy()
        return reader

    def test_a_role_assignment_persists(self, policy_db):
        writer = policy_db()
        writer.enforcer.load_policy()
        writer.update_role('u1', 'admin')

        assert _grouping_rows(self._reader(policy_db), 'u1') == ['admin']

    def test_a_role_replacement_persists(self, policy_db):
        writer = policy_db()
        writer.enforcer.load_policy()
        writer.update_role('u1', 'admin')
        writer.update_role('u1', 'viewer')

        # Both halves have to have landed: the removal as well as the add, or
        # the user keeps admin alongside viewer and admin wins.
        assert _grouping_rows(self._reader(policy_db), 'u1') == ['viewer']

    def test_a_workspace_grant_persists(self, policy_db):
        writer = policy_db()
        writer.enforcer.load_policy()
        writer.add_workspace_policy('ws', ['u1'])

        grants = self._reader(policy_db).enforcer.get_policy()
        assert ['u1', 'ws', '*'] in grants

    def test_a_workspace_removal_persists(self, policy_db):
        writer = policy_db()
        writer.enforcer.load_policy()
        writer.add_workspace_policy('ws', ['u1'])
        # Confirm it landed before removing it. Otherwise a broken *add* also
        # satisfies the assertion below, and the test passes for the one reason
        # it exists to rule out.
        before = self._reader(policy_db).enforcer.get_policy()
        assert ['u1', 'ws', '*'] in before

        writer.remove_workspace_policy('ws')

        after = self._reader(policy_db).enforcer.get_policy()
        assert ['u1', 'ws', '*'] not in after

    def test_a_user_deletion_persists(self, policy_db):
        writer = policy_db()
        writer.enforcer.load_policy()
        writer.update_role('u1', 'admin')
        writer.add_workspace_policy('ws', ['u1'])
        seeded = self._reader(policy_db)
        assert _grouping_rows(seeded, 'u1') == ['admin']
        assert ['u1', 'ws', '*'] in seeded.enforcer.get_policy()

        writer.delete_user('u1')

        reader = self._reader(policy_db)
        assert _grouping_rows(reader, 'u1') == []
        assert ['u1', 'ws', '*'] not in reader.enforcer.get_policy()

    def test_no_write_path_calls_save_policy(self, policy_db):
        """The rewrite is gone, not merely unnecessary.

        Without this, someone re-adding one `save_policy()` for symmetry would
        pass every test above -- they assert the row is there, and a rewrite
        also puts it there.
        """
        writer = policy_db()
        writer.enforcer.load_policy()
        with mock.patch.object(writer.enforcer,
                               'save_policy',
                               side_effect=AssertionError(
                                   'save_policy() is back on a write path')):
            writer.update_role('u1', 'admin')
            writer.add_workspace_policy('ws', ['u1'])
            writer.update_workspace_policy('ws', ['u2'])
            writer.remove_workspace_policy('ws')
            writer.delete_user('u1')
