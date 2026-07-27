"""Utils for workspaces."""
from typing import Any, Dict, List, Optional, Set

from sky import skypilot_config
from sky.users import resolver as user_resolver
from sky.workspaces import constants as workspace_constants


def is_read_only_for_non_members(workspace_config: Dict[str, Any]) -> bool:
    """Whether non-members may see this workspace read-only.

    True for a private workspace whose effective ``non_member_access`` is
    ``read-only``. The effective value is the workspace's own
    ``non_member_access`` if set, otherwise the org-wide default
    ``workspace_config.non_member_access`` (default ``none``). An open
    (non-private) workspace is usable by everyone, so the flag is moot there
    and this returns False.
    """
    if not workspace_config.get('private', False):
        return False
    access = workspace_config.get('non_member_access')
    if access is None:
        access = skypilot_config.get_nested(
            ('workspace_config', 'non_member_access'),
            default_value=workspace_constants.NON_MEMBER_ACCESS_NONE)
    return access == workspace_constants.NON_MEMBER_ACCESS_READ_ONLY


def get_read_only_workspace_names() -> Set[str]:
    """Names of workspaces that non-members may see read-only.

    Evaluated live from the current config (per-workspace ``non_member_access``,
    falling back to the org-wide ``workspace_config.non_member_access``) at
    permission-check time -- see ``is_read_only_for_non_members`` -- so changes
    take effect without a policy re-sync or restart.
    """
    current_workspaces = skypilot_config.get_nested(('workspaces',),
                                                    default_value={})
    return {
        workspace_name
        for workspace_name, workspace_config in current_workspaces.items()
        if is_read_only_for_non_members(workspace_config)
    }


def is_read_only_workspace(workspace_name: str) -> bool:
    """Whether a single workspace is read-only-visible to non-members.

    Live equivalent of ``workspace_name in get_read_only_workspace_names()``
    that only looks up the one workspace's config.
    """
    current_workspaces = skypilot_config.get_nested(('workspaces',),
                                                    default_value={})
    return is_read_only_for_non_members(
        current_workspaces.get(workspace_name, {}))


def get_workspace_users(
        workspace_config: Dict[str, Any],
        resolver: Optional[user_resolver.UserResolver] = None) -> List[str]:
    """Get the user_ids that should have access to a workspace.

    For private workspaces, resolves ``allowed_users`` (which may contain
    a mix of user_ids and usernames) to user_ids. For public workspaces,
    returns ``['*']``.

    Args:
        workspace_config: Dict with optional ``private: bool`` and
            ``allowed_users: List[str]`` keys.
        resolver: Optional ``UserResolver`` so batch callers don't pay a
            fresh ``get_all_users()`` per workspace. If not provided, a
            transient resolver is built internally.

    Returns:
        List of user IDs. ``['*']`` for public workspaces.
    """
    if resolver is None:
        resolver = user_resolver.UserResolver()
    return resolver.resolve_workspace_users(workspace_config)
