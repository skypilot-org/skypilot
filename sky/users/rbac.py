"""RBAC (Role-Based Access Control) functionality for SkyPilot API Server."""

import enum
from typing import Dict, List

from sky import sky_logging
from sky import skypilot_config

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
