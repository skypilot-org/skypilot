"""Permission service for SkyPilot API Server."""
import os

import casbin
import sqlalchemy_adapter

from sky import global_user_state
from sky import sky_logging
from sky.users import rbac

logger = sky_logging.init_logger(__name__)


class PermissionService:
    """Permission service for SkyPilot API Server."""

    def __init__(self):
        engine = global_user_state._SQLALCHEMY_ENGINE
        adapter = sqlalchemy_adapter.Adapter(engine)
        model_path = os.path.join(os.path.dirname(__file__), 'model.conf')
        enforcer = casbin.Enforcer(model_path, adapter)
        self.enforcer = enforcer

    def init_policies(self):
        """Initialize policies."""
        self.enforcer.clear_policy()
        for role, permissions in rbac.get_role_permissions().items():
            logger.info(f'role: {role}, permissions: {permissions}')
            if permissions['permissions'] and 'blocklist' in permissions[
                    'permissions']:
                blocklist = permissions['permissions']['blocklist']
                for item in blocklist:
                    path = item['path']
                    method = item['method']
                    self.enforcer.add_policy(role, path, method)
                    logger.info(f'add_policy: {role}, {path}, {method}')
            else:
                logger.info(f'no blocklist for role: {role}')
        all_users = global_user_state.get_all_users()
        for user in all_users:
            self.enforcer.add_grouping_policy(user.id, user.role)
        self.enforcer.save_policy()

    def add_role(self, user: str, role: str):
        """Add user role relationship."""
        self.enforcer.add_grouping_policy(user, role)
        self.enforcer.save_policy()

    def remove_role(self, user: str, role: str):
        """Remove user role relationship."""
        self.enforcer.remove_grouping_policy(user, role)
        self.enforcer.save_policy()

    def update_role(self, user: str, old_role: str, new_role: str):
        """Update user role relationship."""
        self.enforcer.remove_grouping_policy(user, old_role)
        self.enforcer.add_grouping_policy(user, new_role)
        self.enforcer.save_policy()

    def check_permission(self, user: str, path: str, method: str) -> bool:
        """Check permission."""
        return self.enforcer.enforce(user, path, method)
