"""Utils for workspaces."""
import collections
from typing import Any, Dict, List

from sky import global_user_state
from sky import sky_logging

logger = sky_logging.init_logger(__name__)


def get_workspace_users(workspace_config: Dict[str, Any]) -> List[str]:
    """Get the users that should have access to a workspace.

    workspace_config is a dict with the following keys:
    - private: bool
    - allowed_users: list of user names or IDs

    This function will automatically resolve the user names to IDs.

    Args:
        workspace_config: The configuration of the workspace.

    Returns:
        List of user IDs that should have access to the workspace.
        For private workspaces, returns specific user IDs.
        For public workspaces, returns ['*'] to indicate all users.
    """
    if workspace_config.get('private', False):
        user_ids = []
        workspace_user_name_or_ids = workspace_config.get('allowed_users', [])
        all_users = global_user_state.get_all_users()
        all_user_ids = {user.id for user in all_users}
        all_user_map = collections.defaultdict(list)
        for user in all_users:
            all_user_map[user.name].append(user.id)

        # Resolve user names to IDs
        for user_name_or_id in workspace_user_name_or_ids:
            if user_name_or_id in all_user_ids:
                user_ids.append(user_name_or_id)
            elif user_name_or_id in all_user_map:
                if len(all_user_map[user_name_or_id]) > 1:
                    user_ids_str = ', '.join(all_user_map[user_name_or_id])
                    raise ValueError(
                        f'User {user_name_or_id!r} has multiple IDs: '
                        f'{user_ids_str}. Please specify the user '
                        f'ID instead.')
                user_ids.append(all_user_map[user_name_or_id][0])
            else:
                logger.warning(
                    f'User {user_name_or_id!r} not found in all users')
                continue
        return user_ids
    else:
        # Public workspace - return '*' to indicate all users should have access
        return ['*']
