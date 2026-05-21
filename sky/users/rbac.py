"""RBAC (Role-Based Access Control) functionality for SkyPilot API Server."""

import enum
from typing import Dict, List, Optional

from sky import sky_logging
from sky import skypilot_config
from sky.skylet import constants
from sky.workspaces import utils as workspaces_utils

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
    'path': '/users/delete',
    'method': 'POST'
}, {
    'path': '/users/create',
    'method': 'POST'
}, {
    'path': '/users/import',
    'method': 'POST'
}, {
    'path': '/users/export',
    'method': 'GET'
}, {
    'path': '/debug/dump_create',
    'method': 'POST'
}, {
    'path': '/debug/dump_download/:filename',
    'method': 'GET'
}]

# Default allowlist for the viewer role. Viewer is allowlist-based: any
# endpoint NOT on this list is denied for viewers, including any new
# endpoints added in the future.
_DEFAULT_VIEWER_ALLOWLIST = [
    # --- Authentication / session ---
    {
        'path': '/users/role',
        'method': 'GET'
    },
    {
        'path': '/token',
        'method': 'GET'
    },
    {
        'path': '/auth/authorize',
        'method': 'GET'
    },
    # --- Cluster reads ---
    {
        'path': '/status',
        'method': 'POST'
    },
    {
        'path': '/endpoints',
        'method': 'POST'
    },
    {
        'path': '/cost_report',
        'method': 'POST'
    },
    {
        'path': '/cluster_events',
        'method': 'POST'
    },
    {
        'path': '/queue',
        'method': 'POST'
    },
    {
        'path': '/job_status',
        'method': 'POST'
    },
    {
        'path': '/logs',
        'method': 'POST'
    },
    {
        'path': '/download_logs',
        'method': 'POST'
    },
    {
        'path': '/download',
        'method': 'POST'
    },
    {
        'path': '/provision_logs',
        'method': 'POST'
    },
    {
        'path': '/autostop_logs',
        'method': 'POST'
    },
    # --- Managed jobs reads ---
    {
        'path': '/jobs/queue',
        'method': 'POST'
    },
    {
        'path': '/jobs/queue/v2',
        'method': 'POST'
    },
    {
        'path': '/jobs/wait',
        'method': 'POST'
    },
    {
        'path': '/jobs/logs',
        'method': 'POST'
    },
    {
        'path': '/jobs/download_logs',
        'method': 'POST'
    },
    {
        'path': '/jobs/pool_status',
        'method': 'POST'
    },
    {
        'path': '/jobs/pool_logs',
        'method': 'POST'
    },
    {
        'path': '/jobs/pool_sync-down-logs',
        'method': 'POST'
    },
    {
        'path': '/jobs/events',
        'method': 'POST'
    },
    # --- SkyServe reads ---
    {
        'path': '/serve/status',
        'method': 'POST'
    },
    {
        'path': '/serve/logs',
        'method': 'POST'
    },
    {
        'path': '/serve/sync-down-logs',
        'method': 'POST'
    },
    # --- Workspaces / volumes / recipes reads ---
    # NOTE: /workspaces/config GET intentionally NOT on allowlist;
    # it returns the entire admin config including provider tokens.
    {
        'path': '/workspaces',
        'method': 'GET'
    },
    {
        'path': '/volumes',
        'method': 'GET'
    },
    {
        'path': '/volumes/validate',
        'method': 'POST'
    },
    {
        'path': '/recipes',
        'method': 'GET'
    },
    {
        'path': '/recipes/list',
        'method': 'POST'
    },
    {
        'path': '/recipes/get',
        'method': 'POST'
    },
    # --- SSH node pool reads ---
    # NOTE: /ssh_node_pools/keys GET NOT on allowlist (key paths).
    {
        'path': '/ssh_node_pools',
        'method': 'GET'
    },
    {
        'path': '/ssh_node_pools/:pool_name/status',
        'method': 'GET'
    },
    # --- Infra info (read-only catalog/state) ---
    {
        'path': '/enabled_clouds',
        'method': 'GET'
    },
    {
        'path': '/enabled_clouds/batch',
        'method': 'GET'
    },
    {
        'path': '/realtime_kubernetes_gpu_availability',
        'method': 'POST'
    },
    {
        'path': '/kubernetes_node_info',
        'method': 'POST'
    },
    {
        'path': '/slurm_gpu_availability',
        'method': 'POST'
    },
    {
        'path': '/slurm_node_info',
        'method': 'GET'
    },
    {
        'path': '/slurm_node_info',
        'method': 'POST'
    },
    {
        'path': '/status_kubernetes',
        'method': 'GET'
    },
    {
        'path': '/all_contexts',
        'method': 'GET'
    },
    {
        'path': '/list_accelerators',
        'method': 'POST'
    },
    {
        'path': '/list_accelerator_counts',
        'method': 'POST'
    },
    {
        'path': '/validate',
        'method': 'POST'
    },
    {
        'path': '/optimize',
        'method': 'POST'
    },
    # --- Users / SA tokens (read-only) ---
    # /users GET returns name/role only (no password hashes, no
    # secrets); see sky/users/server.py:60.
    {
        'path': '/users',
        'method': 'GET'
    },
    # /users/service-account-tokens GET returns metadata only (no JWT
    # values). POST and sub-paths (delete/update-role/rotate) are
    # intentionally NOT allowlisted; viewers cannot mint, rotate, or
    # modify tokens.  `check_service_account_token_permission` adds
    # defense-in-depth.
    {
        'path': '/users/service-account-tokens',
        'method': 'GET'
    },
    # --- Storage reads ---
    {
        'path': '/storage/ls',
        'method': 'GET'
    },
    # /upload_v2/blob GET is a pure existence-check (blob_exists);
    # see sky/server/server.py:1618.
    {
        'path': '/upload_v2/blob',
        'method': 'GET'
    },
    # /debug/dump_create is a diagnostic snapshot — operators (including
    # viewers) need it to collect dumps for support. The corresponding
    # download (`/debug/dump_download/:filename`) is intentionally NOT
    # allowlisted: dumps can contain user-scoped data, so reads stay
    # admin-only via the user-role blocklist semantics.
    {
        'path': '/debug/dump_create',
        'method': 'POST'
    },
    # --- Dashboard / static / auth-flow surface ---
    # These paths are usually RBAC-skipped at the middleware level
    # (`/dashboard/`, `/api/` prefix in server.py:200) but are listed
    # here so the route-coverage test in
    # tests/unit_tests/test_sky/users/test_viewer_route_coverage.py
    # treats them as a deliberate allow rather than an
    # un-categorized route.
    {
        'path': '/dashboard/*',
        'method': 'GET'
    },
    {
        'path': '/api/v1/auth/token',
        'method': 'GET'
    },
    {
        'path': '/api/plugins',
        'method': 'GET'
    },
]


