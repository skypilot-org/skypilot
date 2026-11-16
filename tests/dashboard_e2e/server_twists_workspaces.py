"""Server twists for the workspaces tests (see framework/server_plugin.py).

Each one makes the server misbehave the way a backend regression would, for
the one test whose requests carry its id.
"""
import os

from framework.server_plugin import register_body_twist
import yaml

_JOBS_QUEUE = '/jobs/queue/v2'


def _strip_config_keys(body, keys):
    config = {
        k: v for k, v in (body.get('config') or {}).items() if k not in keys
    }
    return {**body, 'config': config}


def _delete_by_name(body):
    """The delete handler is handed the workspace's display name instead of
    its id, as if the id/name mapping were reversed."""
    # Read the config file: this process's in-memory config may predate the
    # workspace, which an executor process created.
    workspace = body.get('workspace_name')
    with open(os.environ['SKYPILOT_GLOBAL_CONFIG'], encoding='utf-8') as f:
        workspaces = (yaml.safe_load(f) or {}).get('workspaces') or {}
    name = (workspaces.get(workspace) or {}).get('name')
    if name:
        body = {**body, 'workspace_name': name}
    return body


# The cluster summary ignores "all users" and returns only the caller's own
# clusters, so per-workspace counts miss everyone else's.
register_body_twist(
    'srv-workspaces-status-own-clusters-only', '/status', lambda b: {
        **b, 'all_users': False
    } if 'summary_response' in b else b)

# The managed-jobs summary (no paging) ignores "all users", so per-workspace
# job counts miss everyone else's jobs.
register_body_twist(
    'srv-workspaces-jobs-own-only', _JOBS_QUEUE, lambda b: {
        **b, 'all_users': False
    } if 'page' not in b else b)

# The jobs queue ignores the workspace filter.
register_body_twist(
    'srv-workspaces-jobs-ignore-workspace-match', _JOBS_QUEUE,
    lambda b: {k: v for k, v in b.items() if k != 'workspace_match'})

# Creating a workspace drops its access settings: it is stored public.
register_body_twist(
    'srv-workspaces-create-drops-access', '/workspaces/create',
    lambda b: _strip_config_keys(b, ('private', 'allowed_users')))

# Updating a workspace drops `private` from the new config.
register_body_twist('srv-workspaces-update-drops-private', '/workspaces/update',
                    lambda b: _strip_config_keys(b, ('private',)))

register_body_twist('srv-workspaces-delete-by-name', '/workspaces/delete',
                    _delete_by_name)
