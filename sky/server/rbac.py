"""RBAC (Role-Based Access Control) functionality for SkyPilot API Server."""

import enum
from typing import Dict, List

from sky import sky_logging
from sky import skypilot_config

logger = sky_logging.init_logger(__name__)

# Default user black list for user role
# Cannot access workspace CUD operations
_DEFAULT_USER_BLACK_LIST = [{
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


def get_role_permissions(
) -> Dict[str, Dict[str, Dict[str, List[Dict[str, str]]]]]:
    config_permissions = skypilot_config.get_role_permissions()
    supported_roles = [role_name.value for role_name in RoleName]
    for role_name in config_permissions:
        if role_name not in supported_roles:
            raise ValueError(f'Invalid role: {role_name}')
    # Add default roles if not present
    if 'admin' not in config_permissions:
        config_permissions['admin'] = {'permissions': {'black_list': []}}
    if 'user' not in config_permissions:
        config_permissions['user'] = {
            'permissions': {
                'black_list': _DEFAULT_USER_BLACK_LIST
            }
        }
    return config_permissions
