"""Permission service for SkyPilot API Server."""
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

_instance = None
_lock = threading.Lock()


class PermissionService:
    """Permission service for SkyPilot API Server."""

    def __init__(self):
        global _instance
        if _instance is None:
            with _lock:
                if _instance is None:
                    _instance = self
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
            self.enforcer = _instance.enforcer

    def init_policies(self):
        """Initialize policies."""
        logger.debug(f'Initializing policies in process: {os.getpid()}')
        # Only clear p policies (permission policies),
        # keep g policies (role policies)
        self.enforcer.remove_filtered_policy(0)
        for role, permissions in rbac.get_role_permissions().items():
            if permissions['permissions'] and 'blocklist' in permissions[
                    'permissions']:
                blocklist = permissions['permissions']['blocklist']
                for item in blocklist:
                    path = item['path']
                    method = item['method']
                    self.enforcer.add_policy(role, path, method)
        all_users = global_user_state.get_all_users()
        for user in all_users:
            user_roles = self.get_user_roles(user.id)
            if len(user_roles) == 0:
                logger.info(f'User {user.id} has no roles, adding'
                            f' default role {rbac.get_default_role()}')
                self.enforcer.add_grouping_policy(user.id,
                                                  rbac.get_default_role())
        self.enforcer.save_policy()

    def add_role(self, user: str, role: str):
        """Add user role relationship."""
        self.enforcer.add_grouping_policy(user, role)
        self.enforcer.save_policy()

    def remove_role(self, user: str, role: str):
        """Remove user role relationship."""
        self.enforcer.remove_grouping_policy(user, role)
        self.enforcer.save_policy()

    def update_role(self, user: str, new_role: str):
        """Update user role relationship."""
        try:
            with filelock.FileLock(POLICY_UPDATE_LOCK_PATH,
                                   POLICY_UPDATE_LOCK_TIMEOUT_SECONDS):
                # Get current roles
                self.load_policy()
                current_roles = self.get_user_roles(user)
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
        except filelock.Timeout as e:
            raise RuntimeError(f'Failed to update role due to a timeout '
                               f'when trying to acquire the lock at '
                               f'{POLICY_UPDATE_LOCK_PATH}. '
                               'Please try again or manually remove the lock '
                               f'file if you believe it is stale.') from e

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
        return self.enforcer.get_roles_for_user(user)

    def check_permission(self, user: str, path: str, method: str) -> bool:
        """Check permission."""
        return self.enforcer.enforce(user, path, method)

    def load_policy(self):
        """Load policy from storage."""
        self.enforcer.load_policy()
