"""Permission service for SkyPilot API Server."""
import contextlib
import hashlib
import logging
import os
import threading
from typing import Generator, List, Optional

import casbin
import filelock
import sqlalchemy_adapter

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.skylet import constants
from sky.users import rbac
from sky.utils import annotations
from sky.utils import common
from sky.utils import common_utils
from sky.utils.db import db_utils

logging.getLogger('casbin.policy').setLevel(sky_logging.ERROR)
logging.getLogger('casbin.role').setLevel(sky_logging.ERROR)
logging.getLogger('casbin.model').setLevel(sky_logging.ERROR)
logging.getLogger('casbin.rbac').setLevel(sky_logging.ERROR)
logger = sky_logging.init_logger(__name__)

# Filelocks for the policy update.
POLICY_UPDATE_LOCK_PATH = os.path.expanduser('~/.sky/.policy_update.lock')
POLICY_UPDATE_LOCK_TIMEOUT_SECONDS = 20

_enforcer_instance: Optional['PermissionService'] = None


class PermissionService:
    """Permission service for SkyPilot API Server."""

    def __init__(self):
        self.enforcer: Optional[casbin.Enforcer] = None
        self._lock = threading.Lock()

    def initialize(self):
        self._lazy_initialize(full_initialize=True)

    def _lazy_initialize(self, full_initialize: bool = False):
        if self.enforcer is not None:
            return
        with self._lock:
            if self.enforcer is not None:
                return
            global _enforcer_instance
            if _enforcer_instance is None:
                engine = global_user_state.initialize_and_get_db()
                if full_initialize:
                    db_utils.add_all_tables_to_db_sqlalchemy(
                        sqlalchemy_adapter.Base.metadata, engine)
                adapter = sqlalchemy_adapter.Adapter(
                    engine, db_class=sqlalchemy_adapter.CasbinRule)
                model_path = os.path.join(os.path.dirname(__file__),
                                          'model.conf')
                enforcer = casbin.Enforcer(model_path, adapter)
                self.enforcer = enforcer
                # Only set the enforcer instance once the enforcer
                # is successfully initialized, if we change it and then fail
                # we will set it to None and all subsequent calls will fail.
                _enforcer_instance = self
                if full_initialize:
                    with _policy_lock():
                        self._maybe_initialize_policies()
                        self._maybe_initialize_basic_auth_user()
            else:
                assert _enforcer_instance is not None
                self.enforcer = _enforcer_instance.enforcer

    def _ensure_enforcer(self) -> casbin.Enforcer:
        """Ensure enforcer is initialized and return it."""
        self._lazy_initialize()
        assert self.enforcer is not None, (
            'Enforcer should be initialized after _lazy_initialize()')
        return self.enforcer

    def _get_plugin_rbac_rules(self):
        """Get RBAC rules from loaded plugins.

        Returns:
            Dictionary of plugin RBAC rules, or empty dict if plugins module
            is not available or no rules are defined.
        """
        try:
            # pylint: disable=import-outside-toplevel
            from sky.server import plugins as server_plugins
            return server_plugins.get_plugin_rbac_rules()
        except ImportError:
            # Plugin module not available (e.g., not running as server)
            logger.debug(
                'Plugin module not available, skipping plugin RBAC rules')
            return {}
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get plugin RBAC rules: {e}')
            return {}

    def _maybe_initialize_basic_auth_user(self) -> None:
        """Initialize basic auth user if it is enabled."""
        basic_auth = os.environ.get(constants.SKYPILOT_INITIAL_BASIC_AUTH)
        if not basic_auth:
            return
        username, password = basic_auth.split(':', 1)
        if username and password:
            user_hash = hashlib.md5(
                username.encode()).hexdigest()[:common_utils.USER_HASH_LENGTH]
            user_info = global_user_state.get_user(user_hash)
            if user_info:
                logger.debug(f'Basic auth user {username} already exists')
                return
            global_user_state.add_or_update_user(
                models.User(id=user_hash, name=username, password=password))
            enforcer = self._ensure_enforcer()
            enforcer.add_grouping_policy(user_hash, rbac.RoleName.ADMIN.value)
            enforcer.save_policy()
            logger.info(f'Basic auth user {username} initialized')

    def _maybe_initialize_policies(self) -> None:
        """Initialize policies if they don't already exist."""
        logger.debug(f'Initializing policies in process: {os.getpid()}')

        policy_updated = False

        # Check if policies are already initialized by looking for existing
        # permission policies in the enforcer
        enforcer = self._ensure_enforcer()
        # Convert existing policies to set of tuples for O(1) lookups
        existing_policies = {tuple(p) for p in enforcer.get_policy()}

        # Get plugin RBAC rules dynamically
        plugin_rules = self._get_plugin_rbac_rules()

        # If we already have policies for the expected roles, skip
        # initialization
        role_permissions = rbac.get_role_permissions(plugin_rules=plugin_rules)
        expected_policies = []
        for role, permissions in role_permissions.items():
            if permissions.get('permissions'
                              ) and 'blocklist' in permissions['permissions']:
                blocklist = permissions['permissions']['blocklist']
                for item in blocklist:
                    expected_policies.append(
                        (role, item['path'], item['method']))

        # Add workspace policy
        workspace_policy_permissions = rbac.get_workspace_policy_permissions()
        logger.debug(f'Workspace policy permissions from config: '
                     f'{workspace_policy_permissions}')

        for workspace_name, users in workspace_policy_permissions.items():
            for user in users:
                expected_policies.append((user, workspace_name, '*'))
        # Check if all expected policies already exist and find missing ones
        missing_policies = [
            p for p in expected_policies if p not in existing_policies
        ]
        # Find policies to remove
        expected_policies_set = set(expected_policies)
        redundant_policies = [
            p for p in existing_policies if p not in expected_policies_set
        ]
        if missing_policies:
            # Add missing policies
            logger.debug(f'Found {len(missing_policies)} missing policies, '
                         'initializing...')
            for p in missing_policies:
                logger.debug(f'Adding policy: {p}')
                enforcer.add_policy(*p)
                policy_updated = True
            logger.debug('Missing policies added successfully')

        if redundant_policies:
            # Remove redundant policies
            logger.debug(f'Found {len(redundant_policies)} redundant policies, '
                         'cleaning up...')
            for p in redundant_policies:
                logger.debug(f'Removing policy: {p}')
                enforcer.remove_policy(*p)
                policy_updated = True
            logger.debug('Redundant policies removed successfully')

        if not missing_policies and not redundant_policies:
            logger.debug('Policies already in sync, skipping initialization')

        # Always ensure users have default roles (this is idempotent)
        # Get users who already have roles (g policies) to avoid redundant calls
        users_with_roles = {tuple(g)[0] for g in enforcer.get_grouping_policy()}
        all_users = global_user_state.get_all_users()
        for existing_user in all_users:
            if str(existing_user.id) not in users_with_roles:
                logger.debug(f'Adding role for user: {existing_user.name}'
                             f'({existing_user.id})')
                user_added = self._add_user_if_not_exists_no_lock(
                    existing_user.id)
                policy_updated = policy_updated or user_added
        for system_user_id in [
                common.SERVER_ID, constants.SKYPILOT_SYSTEM_USER_ID
        ]:
            if system_user_id not in users_with_roles:
                logger.debug(f'Adding role for system user: {system_user_id}')
                user_added = self._add_user_if_not_exists_no_lock(
                    system_user_id, rbac.RoleName.ADMIN.value)
                policy_updated = policy_updated or user_added
        if policy_updated:
            enforcer.save_policy()

    def add_user_if_not_exists(self, user_id: str) -> None:
        """Add user role relationship."""
        self._lazy_initialize()
        with _policy_lock():
            self._add_user_if_not_exists_no_lock(user_id)

    def _add_user_if_not_exists_no_lock(self,
                                        user_id: str,
                                        role: Optional[str] = None) -> bool:
        """Add user role relationship without lock.

        Returns:
            True if the user was added, False otherwise.
        """
        enforcer = self._ensure_enforcer()
        user_roles = enforcer.get_roles_for_user(user_id)
        if not user_roles:
            enforcer.add_grouping_policy(user_id, role or
                                         rbac.get_default_role())
            return True
        return False

    def delete_user(self, user_id: str) -> None:
        """Delete user role relationship."""
        with _policy_lock():
            # Get current roles
            self._load_policy_no_lock()
            # Avoid calling get_user_roles, as it will require the lock.
            enforcer = self._ensure_enforcer()
            current_roles = enforcer.get_roles_for_user(user_id)
            if not current_roles:
                logger.debug(f'User {user_id} has no roles')
                return
            enforcer.remove_grouping_policy(user_id, current_roles[0])
            enforcer.save_policy()

    def update_role(self, user_id: str, new_role: str) -> None:
        """Update user role relationship."""
        with _policy_lock():
            # Get current roles
            self._load_policy_no_lock()
            # Avoid calling get_user_roles, as it will require the lock.
            enforcer = self._ensure_enforcer()
            current_roles = enforcer.get_roles_for_user(user_id)
            if not current_roles:
                logger.debug(f'User {user_id} has no roles')
            else:
                # TODO(hailong): how to handle multiple roles?
                current_role = current_roles[0]
                if current_role == new_role:
                    logger.debug(f'User {user_id} already has role {new_role}')
                    return
                enforcer.remove_grouping_policy(user_id, current_role)

            # Update user role
            enforcer.add_grouping_policy(user_id, new_role)
            enforcer.save_policy()

    def get_user_roles(self, user_id: str) -> List[str]:
        """Get all roles for a user.

        This method returns all roles that the user has, including inherited
        roles. For example, if a user has role 'admin' and 'admin' inherits
        from 'user', this method will return ['admin', 'user'].

        Args:
            user: The user ID to get roles for.

        Returns:
            A list of role names that the user has.
        """
        self._load_policy_no_lock()
        enforcer = self._ensure_enforcer()
        return enforcer.get_roles_for_user(user_id)

    def get_users_for_role(self, role: str) -> List[str]:
        """Get all users for a role."""
        self._load_policy_no_lock()
        enforcer = self._ensure_enforcer()
        return enforcer.get_users_for_role(role)

    def check_endpoint_permission(self, user_id: str, path: str,
                                  method: str) -> bool:
        """Check permission."""
        # We intentionally don't load the policy here, as it is a hot path, and
        # we don't support updating the policy.
        # We don't hold the lock for checking permission, as it is read only and
        # it is a hot path in every request. It is ok to have a stale policy,
        # as long as it is eventually consistent.
        # self._load_policy_no_lock()
        enforcer = self._ensure_enforcer()
        return enforcer.enforce(user_id, path, method)

    def _load_policy_no_lock(self):
        """Load policy from storage."""
        enforcer = self._ensure_enforcer()
        enforcer.load_policy()

    def load_policy(self):
        """Load policy from storage with lock."""
        with _policy_lock():
            self._load_policy_no_lock()

    # Right now, not a lot of users are using multiple workspaces,
    # so 5 should be more than enough.
    @annotations.lru_cache(scope='request', maxsize=5)
    def check_workspace_permission(self, user_id: str,
                                   workspace_name: str) -> bool:
        """Check workspace permission.

        This method checks if a user has permission to access a specific
        workspace.

        For private workspaces, the user must have explicit permission.

        For public workspaces, the permission is granted via a wildcard policy
        ('*').
        """
        if os.getenv(constants.ENV_VAR_IS_SKYPILOT_SERVER) is None:
            # When it is not on API server, we allow all users to access all
            # workspaces, as the workspace check has been done on API server.
            return True
        role = self.get_user_roles(user_id)
        if rbac.RoleName.ADMIN.value in role:
            return True
        # The Casbin model matcher already handles the wildcard '*' case:
        # m = (g(r.sub, p.sub)|| p.sub == '*') && r.obj == p.obj &&
        # r.act == p.act
        # This means if there's a policy ('*', workspace_name, '*'), it will
        # match any user
        enforcer = self._ensure_enforcer()
        result = enforcer.enforce(user_id, workspace_name, '*')
        logger.debug(f'Workspace permission check: user={user_id}, '
                     f'workspace={workspace_name}, result={result}')
        return result

    def check_service_account_token_permission(self, user_id: str,
                                               token_owner_id: str,
                                               action: str) -> bool:
        """Check service account token permission.

        This method checks if a user has permission to perform an action on
        a service account token owned by another user.

        Args:
            user_id: The ID of the user requesting the action
            token_owner_id: The ID of the user who owns the token
            action: The action being performed (e.g., 'delete', 'view')

        Returns:
            True if the user has permission, False otherwise
        """
        del action
        # Users can always manage their own tokens
        if user_id == token_owner_id:
            return True

        # Check if user has admin role (admins can manage any token)
        user_roles = self.get_user_roles(user_id)
        if rbac.RoleName.ADMIN.value in user_roles:
            return True

        # Regular users cannot manage tokens owned by others
        return False

    def add_workspace_policy(self, workspace_name: str,
                             users: List[str]) -> None:
        """Add workspace policy.

        Args:
            workspace_name: Name of the workspace
            users: List of user IDs that should have access.
                   For public workspaces, this should be ['*'].
                   For private workspaces, this should be specific user IDs.
        """
        with _policy_lock():
            enforcer = self._ensure_enforcer()
            for user in users:
                logger.debug(f'Adding workspace policy: user={user}, '
                             f'workspace={workspace_name}')
                enforcer.add_policy(user, workspace_name, '*')
            enforcer.save_policy()

    def update_workspace_policy(self, workspace_name: str,
                                users: List[str]) -> None:
        """Update workspace policy.

        Args:
            workspace_name: Name of the workspace
            users: List of user IDs that should have access.
                   For public workspaces, this should be ['*'].
                   For private workspaces, this should be specific user IDs.
        """
        with _policy_lock():
            self._load_policy_no_lock()
            enforcer = self._ensure_enforcer()
            # Remove all existing policies for this workspace
            enforcer.remove_filtered_policy(1, workspace_name)
            # Add new policies
            for user in users:
                logger.debug(f'Updating workspace policy: user={user}, '
                             f'workspace={workspace_name}')
                enforcer.add_policy(user, workspace_name, '*')
            enforcer.save_policy()

    def remove_workspace_policy(self, workspace_name: str) -> None:
        """Remove workspace policy."""
        with _policy_lock():
            enforcer = self._ensure_enforcer()
            enforcer.remove_filtered_policy(1, workspace_name)
            enforcer.save_policy()


@contextlib.contextmanager
def _policy_lock() -> Generator[None, None, None]:
    """Context manager for policy update lock."""
    try:
        with filelock.FileLock(POLICY_UPDATE_LOCK_PATH,
                               POLICY_UPDATE_LOCK_TIMEOUT_SECONDS):
            yield
    except filelock.Timeout as e:
        raise RuntimeError(f'Failed to reload policy due to a timeout '
                           f'when trying to acquire the lock at '
                           f'{POLICY_UPDATE_LOCK_PATH}. '
                           'Please try again or manually remove the lock '
                           f'file if you believe it is stale.') from e


# Singleton instance of PermissionService for other modules to use.
permission_service = PermissionService()
