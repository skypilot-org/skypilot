from typing import Any, Dict, List

from sky import global_user_state
from sky import sky_logging

logger = sky_logging.init_logger(__name__)


def get_workspace_users(workspace_config: Dict[str, Any]) -> List[str]:
    """Get the users that should have access to a workspace.

    Args:
        workspace_config: The configuration of the workspace.

    Returns:
        List of user IDs that should have access to the workspace.
        For private workspaces, returns specific user IDs.
        For public workspaces, returns ['*'] to indicate all users.
    """
    if workspace_config.get('private', False):
        user_ids = []
        user_names = workspace_config.get('allowed_users', [])
        all_users = global_user_state.get_all_users()
        all_users_map = {user.name: user.id for user in all_users}
        for user_name in user_names:
            if user_name not in all_users_map:
                logger.warning(f'User {user_name} not found in all users')
                continue
            user_ids.append(all_users_map[user_name])
        return user_ids
    else:
        # Public workspace - return '*' to indicate all users should have access
        return ['*']