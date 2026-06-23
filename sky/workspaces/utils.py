"""Utils for workspaces."""
from typing import Any, Dict, List, Optional

from sky.users import resolver as user_resolver


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
