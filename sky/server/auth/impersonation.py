"""Who may act as whom through the impersonation header.

`check` decides whether an authenticated caller may run a request as another
user. Who may impersonate whom is a policy: by default, admin service accounts
when `rbac.allow_impersonation` is on, or whatever a plugin registers with
`ExtensionContext.register_impersonation_policy`. Whatever the policy says, a
caller can never act as a service account or a built-in user, as a user whose
role outranks its own, or call an endpoint its own role is denied.

Everything here is blocking (database reads, policy reloads): call it from a
worker thread.
"""
from typing import Callable, Optional

from sky import global_user_state
from sky import models
from sky import skypilot_config
from sky.users import permission
from sky.users import rbac
from sky.users import server as users_server

# Decides whether `caller` may act as `target`: returns None to allow, or the
# reason for refusing, which is sent back to the caller.
ImpersonationPolicy = Callable[[models.User, models.User], Optional[str]]

_policy: Optional[ImpersonationPolicy] = None


class Refused(Exception):
    """The caller may not act as the requested user."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def set_policy(policy: ImpersonationPolicy) -> None:
    """Replace the default policy, e.g. with a plugin's grants."""
    global _policy
    _policy = policy


def _roles(user_id: str) -> list:
    """The user's roles, reloading the policy only when none is recognized.

    The reload is a database read; doing it only before a denial keeps the
    common case in memory while still seeing a role granted moments ago on
    another worker.
    """
    service = permission.permission_service
    roles = service.roles_in_memory(user_id)
    if not any(role in rbac.get_supported_roles() for role in roles):
        roles = service.get_user_roles(user_id)
    return roles


def _allow_impersonation_enabled() -> bool:
    if skypilot_config.get_nested(('rbac', 'allow_impersonation'), False):
        return True
    # Uvicorn workers refresh the config on their own schedule; re-read it
    # before refusing so a just-enabled setting counts.
    skypilot_config.safe_reload_config()
    return bool(
        skypilot_config.get_nested(('rbac', 'allow_impersonation'), False))


def default_policy(caller: models.User, target: models.User) -> Optional[str]:
    """Admin service accounts, when `rbac.allow_impersonation` is on."""
    del target
    if not _allow_impersonation_enabled():
        return 'Impersonation is not enabled on this API server.'
    if not caller.is_service_account():
        return 'Impersonation requires a service account token.'
    if _roles(caller.id) != [rbac.RoleName.ADMIN.value]:
        return ('Only service accounts with the admin role can impersonate '
                'users.')
    return None


def _resolve_target(target: str) -> models.User:
    """The user to act as: by ID, else by an exact name matching one user."""
    if not target:
        raise Refused('The user to impersonate must not be empty.')
    user = global_user_state.get_user(target)
    if user is None:
        matches = global_user_state.get_user_by_name(target)
        if not matches:
            raise Refused(f'User {target!r} not found.')
        if len(matches) > 1:
            raise Refused(f'User name {target!r} matches {len(matches)} users. '
                          'Use the user ID instead.')
        user = matches[0]
    user_type = users_server.get_user_type(user)
    if user_type == models.UserType.SA.value:
        raise Refused('Service accounts cannot be impersonated.')
    if user_type == models.UserType.SYSTEM.value:
        raise Refused('Built-in system users cannot be impersonated.')
    return user


def check(caller: models.User, target: str, path: str,
          method: str) -> models.User:
    """The user `caller` acts as for this request.

    Raises:
        Refused: If the caller may not act as `target` for this request.
    """
    user = _resolve_target(target)
    reason = (_policy or default_policy)(caller, user)
    if reason is not None:
        raise Refused(reason)
    caller_role = rbac.least_privileged_role(_roles(caller.id))
    user_role = rbac.least_privileged_role(_roles(user.id))
    if rbac.outranks(user_role, caller_role):
        raise Refused(f'Cannot act as {user.name!r}: their role '
                      f'({user_role}) outranks the caller\'s ({caller_role}).')
    # The RBAC middleware checks the user; the caller's own role must allow the
    # endpoint too. It skips the same request-management and dashboard paths.
    if not (path.startswith('/api/') or path.startswith('/dashboard/')):
        if permission.permission_service.check_endpoint_permission(
                caller.id, path, method):
            raise Refused(f'The caller\'s role ({caller_role}) does not allow '
                          f'{method} {path}.')
    return user