# Define roles
class RoleName(str, enum.Enum):
    ADMIN = 'admin'
    USER = 'user'
    # Strictly read-only role.  Allowlist-based (see
    # `_DEFAULT_VIEWER_ALLOWLIST`); not currently used by anything by
    # default — operators opt in by setting
    # `rbac.default_role: viewer` or by assigning specific users to
    # this role.
    VIEWER = 'viewer'


def get_supported_roles() -> List[str]:
    return [role_name.value for role_name in RoleName]


def get_default_role() -> str:
    return skypilot_config.get_nested(('rbac', 'default_role'),
                                      default_value=RoleName.ADMIN.value)


def get_viewer_allowlist(
    plugin_allowlist: Optional[List[Dict[str, str]]] = None
) -> List[Dict[str, str]]:
    """Get the viewer role's endpoint allowlist.

    Composed of:
      1. `_DEFAULT_VIEWER_ALLOWLIST` (this module's constant).
      2. Any rules under
         `rbac.roles.viewer.permissions.allowlist` in
         `~/.sky/config.yaml` (additive — operator-supplied entries
         augment the default, they do not replace it).
      3. Plugin-supplied rules from `BasePlugin.viewer_allowlist`.

    Args:
        plugin_allowlist: Optional list of `{path, method}` records
            collected from loaded plugins.

    Returns:
        Combined list of `{path, method}` records.  Each record uses
        Casbin `keyMatch2` path syntax (e.g. `/users/:name`, `/x/*`).
    """
    combined: List[Dict[str, str]] = list(_DEFAULT_VIEWER_ALLOWLIST)

    config_roles = skypilot_config.get_nested(('rbac', 'roles'),
                                              default_value={})
    viewer_cfg = config_roles.get(RoleName.VIEWER.value, {}) if isinstance(
        config_roles, dict) else {}
    extra = ((viewer_cfg.get('permissions') or {}).get('allowlist') or
             []) if isinstance(viewer_cfg, dict) else []
    for rule in extra:
        if not isinstance(rule, dict):
            logger.warning(f'Invalid viewer allowlist entry (not a dict): '
                           f'{rule}')
            continue
        if 'path' not in rule or 'method' not in rule:
            logger.warning(f'Viewer allowlist entry missing path/method: '
                           f'{rule}')
            continue
        entry = {'path': rule['path'], 'method': rule['method']}
        if entry not in combined:
            combined.append(entry)

    if plugin_allowlist:
        for rule in plugin_allowlist:
            entry = {'path': rule['path'], 'method': rule['method']}
            if entry not in combined:
                combined.append(entry)

    return combined


