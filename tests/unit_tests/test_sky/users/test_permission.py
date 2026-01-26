"""Unit tests for permission service using pytest."""

import os
import threading
import time
from unittest import mock

import pytest
import sqlalchemy_adapter

from sky import models
from sky.skylet import constants
from sky.users import permission
from sky.users import rbac
from sky.utils import common


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


@pytest.mark.usefixtures("reset_permission_singleton", "cleanup_env_vars")
class TestPermissionService:
    """Test permission service functionality."""

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.sqlalchemy_adapter.Adapter')
    @mock.patch('sky.users.permission.casbin.Enforcer')
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
        mock_adapter = mock.Mock()
        mock_adapter_class.return_value = mock_adapter
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        service = permission.PermissionService()
        service._lazy_initialize()

        # Verify database was initialized
        mock_init_db.assert_called_once()

        # Verify SQLAlchemy adapter was created with the correct engine
        mock_adapter_class.assert_called_once_with(
            mock_engine, db_class=sqlalchemy_adapter.CasbinRule)

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
    def test_maybe_initialize_policies_new_setup(self, mock_get_users,
                                                 mock_get_role_perms,
                                                 mock_get_workspace_perms,
                                                 mock_get_plugin_rules,
                                                 mock_policy_lock, mock_users):
        """Test policy initialization for a new setup."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # Mock enforcer
        mock_enforcer = mock.Mock()
        mock_enforcer.get_policy.return_value = []  # No existing policies
        mock_enforcer.get_grouping_policy.return_value = []  # No existing roles
        # Users don't have roles yet (used by _add_user_if_not_exists_no_lock)
        mock_enforcer.get_roles_for_user.return_value = []
        mock_enforcer.add_policy.return_value = True
        mock_enforcer.add_grouping_policy.return_value = True

        # Mock plugin RBAC rules (empty by default)
        mock_get_plugin_rules.return_value = {}

        # Mock role permissions
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

        # Mock workspace permissions
        mock_get_workspace_perms.return_value = {
            'default': ['*'],
            'private-ws': ['user1', 'user2']
        }

        # Mock users
        mock_get_users.return_value = mock_users

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._maybe_initialize_policies()

        # Verify plugin RBAC rules were fetched
        mock_get_plugin_rules.assert_called_once()

        # Verify policies were added for user role blocklist
        mock_enforcer.add_policy.assert_any_call('user', '/workspaces/config',
                                                 'POST')

        # Verify workspace policies were added
        mock_enforcer.add_policy.assert_any_call('*', 'default', '*')
        mock_enforcer.add_policy.assert_any_call('user1', 'private-ws', '*')
        mock_enforcer.add_policy.assert_any_call('user2', 'private-ws', '*')

        # Verify users were assigned default roles
        for user in mock_users:
            mock_enforcer.add_grouping_policy.assert_any_call(
                user.id, rbac.get_default_role())

        # Verify policy was saved
        mock_enforcer.save_policy.assert_called()

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_maybe_initialize_policies_existing_policies(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users):
        """Test policy initialization when policies already exist (idempotent)."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # Mock enforcer
        mock_enforcer = mock.Mock()
        # Simulate existing policies that match expected policies
        existing_policies = [['user', '/workspaces/config', 'POST'],
                             ['*', 'default', '*'],
                             ['user1', 'private-ws', '*'],
                             ['user2', 'private-ws', '*']]
        mock_enforcer.get_policy.return_value = existing_policies
        # Users already have roles (returned as grouping policies)
        mock_enforcer.get_grouping_policy.return_value = [['user1', 'user'],
                                                          ['user2', 'user'],
                                                          ['user3', 'user']]
        mock_enforcer.add_policy.return_value = True
        mock_enforcer.add_grouping_policy.return_value = True

        # Mock plugin RBAC rules
        mock_get_plugin_rules.return_value = {}

        # Mock role permissions
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

        # Mock workspace permissions
        mock_get_workspace_perms.return_value = {
            'default': ['*'],
            'private-ws': ['user1', 'user2']
        }

        # Mock users
        mock_get_users.return_value = mock_users

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._maybe_initialize_policies()

        # Verify no new policies were added (since they already exist)
        mock_enforcer.add_policy.assert_not_called()
        # save_policy should not be called if no updates were made
        # (users already have roles, policies already exist)
        mock_enforcer.save_policy.assert_not_called()

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
    def test_update_role_new_role(self, mock_policy_lock):
        """Test updating user role to a new role."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = ['user']  # Current role
        mock_enforcer.remove_grouping_policy.return_value = True
        mock_enforcer.add_grouping_policy.return_value = True

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._load_policy_no_lock = mock.Mock()

        service.update_role('user1', 'admin')

        # Verify old role was removed and new role was added
        mock_enforcer.remove_grouping_policy.assert_called_once_with(
            'user1', 'user')
        mock_enforcer.add_grouping_policy.assert_called_once_with(
            'user1', 'admin')
        mock_enforcer.save_policy.assert_called_once()

    @mock.patch('sky.users.permission._policy_lock')
    def test_update_role_same_role(self, mock_policy_lock):
        """Test updating user role to the same role (no-op)."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        mock_enforcer = mock.Mock()
        mock_enforcer.get_roles_for_user.return_value = ['admin'
                                                        ]  # Current role

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._load_policy_no_lock = mock.Mock()

        service.update_role('user1', 'admin')

        # Verify no changes were made
        mock_enforcer.remove_grouping_policy.assert_not_called()
        mock_enforcer.add_grouping_policy.assert_not_called()
        mock_enforcer.save_policy.assert_not_called()

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
        """Test checking endpoint permissions."""
        mock_enforcer = mock.Mock()
        mock_enforcer.enforce.return_value = True

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        result = service.check_endpoint_permission('user1', '/api/test', 'GET')

        assert result is True
        mock_enforcer.enforce.assert_called_once_with('user1', '/api/test',
                                                      'GET')

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

    @mock.patch('sky.users.permission._policy_lock')
    def test_add_workspace_policy(self, mock_policy_lock):
        """Test adding workspace policy."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        mock_enforcer = mock.Mock()
        mock_enforcer.add_policy.return_value = True

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        service.add_workspace_policy('test-workspace', ['user1', 'user2'])

        # Verify policies were added for each user
        mock_enforcer.add_policy.assert_any_call('user1', 'test-workspace', '*')
        mock_enforcer.add_policy.assert_any_call('user2', 'test-workspace', '*')
        mock_enforcer.save_policy.assert_called_once()

    @mock.patch('sky.users.permission.filelock.FileLock')
    def test_policy_lock_context_manager(self, mock_filelock):
        """Test the policy lock context manager."""
        mock_lock = mock.Mock()
        mock_lock.__enter__ = mock.Mock(return_value=mock.Mock())
        mock_lock.__exit__ = mock.Mock(return_value=None)
        mock_filelock.return_value = mock_lock

        with permission._policy_lock():
            pass

        mock_filelock.assert_called_once_with(
            permission.POLICY_UPDATE_LOCK_PATH,
            permission.POLICY_UPDATE_LOCK_TIMEOUT_SECONDS)
        mock_lock.__enter__.assert_called_once()
        mock_lock.__exit__.assert_called_once()

    @mock.patch('sky.users.permission.filelock.FileLock')
    def test_policy_lock_timeout_exception(self, mock_filelock):
        """Test policy lock timeout exception handling."""
        from filelock import Timeout

        mock_lock = mock.Mock()
        mock_lock.__enter__ = mock.Mock(side_effect=Timeout('test_lock'))
        mock_lock.__exit__ = mock.Mock(return_value=None)
        mock_filelock.return_value = mock_lock

        with pytest.raises(RuntimeError) as exc_info:
            with permission._policy_lock():
                pass

        assert 'Failed to reload policy due to a timeout' in str(exc_info.value)
        assert 'policy_update.lock' in str(exc_info.value)

    def test_delete_user_with_role(self):
        """Test deleting a user who has a role."""
        mock_enforcer = mock.Mock()
        # User has a role
        mock_enforcer.get_roles_for_user.return_value = ['user']
        mock_enforcer.remove_grouping_policy.return_value = True

        with mock.patch.object(permission.PermissionService,
                               '__init__',
                               return_value=None):
            service = permission.PermissionService()
            service.enforcer = mock_enforcer

            service.delete_user('user1')

            mock_enforcer.get_roles_for_user.assert_called_once_with('user1')
            mock_enforcer.remove_grouping_policy.assert_called_once_with(
                'user1', 'user')
            mock_enforcer.save_policy.assert_called_once()

    def test_delete_user_without_role(self):
        """Test deleting a user who has no roles."""
        mock_enforcer = mock.Mock()
        # User has no roles
        mock_enforcer.get_roles_for_user.return_value = []

        with mock.patch.object(permission.PermissionService,
                               '__init__',
                               return_value=None):
            service = permission.PermissionService()
            service.enforcer = mock_enforcer

            service.delete_user('user2')

            mock_enforcer.get_roles_for_user.assert_called_once_with('user2')
            mock_enforcer.remove_grouping_policy.assert_not_called()
            mock_enforcer.save_policy.assert_not_called()

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.sqlalchemy_adapter.Adapter')
    @mock.patch('sky.users.permission.casbin.Enforcer')
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
    @mock.patch('sky.users.permission.sqlalchemy_adapter.Adapter')
    @mock.patch('sky.users.permission.casbin.Enforcer')
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
    @mock.patch('sky.users.permission.sqlalchemy_adapter.Adapter')
    @mock.patch('sky.users.permission.casbin.Enforcer')
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
        mock_enforcer.save_policy.return_value = True

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
        # Verify policy was saved
        mock_enforcer.save_policy.assert_called_once()

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
        mock_enforcer.save_policy.assert_not_called()

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
        mock_enforcer.save_policy.assert_not_called()

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_maybe_initialize_policies_with_plugin_rules(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users):
        """Test policy initialization with plugin RBAC rules."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # Mock enforcer
        mock_enforcer = mock.Mock()
        mock_enforcer.get_policy.return_value = []
        mock_enforcer.get_grouping_policy.return_value = []
        mock_enforcer.add_policy.return_value = True
        mock_enforcer.add_grouping_policy.return_value = True

        # Mock plugin RBAC rules
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

        # Mock workspace permissions
        mock_get_workspace_perms.return_value = {'default': ['*']}

        # Mock users
        mock_get_users.return_value = mock_users

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._maybe_initialize_policies()

        # Verify plugin rules were fetched
        mock_get_plugin_rules.assert_called_once()

        # Verify policies were added including plugin rules
        mock_enforcer.add_policy.assert_any_call('user', '/workspaces/config',
                                                 'POST')
        mock_enforcer.add_policy.assert_any_call('user', '/plugins/api/test',
                                                 'POST')
        mock_enforcer.add_policy.assert_any_call('user', '/plugins/api/test',
                                                 'DELETE')

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_maybe_initialize_policies_plugin_rules_import_error(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users):
        """Test policy initialization when plugin module is not available."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # Mock enforcer
        mock_enforcer = mock.Mock()
        mock_enforcer.get_policy.return_value = []
        mock_enforcer.get_grouping_policy.return_value = []
        mock_enforcer.add_policy.return_value = True
        mock_enforcer.add_grouping_policy.return_value = True

        # Simulate ImportError - return empty dict (as the actual method would)
        mock_get_plugin_rules.return_value = {}

        # Mock role permissions (without plugin rules)
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

        # Mock workspace permissions
        mock_get_workspace_perms.return_value = {'default': ['*']}

        # Mock users
        mock_get_users.return_value = mock_users

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        # Should not raise an exception, should handle gracefully
        service._maybe_initialize_policies()

        # Verify plugin rules were attempted to be fetched
        mock_get_plugin_rules.assert_called_once()
        # Verify policies were still initialized correctly
        mock_enforcer.add_policy.assert_any_call('user', '/workspaces/config',
                                                 'POST')

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_maybe_initialize_policies_no_save_when_no_changes(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users):
        """Test that save_policy is not called when no changes are made."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # Mock enforcer with existing policies matching expected
        existing_policies = [['user', '/workspaces/config', 'POST'],
                             ['*', 'default', '*']]
        mock_enforcer = mock.Mock()
        mock_enforcer.get_policy.return_value = existing_policies
        mock_enforcer.get_grouping_policy.return_value = [['user1', 'user'],
                                                          ['user2', 'user'],
                                                          ['user3', 'user']]

        # Mock plugin RBAC rules
        mock_get_plugin_rules.return_value = {}

        # Mock role permissions matching existing policies
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

        # Mock workspace permissions matching existing policies
        mock_get_workspace_perms.return_value = {'default': ['*']}

        # Mock users
        mock_get_users.return_value = mock_users

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._maybe_initialize_policies()

        # Verify no policies were added or removed
        mock_enforcer.add_policy.assert_not_called()
        mock_enforcer.remove_policy.assert_not_called()
        # Verify save_policy was NOT called since no changes were made
        mock_enforcer.save_policy.assert_not_called()

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_maybe_initialize_policies_remove_redundant(
            self, mock_get_users, mock_get_role_perms, mock_get_workspace_perms,
            mock_get_plugin_rules, mock_policy_lock, mock_users):
        """Test policy initialization removes redundant policies."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # Mock enforcer
        mock_enforcer = mock.Mock()
        # Simulate existing policies, including a redundant one
        existing_policies = [
            ['user', '/workspaces/config', 'POST'],
            ['*', 'default', '*'],
            ['user-to-be-removed', 'some-workspace', '*']  # Redundant
        ]
        mock_enforcer.get_policy.return_value = existing_policies
        mock_enforcer.get_grouping_policy.return_value = [['user1', 'user'],
                                                          ['user2', 'user'],
                                                          ['user3', 'user']]
        mock_enforcer.add_policy.return_value = True
        mock_enforcer.remove_policy.return_value = True
        mock_enforcer.add_grouping_policy.return_value = True

        # Mock plugin RBAC rules
        mock_get_plugin_rules.return_value = {}

        # Mock role permissions (only one policy expected)
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

        # Mock workspace permissions (only one policy expected)
        mock_get_workspace_perms.return_value = {'default': ['*']}

        # Mock users
        mock_get_users.return_value = mock_users

        service = permission.PermissionService()
        service.enforcer = mock_enforcer
        service._maybe_initialize_policies()

        # Verify redundant policy was removed
        mock_enforcer.remove_policy.assert_called_once_with(
            'user-to-be-removed', 'some-workspace', '*')
        # Verify save_policy was called
        mock_enforcer.save_policy.assert_called()


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
            mock_get_plugin_rules, mock_policy_lock, mock_users):
        """Test that concurrent initialization doesn't create duplicate policies."""
        # This simulates multiple threads trying to initialize policies simultaneously
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # Track calls to add_policy and add_grouping_policy
        policy_calls = []
        grouping_policy_calls = []

        def track_add_policy(*args):
            policy_calls.append(args)
            return True

        def track_add_grouping_policy(*args):
            grouping_policy_calls.append(args)
            return True

        # Mock enforcer
        mock_enforcer = mock.Mock()
        mock_enforcer.get_policy.return_value = [
        ]  # No existing policies initially
        mock_enforcer.get_grouping_policy.return_value = []  # No existing roles
        # Users don't have roles yet (used by _add_user_if_not_exists_no_lock)
        mock_enforcer.get_roles_for_user.return_value = []
        mock_enforcer.add_policy.side_effect = track_add_policy
        mock_enforcer.add_grouping_policy.side_effect = track_add_grouping_policy

        # Mock plugin RBAC rules
        mock_get_plugin_rules.return_value = {}

        # Mock role permissions
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

        # Mock workspace permissions
        mock_get_workspace_perms.return_value = {
            'workspace1': ['user1', 'user2']
        }

        # Mock users
        mock_get_users.return_value = mock_users

        # Simulate concurrent initialization
        services = []
        errors = []

        def create_service():
            try:
                service = permission.PermissionService()
                service.enforcer = mock_enforcer
                service._maybe_initialize_policies()
                services.append(service)
            except Exception as e:
                errors.append(e)

        # Create multiple threads to simulate concurrent access
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=create_service)
            threads.append(thread)

        # Start all threads simultaneously
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify no errors occurred
        assert len(
            errors) == 0, f"Errors during concurrent initialization: {errors}"

        # Verify that each policy and grouping policy was only added once per user
        # (even though multiple threads tried to initialize)
        unique_policy_calls = set(policy_calls)
        unique_grouping_policy_calls = set(grouping_policy_calls)

        # Should have one policy call per workspace-user combination
        expected_policy_calls = {('user', '/workspaces/config', 'POST'),
                                 ('user1', 'workspace1', '*'),
                                 ('user2', 'workspace1', '*')}

        # Should have one grouping policy call per user (for default role assignment)
        # plus system users (SERVER_ID and SKYPILOT_SYSTEM_USER_ID) with admin role
        expected_grouping_policy_calls = {('user1', rbac.get_default_role()),
                                          ('user2', rbac.get_default_role()),
                                          ('user3', rbac.get_default_role()),
                                          (common.SERVER_ID, 'admin'),
                                          (constants.SKYPILOT_SYSTEM_USER_ID,
                                           'admin')}

        assert unique_policy_calls == expected_policy_calls
        assert unique_grouping_policy_calls == expected_grouping_policy_calls

    @mock.patch('sky.users.permission._policy_lock')
    @mock.patch('sky.users.permission.PermissionService._get_plugin_rbac_rules')
    @mock.patch('sky.users.rbac.get_workspace_policy_permissions')
    @mock.patch('sky.users.rbac.get_role_permissions')
    @mock.patch('sky.global_user_state.get_all_users')
    def test_idempotent_user_addition(self, mock_get_users, mock_get_role_perms,
                                      mock_get_workspace_perms,
                                      mock_get_plugin_rules, mock_policy_lock,
                                      mock_users):
        """Test that adding the same user multiple times is idempotent."""
        mock_policy_lock.return_value.__enter__ = mock.Mock()
        mock_policy_lock.return_value.__exit__ = mock.Mock()

        # Track calls to add_grouping_policy
        grouping_policy_calls = []

        def track_add_grouping_policy(*args):
            grouping_policy_calls.append(args)
            return True

        # Mock enforcer - simulate that users already have roles after first call
        mock_enforcer = mock.Mock()
        mock_enforcer.get_policy.return_value = []
        mock_enforcer.add_policy.return_value = True

        # Track initialization calls
        init_call_count = 0
        user_roles_map = {}  # Track which users have been assigned roles

        def get_grouping_policy_side_effect():
            nonlocal init_call_count
            init_call_count += 1
            if init_call_count <= 1:  # First initialization call
                return []  # No roles initially
            else:
                # Users have roles after first call (including system users)
                result = [
                    [user.id, rbac.get_default_role()] for user in mock_users
                ]
                result.append([common.SERVER_ID, 'admin'])
                result.append([constants.SKYPILOT_SYSTEM_USER_ID, 'admin'])
                return result

        def get_roles_for_user_side_effect(user_id):
            # This is called by _add_user_if_not_exists_no_lock
            # Return roles if user has been assigned (tracked in user_roles_map)
            return user_roles_map.get(user_id, [])

        def add_grouping_policy_tracked(*args):
            # Track when roles are assigned
            user_id, role = args
            user_roles_map[user_id] = [role]
            return track_add_grouping_policy(*args)

        mock_enforcer.get_grouping_policy.side_effect = get_grouping_policy_side_effect
        mock_enforcer.get_roles_for_user.side_effect = get_roles_for_user_side_effect
        mock_enforcer.add_grouping_policy.side_effect = add_grouping_policy_tracked

        # Mock plugin RBAC rules
        mock_get_plugin_rules.return_value = {}

        # Mock other dependencies
        mock_get_role_perms.return_value = {
            'user': {
                'permissions': {
                    'blocklist': []
                }
            }
        }
        mock_get_workspace_perms.return_value = {}
        mock_get_users.return_value = mock_users

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

        # Initialize policies multiple times
        service._maybe_initialize_policies()
        service._maybe_initialize_policies()
        service._maybe_initialize_policies()

        # Each user should only be added once (3 mock users + 2 system users)
        expected_calls = {
            (user.id, rbac.get_default_role()) for user in mock_users
        }
        expected_calls.add((common.SERVER_ID, 'admin'))
        expected_calls.add((constants.SKYPILOT_SYSTEM_USER_ID, 'admin'))
        assert len(grouping_policy_calls) == len(expected_calls)

        # Verify each user was added exactly once
        actual_calls = set(grouping_policy_calls)
        assert actual_calls == expected_calls

    @mock.patch('sky.users.permission._policy_lock')
    def test_concurrent_policy_updates_use_lock(self, mock_policy_lock):
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

        # Mock enforcer
        mock_enforcer = mock.Mock()
        mock_enforcer.add_policy.return_value = True
        mock_enforcer.save_policy.return_value = True

        service = permission.PermissionService()
        service.enforcer = mock_enforcer

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
    @mock.patch('sky.users.permission.casbin.Enforcer')
    @mock.patch('sky.users.permission.sqlalchemy_adapter.Adapter')
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
