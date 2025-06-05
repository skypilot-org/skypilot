"""RBAC (Role-Based Access Control) functionality for SkyPilot API Server."""

import enum
from typing import Dict, List, Optional

from sky import sky_logging
from sky import skypilot_config
from sky.skylet import constants
from sky.workspaces import core as workspaces_core

logger = sky_logging.init_logger(__name__)

# Default user blocklist for user role
# Cannot access workspace CUD operations
_DEFAULT_USER_BLOCKLIST = [{
    'path': '/workspaces/config',
    'method': 'POST'
}, {
    'path': '/workspaces/update',
    'method': 'POST'
}, {
    'path': '/workspaces/create',
    'method': 'POST'
}, {
    'path': '/workspaces/delete',
    'method': 'POST'
}, {
    'path': '/users/update',
    'method': 'POST'
}]


# Define roles
class RoleName(str, enum.Enum):
    ADMIN = 'admin'
    USER = 'user'


def get_supported_roles() -> List[str]:
    return [role_name.value for role_name in RoleName]


def get_default_role() -> str:
    return skypilot_config.get_nested(('rbac', 'default_role'),
                                      default_value=RoleName.ADMIN.value)


def get_role_permissions(
) -> Dict[str, Dict[str, Dict[str, List[Dict[str, str]]]]]:
    """Get all role permissions from config.

    Returns:
        Dictionary containing all roles and their permissions configuration.
        Example:
        {
            'admin': {
                'permissions': {
                    'blocklist': []
                }
            },
            'user': {
                'permissions': {
                    'blocklist': [
                        {'path': '/workspaces/config', 'method': 'POST'},
                        {'path': '/workspaces/update', 'method': 'POST'}
                    ]
                }
            }
        }
    """
    # Get all roles from the config
    config_permissions = skypilot_config.get_nested(('rbac', 'roles'),
                                                    default_value={})
    supported_roles = get_supported_roles()
    for role, permissions in config_permissions.items():
        role_name = role.lower()
        if role_name not in supported_roles:
            logger.warning(f'Invalid role: {role_name}')
            continue
        config_permissions[role_name] = permissions
    # Add default roles if not present
    if 'user' not in config_permissions:
        config_permissions['user'] = {
            'permissions': {
                'blocklist': _DEFAULT_USER_BLOCKLIST
            }
        }
    return config_permissions


def get_workspace_policy_permissions(
        workspace_name: Optional[str] = None) -> Dict[str, List[str]]:
    """Get workspace policy permissions from config.

    Args:
        workspace_name: The name of the workspace to get the policy permissions
            for. If None, return all workspace policy permissions.

    Returns:
        A dictionary of workspace policy permissions.
        Example:
        {
            'workspace1': ['user1', 'user2'],
            'workspace2': ['user3', 'user4']
        }
    """
    current_workspaces = workspaces_core.get_workspaces()
    workspaces_to_policy = {}
    for workspace_name, workspace_config in current_workspaces.items():
        if workspace_config.get('private', False):
            # This only list out the users explicitly allowed.
            # Admin users should be automatically included during checks.
            users = workspace_config.get('allowed_users', [])
            workspaces_to_policy[workspace_name] = users
        else:
            workspaces_to_policy[workspace_name] = ['*']
    if constants.SKYPILOT_DEFAULT_WORKSPACE not in workspaces_to_policy:
        workspaces_to_policy[constants.SKYPILOT_DEFAULT_WORKSPACE] = ['*']
    logger.debug(f'Workspace policy permissions: {workspaces_to_policy}')
    return workspaces_to_policy