def get_role_permissions(
    plugin_rules: Optional[Dict[str, List[Dict[str, str]]]] = None
) -> Dict[str, Dict[str, Dict[str, List[Dict[str, str]]]]]:
    """Get all role permissions from config and plugins.

    Args:
        plugin_rules: Optional dictionary of plugin RBAC rules to merge.
                     Format: {'user': [{'path': '...', 'method': '...'}]}

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
                'blocklist': _DEFAULT_USER_BLOCKLIST.copy()
            }
        }

    # Merge plugin rules into the appropriate roles
    if plugin_rules:
        for role, rules in plugin_rules.items():
            if role not in supported_roles:
                logger.warning(f'Plugin specified invalid role: {role}')
                continue
            if role not in config_permissions:
                config_permissions[role] = {'permissions': {'blocklist': []}}
            if 'permissions' not in config_permissions[role]:
                config_permissions[role]['permissions'] = {'blocklist': []}
            if 'blocklist' not in config_permissions[role]['permissions']:
                config_permissions[role]['permissions']['blocklist'] = []

            # Merge plugin rules, avoiding duplicates
            existing_rules = config_permissions[role]['permissions'][
                'blocklist']
            for rule in rules:
                if rule not in existing_rules:
                    existing_rules.append(rule)
                    logger.debug(f'Added plugin RBAC rule for {role}: '
                                 f'{rule["method"]} {rule["path"]}')

    return config_permissions


def get_workspace_policy_permissions() -> Dict[str, List[str]]:
    """Get workspace policy permissions from config.

    Returns:
        A dictionary of workspace policy permissions.
        Example:
        {
            'workspace1': ['user1-id', 'user2-id'],
            'workspace2': ['user3-id', 'user4-id']
            'default': ['*']
        }
    """
    current_workspaces = skypilot_config.get_nested(('workspaces',),
                                                    default_value={})
    if constants.SKYPILOT_DEFAULT_WORKSPACE not in current_workspaces:
        current_workspaces[constants.SKYPILOT_DEFAULT_WORKSPACE] = {}
    workspaces_to_policy = {}
    for workspace_name, workspace_config in current_workspaces.items():
        users = workspaces_utils.get_workspace_users(workspace_config)
        workspaces_to_policy[workspace_name] = users
    logger.debug(f'Workspace policy permissions: {workspaces_to_policy}')
    return workspaces_to_policy
