"""Permission service for SkyPilot API Server."""
import contextlib
import logging
import os
import threading
from typing import List

import casbin
import filelock
import sqlalchemy_adapter

from sky import global_user_state
from sky import sky_logging
from sky.users import rbac

logger = sky_logging.init_logger(__name__)

# Filelocks for the policy update.
POLICY_UPDATE_LOCK_PATH = os.path.expanduser('~/.sky/.policy_update.lock')
POLICY_UPDATE_LOCK_TIMEOUT_SECONDS = 20

_enforcer_instance = None
_lock = threading.Lock()


class PermissionService:
    """Permission service for SkyPilot API Server."""

    def __init__(self):
        global _enforcer_instance
        if _enforcer_instance is None:
            # For different threads, we share the same enforcer instance.
            with _lock:
                if _enforcer_instance is None:
                    _enforcer_instance = self
                    engine = global_user_state.SQLALCHEMY_ENGINE
                    adapter = sqlalchemy_adapter.Adapter(engine)
                    model_path = os.path.join(os.path.dirname(__file__),
                                              'model.conf')
                    enforcer = casbin.Enforcer(model_path, adapter)
                    logging.getLogger('casbin.policy').setLevel(
                        sky_logging.ERROR)
                    logging.getLogger('casbin.role').setLevel(sky_logging.ERROR)
                    self.enforcer = enforcer
        else:
            self.enforcer = _enforcer_instance.enforcer
        self._maybe_initialize_policies()

    def _maybe_initialize_policies(self):
        """Initialize policies if they don't already exist."""
        logger.debug(f'Initializing policies in process: {os.getpid()}')

        # Check if policies are already initialized by looking for existing
        # permission policies in the enforcer
        existing_policies = self.enforcer.get_policy()

        # If we already have policies for the expected roles, skip
        # initialization
        role_permissions = rbac.get_role_permissions()
        expected_policies = []
        for role, permissions in role_permissions.items():
            if permissions['permissions'] and 'blocklist' in permissions[
                    'permissions']:
                blocklist = permissions['permissions']['blocklist']
                for item in blocklist:
                    expected_policies.append(
                        [role, item['path'], item['method']])

        # Check if all expected policies already exist
        policies_exist = all(
            any(policy == expected
                for policy in existing_policies)
            for expected in expected_policies)

        if not policies_exist:
            # Only clear and reinitialize if policies don't exist or are
            # incomplete
            logger.debug('Policies not found or incomplete, initializing...')
            # Only clear p policies (permission policies),
            # keep g policies (role policies)
            self.enforcer.remove_filtered_policy(0)
            for role, permissions in role_permissions.items():
                if permissions['permissions'] and 'blocklist' in permissions[
                        'permissions']:
                    blocklist = permissions['permissions']['blocklist']
                    for item in blocklist:
                        path = item['path']
                        method = item['method']
                        self.enforcer.add_policy(role, path, method)
            self.enforcer.save_policy()
        else:
            logger.debug('Policies already exist, skipping initialization')

        # Always ensure users have default roles (this is idempotent)
        all_users = global_user_state.get_all_users()
        for user in all_users:
            self.add_user_if_not_exists(user.id)

    def add_user_if_not_exists(self, user: str) -> None:
        """Add user role relationship."""
        with _policy_lock():
            user_roles = self.enforcer.get_roles_for_user(user)
            if not user_roles:
                logger.info(f'User {user} has no roles, adding'
                            f' default role {rbac.get_default_role()}')
                self.enforcer.add_grouping_policy(user, rbac.get_default_role())
                self.enforcer.save_policy()

    def update_role(self, user: str, new_role: str):
        """Update user role relationship."""
        with _policy_lock():
            # Get current roles
            self._load_policy_no_lock()
            # Avoid calling get_user_roles, as it will require the lock.
            current_roles = self.enforcer.get_roles_for_user(user)
            if not current_roles:
                logger.warning(f'User {user} has no roles')
            else:
                # TODO(hailong): how to handle multiple roles?
                current_role = current_roles[0]
                if current_role == new_role:
                    logger.info(f'User {user} already has role {new_role}')
                    return
                self.enforcer.remove_grouping_policy(user, current_role)

            # Update user role
            self.enforcer.add_grouping_policy(user, new_role)
            self.enforcer.save_policy()

    def get_user_roles(self, user: str) -> List[str]:
        """Get all roles for a user.

        This method returns all roles that the user has, including inherited
        roles. For example, if a user has role 'admin' and 'admin' inherits
        from 'user', this method will return ['admin', 'user'].

        Args:
            user: The user ID to get roles for.

        Returns:
            A list of role names that the user has.
        """
        self._load_policy()
        return self.enforcer.get_roles_for_user(user)

    def check_permission(self, user: str, path: str, method: str) -> bool:
        """Check permission."""
        # We intentionally don't load the policy here, as it is a hot path, and
        # we don't support updating the policy.
        # We don't hold the lock for checking permission, as it is read only and
        # it is a hot path in every request. It is ok to have a stale policy,
        # as long as it is eventually consistent.
        # self._load_policy_no_lock()
        return self.enforcer.enforce(user, path, method)

    def _load_policy_no_lock(self):
        """Load policy from storage."""
        self.enforcer.load_policy()

    def _load_policy(self):
        """Load policy from storage with lock."""
        with _policy_lock():
            self._load_policy_no_lock()


@contextlib.contextmanager
def _policy_lock():
    """Context manager for policy update lock."""
    try:
        with filelock.FileLock(POLICY_UPDATE_LOCK_PATH,
                               POLICY_UPDATE_LOCK_TIMEOUT_SECONDS):
            yield
    except filelock.Timeout as e:
        raise RuntimeError(f'Failed to load policy due to a timeout '
                           f'when trying to acquire the lock at '
                           f'{POLICY_UPDATE_LOCK_PATH}. '
                           'Please try again or manually remove the lock '
                           f'file if you believe it is stale.') from e
