"""Utils for workspaces."""
import collections
from typing import Any, Dict, List, Optional

from sky import global_user_state
from sky import models
from sky import sky_logging

logger = sky_logging.init_logger(__name__)


def build_username_to_ids_map(
        all_users: List[models.User]) -> Dict[str, List[str]]:
    """Build a name -> [user_id] map. Exposed so batch callers can compute it
    once and pass it to ``preferred_identifier_for_user`` /
    ``entries_for_user`` per user, instead of rebuilding it on every call
    (each rebuild is O(len(all_users))).
    """
    name_to_ids: Dict[str, List[str]] = collections.defaultdict(list)
    for user in all_users:
        if user.name:
            name_to_ids[user.name].append(user.id)
    return name_to_ids


def build_user_id_to_user_map(
        all_users: List[models.User]) -> Dict[str, models.User]:
    """Build a user_id -> User map. Exposed so batch callers can avoid the
    O(len(all_users)) linear scan inside ``preferred_identifier_for_user`` /
    ``entries_for_user``.
    """
    return {user.id: user for user in all_users}


def _resolve_user_info(
        user_id: str, all_users: Optional[List[models.User]],
        user_id_to_user: Optional[Dict[str,
                                       models.User]]) -> Optional[models.User]:
    if user_id_to_user is not None:
        return user_id_to_user.get(user_id)
    # Defensive: a future caller could pass both args as None.
    if all_users is None:
        return None
    for user in all_users:
        if user.id == user_id:
            return user
    return None


def preferred_identifier_for_user(
        user_id: str,
        all_users: Optional[List[models.User]] = None,
        name_to_ids: Optional[Dict[str, List[str]]] = None,
        user_id_to_user: Optional[Dict[str,
                                       models.User]] = None) -> Optional[str]:
    """Return the preferred ``allowed_users`` entry for a given user_id.

    Prefers the username when it uniquely resolves back to ``user_id``;
    otherwise falls back to ``user_id``. Returns ``None`` if no user exists
    for the given id.

    ``name_to_ids`` and ``user_id_to_user`` are optional pre-built maps
    (see ``build_username_to_ids_map`` / ``build_user_id_to_user_map``) so
    batch callers can avoid the O(M) scan + O(M) map rebuild on every call.
    """
    if all_users is None:
        all_users = global_user_state.get_all_users()
    user_info = _resolve_user_info(user_id, all_users, user_id_to_user)
    if user_info is None:
        return None
    if not user_info.name:
        return user_id
    if name_to_ids is None:
        name_to_ids = build_username_to_ids_map(all_users)
    if len(name_to_ids.get(user_info.name, [])) == 1:
        return user_info.name
    return user_id


def entries_for_user(
        user_id: str,
        all_users: Optional[List[models.User]] = None,
        name_to_ids: Optional[Dict[str, List[str]]] = None,
        user_id_to_user: Optional[Dict[str, models.User]] = None) -> List[str]:
    """Return all ``allowed_users`` entries that resolve to ``user_id``.

    Includes both the user_id itself and the username (when unique). Used to
    strip every form of a user from a workspace's ``allowed_users`` list.

    ``name_to_ids`` and ``user_id_to_user`` are optional pre-built maps
    for batch callers; see ``preferred_identifier_for_user``.
    """
    if all_users is None:
        all_users = global_user_state.get_all_users()
    user_info = _resolve_user_info(user_id, all_users, user_id_to_user)
    if user_info is None:
        return [user_id]
    entries = [user_id]
    if user_info.name:
        if name_to_ids is None:
            name_to_ids = build_username_to_ids_map(all_users)
        if len(name_to_ids.get(user_info.name, [])) == 1:
            entries.append(user_info.name)
    return entries


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
