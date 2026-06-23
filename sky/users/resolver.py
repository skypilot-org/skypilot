"""User-resolution context shared across batch operations.

The ``UserResolver`` class caches a snapshot of the user table plus the
derived id->User and name->[ids] lookup maps so per-user calls in a
batch loop are O(1) instead of rebuilding the maps on every invocation
or re-fetching ``get_all_users()`` from the DB.

Although several methods deal with the workspace ``allowed_users``
schema (a mix of user_ids and usernames), the resolver itself only
operates on plain ``Dict[str, Any]`` workspace configs, so it does not
introduce a dependency on the ``sky.workspaces`` package.
"""
import collections
from typing import Any, Dict, Iterable, List, Optional, Set

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.users import permission
from sky.users import rbac

logger = sky_logging.init_logger(__name__)


class UserResolver:
    """User-resolution context shared across a batch operation.

    Wraps the user list and its derived lookup maps (id -> User, name ->
    [ids]) plus a lazily-fetched admin user_id set. Batch callers should
    build a ``UserResolver`` once at the top of the operation and pass
    it through helpers / per-item loops so each per-user lookup is O(1)
    instead of rebuilding the maps (or re-fetching ``get_all_users()``)
    on every call.

    Single-call callers can just call helper free-functions like
    ``sky.workspaces.utils.get_workspace_users(config)``; those
    construct a transient resolver internally.

    The resolver is read-only after construction; callers that need to
    operate against a different user snapshot should build a new
    resolver.
    """

    def __init__(
        self,
        all_users: Optional[List[models.User]] = None,
        admin_user_ids: Optional[List[str]] = None,
    ):
        if all_users is None:
            all_users = global_user_state.get_all_users()
        self._all_users = all_users
        self._id_to_user: Dict[str, models.User] = {u.id: u for u in all_users}
        self._name_to_ids: Dict[str, List[str]] = collections.defaultdict(list)
        for u in all_users:
            if u.name:
                self._name_to_ids[u.name].append(u.id)
        # Lazy: many callers never need the admin set; only fetch on first
        # access.
        self._admin_user_ids = admin_user_ids

    @property
    def admin_user_ids(self) -> List[str]:
        """User IDs currently assigned the admin role.

        Lazily fetched from the casbin policy on first access so callers
        that never need it don't pay the DB round-trip.
        """
        if self._admin_user_ids is None:
            self._admin_user_ids = list(
                permission.permission_service.get_users_for_role(
                    rbac.RoleName.ADMIN.value))
        return self._admin_user_ids

    @property
    def id_to_user(self) -> Dict[str, models.User]:
        """Mapping from user_id -> ``User``. Read-only; do not mutate."""
        return self._id_to_user

    def get_user(self, user_id: str) -> Optional[models.User]:
        """Return the ``User`` for ``user_id`` or ``None`` if not present."""
        return self._id_to_user.get(user_id)

    def has_id(self, user_id: str) -> bool:
        return user_id in self._id_to_user

    def preferred_entry(self, user_id: str) -> Optional[str]:
        """Return the preferred ``allowed_users`` entry for a user_id.

        Prefers the username when it uniquely resolves back to
        ``user_id``; otherwise falls back to ``user_id``. Returns
        ``None`` if no user exists for the given id.
        """
        user = self._id_to_user.get(user_id)
        if user is None:
            return None
        if user.name and len(self._name_to_ids.get(user.name, [])) == 1:
            return user.name
        return user_id

    def entries_for(self, user_id: str) -> List[str]:
        """Return all ``allowed_users`` entries that resolve to ``user_id``.

        Includes both the user_id itself and the username (when unique).
        Used to strip every form of a user from a workspace's
        ``allowed_users`` list.
        """
        user = self._id_to_user.get(user_id)
        if user is None:
            return [user_id]
        entries = [user_id]
        if user.name and len(self._name_to_ids.get(user.name, [])) == 1:
            entries.append(user.name)
        return entries

    def resolve_allowed_entries(self, allowed: Iterable[str]) -> List[str]:
        """Resolve raw ``allowed_users`` entries to user_ids.

        Each entry can be either a user_id or a username; usernames are
        resolved if unique. Unknown entries are logged and dropped.
        Raises ``ValueError`` if a username collides with multiple
        user_ids.
        """
        out: List[str] = []
        for entry in allowed:
            if entry in self._id_to_user:
                out.append(entry)
                continue
            ids = self._name_to_ids.get(entry)
            if not ids:
                logger.warning(f'User {entry!r} not found in all users')
                continue
            if len(ids) > 1:
                user_ids_str = ', '.join(ids)
                raise ValueError(
                    f'User {entry!r} has multiple IDs: {user_ids_str}. '
                    'Please specify the user ID instead.')
            out.append(ids[0])
        return out

    def resolve_workspace_users(self, workspace_config: Dict[str,
                                                             Any]) -> List[str]:
        """Resolve a workspace's effective user_id list.

        For private workspaces, resolves ``allowed_users`` (mix of
        user_ids and usernames) to user_ids. For public workspaces,
        returns ``['*']``.
        """
        if not workspace_config.get('private', False):
            return ['*']
        return self.resolve_allowed_entries(
            workspace_config.get('allowed_users', []))

    def resolve_workspaces_allowed_users(
            self, workspaces: Dict[str, Dict[str, Any]]) -> Dict[str, Set[str]]:
        """Pre-resolve each private workspace's allowed_users -> user_id set.

        Returned map only contains private workspaces. Useful when the
        same set of workspaces is consulted repeatedly in a batch.
        """
        out: Dict[str, Set[str]] = {}
        for ws_name, ws_cfg in workspaces.items():
            if ws_cfg.get('private', False):
                out[ws_name] = set(
                    self.resolve_allowed_entries(ws_cfg.get(
                        'allowed_users', [])))
        return out
