"""REST API for workspace management."""

import contextlib
import hashlib
import os
import re
import secrets
import time
from typing import Any, Dict, Generator, List, Optional, Set

import fastapi
import filelock

from sky import exceptions
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.server import common as server_common
from sky.server.requests import payloads
from sky.skylet import constants
from sky.users import permission
from sky.users import rbac
from sky.users import resolver as user_resolver
from sky.users import token_service
from sky.utils import common
from sky.utils import common_utils
from sky.utils import context
from sky.utils import resource_checker
from sky.workspaces import constants as workspace_constants
from sky.workspaces import core as workspaces_core

logger = sky_logging.init_logger(__name__)

# Filelocks for the user management.
USER_LOCK_PATH = os.path.expanduser('~/.sky/.{user_id}.lock')
USER_LOCK_TIMEOUT_SECONDS = 20

# Built-in user identities that the server seeds at startup. They must not
# be deletable or have their role changed via the user-management API.
_INTERNAL_USER_IDS = (
    common.SERVER_ID,
    constants.SKYPILOT_SYSTEM_USER_ID,
    constants.SKYPILOT_SYSTEM_VIEWER_USER_ID,
)

router = fastapi.APIRouter()


def get_user_type(user: models.User) -> str:
    """Get user type for a user for backward compatibility.

    Args:
        user: The user to get the type for.

    Returns:
        The user type string.
    """
    if user.is_service_account():
        return models.UserType.SA.value
    if user.id in _INTERNAL_USER_IDS:
        return models.UserType.SYSTEM.value
    if user.password is not None:
        return models.UserType.BASIC.value
    if user.name and '@' in user.name:
        return models.UserType.SSO.value
    return models.UserType.LEGACY.value


# All handlers in user handler are sync to get fastAPI run it in a
# ThreadPoolExecutor to avoid blocking the async event loop.
# TODO(aylei): make these async once we have the global_user_state async
# support.
@router.get('')
def users() -> List[Dict[str, Any]]:
    """Gets all users."""
    all_users = []
    user_list = global_user_state.get_all_users()

    users_to_role = {}
    for role in rbac.get_supported_roles():
        user_ids = permission.permission_service.get_users_for_role(role)
        for user_id in user_ids:
            users_to_role[user_id] = role

    for user in user_list:
        # Filter out service accounts - they have IDs starting with "sa-"
        if user.is_service_account():
            continue

        user_type = user.user_type or get_user_type(user)
        all_users.append({
            'id': user.id,
            'name': user.name,
            'created_at': user.created_at,
            'role': users_to_role.get(user.id, ''),
            'user_type': user_type,
        })
    return all_users


@router.get('/role')
def get_current_user_role(request: fastapi.Request):
    """Get current user's role."""
    # TODO(hailong): is there a reliable way to get the user
    # hash for the request without 'X-Auth-Request-Email' header?
    auth_user = request.state.auth_user
    if auth_user is None:
        return {'id': '', 'name': '', 'role': rbac.RoleName.ADMIN.value}
    user_roles = permission.permission_service.get_user_roles(auth_user.id)
    return {
        'id': auth_user.id,
        'name': auth_user.name,
        'role': user_roles[0] if user_roles else ''
    }


@router.post('/me/workspace')
def set_user_preferred_workspace(
    request: fastapi.Request,
    body: payloads.UserPreferredWorkspaceBody,
) -> Dict[str, Any]:
    """Sets (or clears with `preferred: null`) the user's preferred workspace.

    Echoes the new preferred value on success. Callers that need the
    resolved workspace + accessible list should follow up with
    ``GET /users/me/workspace``.

    RBAC: rejects setting a workspace the user does not have access to.
    """
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Not authenticated.')
    try:
        workspaces_core.set_user_preferred_workspace(auth_user, body.preferred)
    except exceptions.PermissionDeniedError as e:
        raise fastapi.HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        # Workspace does not exist.
        raise fastapi.HTTPException(status_code=404, detail=str(e)) from e
    return {'preferred': body.preferred}


@router.get('/me/workspace')
@context.contextual
def get_user_workspace(
    request: fastapi.Request,
    requested: Optional[str] = None,
) -> Dict[str, Any]:
    """Returns workspace state for the calling user.

    One stop for everything ``sky workspace info`` / dashboard pages /
    ``_show_enabled_infra`` need:

    * ``workspace``: the workspace the launch path would pick for this
      user RIGHT NOW. Mirrors the launch-path precedence — an explicit
      ``active_workspace`` (set client-side in ``.sky.yaml`` and passed
      here via ``?requested=``, or set in the server's own loaded
      config) wins; otherwise the resolver runs preferred /
      default-fallback / single-membership.
    * ``source``: one of ``WORKSPACE_SOURCE_*``. Tells the UI / CLI why
      the resolver landed where it did (``explicit`` when the active
      override won, otherwise ``preferred`` / ``default-fallback`` /
      ``single-membership``). Drives the optional ``note``.
    * ``note``: free-form message — drift on success
      (``preferred 'team-x' not accessible``), or the error message
      when the resolver couldn't pick a workspace
      (``WorkspaceAmbiguousError``, ``NoWorkspaceAccessError``, or a
      ``PermissionDeniedError`` against an explicit ``requested``). In
      those error cases ``workspace`` is ``None`` and ``source`` is
      ``None``; the caller should render ``note`` as guidance instead
      of treating the request as a server fault.
    * ``preferred``: the persisted preferred workspace (``None`` if the
      user has not set one).
    * ``accessible``: sorted list of every workspace the user can launch
      into. Same set ``/workspaces`` returns the config for, but just
      the names.

    Args:
        request: FastAPI request — the auth middleware must have
            populated ``request.state.auth_user``.
        requested: explicit active workspace from the caller. Mirrors
            the resolver's precedence-1 slot. The client SDK reads its
            local ``active_workspace`` (if any) and stamps it here, so
            the answer reflects what would actually land at launch.

    The handler is synchronous (no executor.schedule_request_async) —
    no request body, the resolver is pure-Python, and dashboard pages
    poll this frequently enough that latency matters.
    """
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Not authenticated.')
    # Sync FastAPI handler — see comment in user_update for why we have
    # to set the per-request user context here ourselves. Without this,
    # `workspaces_core.get_accessible_workspace_names()` (which calls
    # `common_utils.get_current_user()`) would fall back to the API-
    # server process's own identity and return the wrong user's
    # accessible set.
    common_utils.set_current_user(auth_user)
    # Same reason: refresh process-cached workspace config +
    # request-scoped lru cache so admin `workspace create/update`
    # ops that ran on a worker process are visible here.
    server_common.refresh_workspace_state_for_sync_handler()
    # The auth middleware does not populate `preferred_workspace` on
    # `auth_user` (only id/name/type travel via the request context); the
    # resolver reads it off the User dataclass directly. Re-fetch the user
    # row so this handler matches what worker-side `add_or_update_user(
    # return_user=True)` would supply.
    fresh_user = global_user_state.get_user(auth_user.id)
    user_for_resolve = fresh_user if fresh_user is not None else auth_user
    # Mirror launch-path precedence: an explicit `active_workspace` — set
    # by the client and shipped here as `?requested=`, or
    # set in the server's own loaded config — beats the resolver's 2-6
    # path. For queued requests the executor reads the merged thread-
    # local (client-overlay + server base), but this synchronous GET has
    # no request body, so the SDK stamps `?requested=` explicitly. We
    # still check the server-side config as a fallback so an admin who
    # set `active_workspace` globally gets honored.
    if requested is None and skypilot_config.is_active_workspace_set():
        requested = skypilot_config.get_active_workspace()
    accessible = sorted(workspaces_core.get_accessible_workspace_names())
    response: Dict[str, Any] = {
        'workspace': None,
        'source': None,
        'note': None,
        'preferred': user_for_resolve.preferred_workspace,
        'accessible': accessible,
    }
    try:
        resolution = workspaces_core.resolve_workspace_for_user(
            user_for_resolve, requested=requested)
    except exceptions.WorkspaceAmbiguousError as e:
        # Per-user state, not a server fault — return 200 with a state-
        # coded `source` and a SHORT `note`. The CLI / dashboard show
        # the long recovery guidance separately (see
        # `WorkspaceAmbiguousError.recovery_hint`) so the structured
        # payload (`workspace` / `source` / `note` / `preferred` /
        # `accessible`) stays clean and grep-able.
        response['source'] = workspace_constants.WORKSPACE_SOURCE_AMBIGUOUS
        # `e.note` only carries drift context ("preferred 'X' not
        # accessible"); fall back to a generic one-liner otherwise.
        response['note'] = (e.note
                            if e.note else 'multiple workspaces accessible; '
                            'no preferred or active workspace set')
        return response
    except exceptions.NoWorkspaceAccessError as e:
        # One-line message from the raise site ("User <name> (<id>) has
        # no accessible workspaces.") — short enough to fit in the tree
        # row and more informative than a generic stand-in.
        response['source'] = workspace_constants.WORKSPACE_SOURCE_NO_ACCESS
        response['note'] = str(e)
        return response
    except exceptions.PermissionDeniedError as e:
        # Per-workspace deny — raised when an explicit `requested`
        # workspace exists but the user can't access it. We keep the
        # exception message here because it names the specific
        # workspace and the reason (RBAC / not-in-allowed-users),
        # which the payload alone wouldn't convey.
        response['source'] = (
            workspace_constants.WORKSPACE_SOURCE_PERMISSION_DENIED)
        response['note'] = str(e)
        return response
    response['workspace'] = resolution.workspace
    response['source'] = resolution.source
    response['note'] = resolution.note
    return response


@router.post('/create')
def user_create(user_create_body: payloads.UserCreateBody) -> None:
    username = user_create_body.username
    password = user_create_body.password
    role = user_create_body.role

    if not username or not password:
        raise fastapi.HTTPException(status_code=400,
                                    detail='Username and password are required')
    if role and role not in rbac.get_supported_roles():
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Invalid role: {role}')

    if not role:
        role = rbac.get_default_role()

    # Create user
    password_hash = server_common.crypt_ctx.hash(password)
    # MD5 here only derives a stable user identifier from the (non-secret)
    # username; it is not used for any security purpose (passwords use bcrypt
    # via crypt_ctx).
    user_hash = hashlib.md5(
        username.encode(),
        usedforsecurity=False).hexdigest()[:common_utils.USER_HASH_LENGTH]
    with _user_lock(user_hash):
        # Check if user already exists
        if global_user_state.get_user_by_name(username):
            raise fastapi.HTTPException(
                status_code=400, detail=f'User {username!r} already exists')
        global_user_state.add_or_update_user(
            models.User(id=user_hash,
                        name=username,
                        password=password_hash,
                        user_type=models.UserType.BASIC.value))
        permission.permission_service.update_role(user_hash, role)


@router.post('/update')
@context.contextual
def user_update(request: fastapi.Request,
                user_update_body: payloads.UserUpdateBody) -> None:
    """Updates the user role."""
    user_id = user_update_body.user_id
    role = user_update_body.role
    password = user_update_body.password
    supported_roles = rbac.get_supported_roles()
    if role and role not in supported_roles:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Invalid role: {role}')
    target_user_roles = permission.permission_service.get_user_roles(user_id)
    need_update_role = role and (not target_user_roles or
                                 (role != target_user_roles[0]))
    current_user = request.state.auth_user
    if current_user is not None:
        # This is a sync FastAPI handler, so it doesn't go through the
        # executor's reload_for_new_request pipeline that normally
        # populates the per-request user context. Without this, downstream
        # calls (e.g. resource_checker -> queue_v2 ->
        # get_accessible_workspace_names) would fall back to the local
        # machine user and silently filter out anything in private
        # workspaces the local user can't see.
        common_utils.set_current_user(current_user)
        current_user_roles = permission.permission_service.get_user_roles(
            current_user.id)
        if not current_user_roles:
            raise fastapi.HTTPException(status_code=403, detail='Invalid user')
        if current_user_roles[0] != rbac.RoleName.ADMIN.value:
            if need_update_role:
                raise fastapi.HTTPException(
                    status_code=403, detail='Only admin can update user role')
            if password and user_id != current_user.id:
                raise fastapi.HTTPException(
                    status_code=403,
                    detail='Only admin can update password for other users')
    user_info = global_user_state.get_user(user_id)
    if user_info is None:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'User {user_id} does not exist')
    # Disallow updating the internal users.
    if need_update_role and user_info.id in _INTERNAL_USER_IDS:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Cannot update role for internal '
                                    f'API server user {user_info.name}')
    if password and user_info.id in _INTERNAL_USER_IDS:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Cannot update password for internal '
            f'API server user {user_info.name}')

    # When demoting from admin to a non-admin role, ensure the user has no
    # active resources in private workspaces they will lose access to.
    is_demotion = (need_update_role and target_user_roles and
                   target_user_roles[0] == rbac.RoleName.ADMIN.value and
                   role != rbac.RoleName.ADMIN.value)
    if is_demotion:
        try:
            resource_checker.check_user_role_demotion(user_info)
        except ValueError as e:
            raise fastapi.HTTPException(status_code=400, detail=str(e))

    with _user_lock(user_info.id):
        if password:
            password_hash = server_common.crypt_ctx.hash(password)
            global_user_state.add_or_update_user(
                models.User(id=user_info.id,
                            name=user_info.name,
                            password=password_hash))
        if role and need_update_role:
            # Update user role in casbin policy
            permission.permission_service.update_role(user_info.id, role)


@router.post('/batch_update')
@context.contextual
def user_batch_update(request: fastapi.Request,
                      body: payloads.UserBatchUpdateBody) -> Dict[str, Any]:
    """Updates the role for a batch of users.

    Returns a per-user result with ``succeeded`` and ``failed`` lists so the
    caller can show partial failures (e.g. one user is blocked
    while others are not).
    """
    role = body.role
    user_ids = body.user_ids
    supported_roles = rbac.get_supported_roles()
    if not role or role not in supported_roles:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Invalid role: {role}')
    if not user_ids:
        raise fastapi.HTTPException(status_code=400,
                                    detail='user_ids must not be empty')

    # Only admin can run a batch role update.
    current_user = request.state.auth_user
    if current_user is not None:
        # Sync FastAPI handler -- see comment in user_update for why we
        # have to set the per-request user context here ourselves.
        common_utils.set_current_user(current_user)
        current_user_roles = permission.permission_service.get_user_roles(
            current_user.id)
        if not current_user_roles:
            raise fastapi.HTTPException(status_code=403, detail='Invalid user')
        if current_user_roles[0] != rbac.RoleName.ADMIN.value:
            raise fastapi.HTTPException(
                status_code=403, detail='Only admin can update user roles')

    # Pre-fetch the per-user role state ONCE for the whole batch so the
    # per-user loop is O(1) dict lookups instead of N * casbin
    # get_user_roles.
    users_to_role: Dict[str, str] = {}
    for supported_role in supported_roles:
        for uid in permission.permission_service.get_users_for_role(
                supported_role):
            users_to_role[uid] = supported_role

    batch_workspaces_allowed_users: Optional[Dict[str, Set[str]]] = None
    if role == rbac.RoleName.ADMIN.value:
        # Promotion -> nobody needs the demotion check, so we only need
        # user info for the batch's user_ids (one targeted IN query,
        # avoiding a full-table scan that gets wasted on this path).
        all_users_map = global_user_state.get_users(set(user_ids))
        batch_workspaces = None
        batch_resources = None
    else:
        # Demotion -> we need the full user list anyway to detect
        # username-uniqueness across the whole system when resolving each
        # private workspace's ``allowed_users``. Build one shared
        # UserResolver and derive the per-user map from the same fetch
        # so we still do exactly one DB round-trip.
        resolver = user_resolver.UserResolver()
        all_users_map = resolver.id_to_user
        batch_workspaces = resource_checker.load_fresh_workspaces()
        batch_resources = resource_checker.ResourceSnapshot.fetch_all()
        # Pre-resolve each private workspace's allowed_users -> user_id
        # set ONCE for the batch. Without this, check_user_role_demotion
        # iterates private workspaces and calls get_workspace_users for
        # each (every call re-fetches get_all_users() from the DB),
        # giving N * P * get_all_users() round-trips in a batch.
        batch_workspaces_allowed_users = (
            resolver.resolve_workspaces_allowed_users(batch_workspaces))

    succeeded: List[str] = []
    failed: List[Dict[str, str]] = []

    for user_id in user_ids:
        try:
            user_info = all_users_map.get(user_id)
            if user_info is None:
                failed.append({
                    'user_id': user_id,
                    'error': f'User {user_id} does not exist'
                })
                continue
            if user_info.id in _INTERNAL_USER_IDS:
                failed.append({
                    'user_id': user_id,
                    'error': (f'Cannot update role for internal API server '
                              f'user {user_info.name}')
                })
                continue
            current_role = users_to_role.get(user_id)
            target_user_roles = [current_role] if current_role else []
            need_update_role = (not target_user_roles or
                                role != target_user_roles[0])
            if not need_update_role:
                # Already in the desired role; record as success no-op.
                succeeded.append(user_id)
                continue
            # When demoting from admin to a non-admin role (user / viewer),
            # ensure the user has no active resources in private workspaces
            # they will lose implicit access to. Reuse the per-batch
            # pre-fetched workspaces + active resources to keep this O(C+J)
            # for the whole batch instead of O(N * (C+J)).
            if (target_user_roles and
                    target_user_roles[0] == rbac.RoleName.ADMIN.value and
                    role != rbac.RoleName.ADMIN.value):
                resource_checker.check_user_role_demotion(
                    user_info,
                    workspaces=batch_workspaces,
                    resources=batch_resources,
                    workspaces_allowed_users=batch_workspaces_allowed_users)
            with _user_lock(user_info.id):
                permission.permission_service.update_role(user_info.id, role)
            succeeded.append(user_id)
        except ValueError as e:
            failed.append({'user_id': user_id, 'error': str(e)})
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Failed to update role for user {user_id}')
            failed.append({'user_id': user_id, 'error': str(e)})

    return {'succeeded': succeeded, 'failed': failed}


def _delete_user(user_id: str) -> None:
    """Delete a user."""
    user_info = global_user_state.get_user(user_id)
    if user_info is None:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'User {user_id} does not exist')
    # Disallow deleting the internal users.
    if user_info.id in _INTERNAL_USER_IDS:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Cannot delete internal '
                                    f'API server user {user_info.name}')

    # Check for active clusters and managed jobs owned by the user
    try:
        resource_checker.check_no_active_resources_for_users([(user_id,
                                                               'delete')])
    except ValueError as e:
        raise fastapi.HTTPException(status_code=400, detail=str(e))

    with _user_lock(user_id):
        global_user_state.delete_user(user_id)
        permission.permission_service.delete_user(user_id)


@router.post('/delete')
@context.contextual
def user_delete(request: fastapi.Request,
                user_delete_body: payloads.UserDeleteBody) -> None:
    current_user = request.state.auth_user
    if current_user is not None:
        # Sync FastAPI handler -- see comment in user_update for why we
        # have to set the per-request user context here ourselves.
        common_utils.set_current_user(current_user)
    user_id = user_delete_body.user_id
    _delete_user(user_id)


@router.post('/import')
def user_import(user_import_body: payloads.UserImportBody) -> Dict[str, Any]:
    """Import users from CSV content."""
    csv_content = user_import_body.csv_content

    if not csv_content:
        raise fastapi.HTTPException(status_code=400,
                                    detail='CSV content is required')

    # Parse CSV content
    lines = csv_content.strip().split('\n')
    if len(lines) < 2:
        raise fastapi.HTTPException(
            status_code=400,
            detail='CSV must have at least a header row and one data row')

    # Parse headers
    headers = [h.strip().lower() for h in lines[0].split(',')]
    required_headers = ['username', 'password', 'role']

    # Check if all required headers are present
    missing_headers = [
        header for header in required_headers if header not in headers
    ]
    if missing_headers:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Missing required columns: {", ".join(missing_headers)}')

    # Parse user data
    users_to_create = []
    parse_errors = []

    for i, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue  # Skip empty lines

        values = [v.strip() for v in line.split(',')]
        if len(values) != len(headers):
            parse_errors.append(f'Line {i}: Invalid number of columns')
            continue

        user_data = dict(zip(headers, values))

        # Validate required fields
        if not user_data.get('username') or not user_data.get('password'):
            parse_errors.append(f'Line {i}: Username and password are required')
            continue

        # Validate role
        role = user_data.get('role', '').lower()
        if role and role not in rbac.get_supported_roles():
            role = rbac.get_default_role()  # Default to default role if invalid
        elif not role:
            role = rbac.get_default_role()

        users_to_create.append({
            'username': user_data['username'],
            'password': user_data['password'],
            'role': role
        })

    if not users_to_create and parse_errors:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'No valid users found. Errors: {"; ".join(parse_errors)}')

    # Create users
    success_count = 0
    error_count = 0
    creation_errors = []

    for user_data in users_to_create:
        try:
            username = user_data['username']
            password = user_data['password']
            role = user_data['role']

            # Check if user already exists
            if global_user_state.get_user_by_name(username):
                error_count += 1
                creation_errors.append(f'{username}: User already exists')
                continue

            # Check if password is already hashed
            if server_common.crypt_ctx.identify(password) is not None:
                # Password is already hashed, use it directly
                password_hash = password
            else:
                # Password is plain text, hash it
                password_hash = server_common.crypt_ctx.hash(password)

            # MD5 only derives a stable user identifier from the (non-secret)
            # username; not a security use (passwords use bcrypt via crypt_ctx).
            user_hash = hashlib.md5(username.encode(),
                                    usedforsecurity=False).hexdigest()
            user_hash = user_hash[:common_utils.USER_HASH_LENGTH]

            with _user_lock(user_hash):
                global_user_state.add_or_update_user(
                    models.User(
                        id=user_hash,
                        name=username,
                        password=password_hash,
                        user_type=models.UserType.BASIC.value,
                    ))
                permission.permission_service.update_role(user_hash, role)

            success_count += 1

        except Exception as e:  # pylint: disable=broad-except
            error_count += 1
            creation_errors.append(f'{user_data["username"]}: {str(e)}')

    return {
        'success_count': success_count,
        'error_count': error_count,
        'total_processed': len(users_to_create),
        'parse_errors': parse_errors,
        'creation_errors': creation_errors
    }


@router.get('/export')
def user_export() -> Dict[str, Any]:
    """Export all users as CSV content."""
    try:
        # Get all users
        user_list = global_user_state.get_all_users()

        # Create CSV content
        csv_lines = ['username,password,role']  # Header

        exported_users = []
        for user in user_list:
            # Filter out service accounts - they have IDs starting with "sa-"
            if user.is_service_account():
                continue

            # Get user role
            user_roles = permission.permission_service.get_user_roles(user.id)
            role = user_roles[0] if user_roles else rbac.get_default_role()
            # Avoid exporting `None` values
            line = ''
            if user.name:
                line += user.name
            line += ','
            if user.password:
                line += user.password
            line += ','
            if role:
                line += role
            csv_lines.append(line)
            exported_users.append(user)

        csv_content = '\n'.join(csv_lines)

        return {'csv_content': csv_content, 'user_count': len(exported_users)}

    except Exception as e:
        raise fastapi.HTTPException(status_code=500,
                                    detail=f'Failed to export users: {str(e)}')


@contextlib.contextmanager
def _user_lock(user_id: str) -> Generator[None, None, None]:
    """Context manager for user lock."""
    try:
        with filelock.FileLock(USER_LOCK_PATH.format(user_id=user_id),
                               USER_LOCK_TIMEOUT_SECONDS):
            yield
    except filelock.Timeout as e:
        raise RuntimeError(f'Failed to update user due to a timeout '
                           f'when trying to acquire the lock at '
                           f'{USER_LOCK_PATH.format(user_id=user_id)}. '
                           'Please try again or manually remove the lock '
                           f'file if you believe it is stale.') from e


# ===============================
# Service account tokens
# ===============================
# SkyPilot currently does not distinguish between service accounts and service
# account tokens, i.e. service accounts have a 1-1 mapping to service account
# tokens.


@router.get('/service-account-tokens')
def get_service_account_tokens(
        request: fastapi.Request) -> List[Dict[str, Any]]:
    """Get service account tokens. All users can see all tokens."""
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Authentication required')

    # All authenticated users can see all tokens
    tokens = global_user_state.get_all_service_account_tokens()

    result = []
    for token in tokens:
        token_info = {
            'token_id': token['token_id'],
            'token_name': token['token_name'],
            'created_at': token['created_at'],
            'last_used_at': token['last_used_at'],
            'expires_at': token['expires_at'],
            'creator_user_hash': token['creator_user_hash'],
            'service_account_user_id': token['service_account_user_id'],
        }

        # Add creator display name
        creator_user = global_user_state.get_user(token['creator_user_hash'])
        token_info[
            'creator_name'] = creator_user.name if creator_user else 'Unknown'

        # Add service account name
        sa_user = global_user_state.get_user(token['service_account_user_id'])
        token_info['service_account_name'] = (sa_user.name if sa_user else
                                              token['token_name'])

        # Add service account roles
        roles = permission.permission_service.get_user_roles(
            token['service_account_user_id'])
        token_info['service_account_roles'] = roles

        result.append(token_info)

    return result


def _generate_service_account_user_id() -> str:
    """Generate a unique user ID for a service account."""
    random_suffix = secrets.token_hex(8)  # 16 character hex string
    service_account_id = (f'sa-{random_suffix}')
    return service_account_id


@router.post('/service-account-tokens')
def create_service_account_token(
        request: fastapi.Request,
        token_body: payloads.ServiceAccountTokenCreateBody) -> Dict[str, Any]:
    """Create a new service account token."""
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Authentication required')

    token_name = token_body.token_name.strip()

    # Check if token follows a valid format
    if not re.match(constants.CLUSTER_NAME_VALID_REGEX, token_name):
        raise fastapi.HTTPException(
            status_code=400,
            detail='Token name must contain only letters, numbers, and '
            'underscores. Please use a different name.')

    # Validate expiration (allow 0 as special value for "never expire")
    if (token_body.expires_in_days is not None and
            token_body.expires_in_days < 0):
        raise fastapi.HTTPException(
            status_code=400,
            detail='Expiration days must be positive or 0 for never expire')

    try:
        # Generate a unique service account user ID
        service_account_user_id = _generate_service_account_user_id()

        # Create a user entry for the service account
        service_account_user = models.User(id=service_account_user_id,
                                           name=token_name,
                                           user_type=models.UserType.SA.value)
        is_new_user = global_user_state.add_or_update_user(
            service_account_user, allow_duplicate_name=False)

        if not is_new_user:
            raise fastapi.HTTPException(
                status_code=400,
                detail=f'Service account with name {token_name!r} '
                f'already exists ({service_account_user_id}). '
                'Please use a different name.')

        # Add service account to permission system with default role
        # Import here to avoid circular imports
        # pylint: disable=import-outside-toplevel
        from sky.users.permission import permission_service
        permission_service.add_user_if_not_exists(service_account_user_id)

        # Handle expiration: 0 means "never expire"
        expires_in_days = token_body.expires_in_days
        if expires_in_days == 0:
            expires_in_days = None

        # Create JWT-based token with service account user ID
        token_data = token_service.token_service.create_token(
            creator_user_id=auth_user.id,
            service_account_user_id=service_account_user_id,
            token_name=token_name,
            expires_in_days=expires_in_days)

        # Store token metadata in database
        global_user_state.add_service_account_token(
            token_id=token_data['token_id'],
            token_name=token_name,
            token_hash=token_data['token_hash'],
            creator_user_hash=auth_user.id,
            service_account_user_id=service_account_user_id,
            expires_at=token_data['expires_at'])

        # Return the JWT token only once (never stored in plain text)
        return {
            'token_id': token_data['token_id'],
            'token_name': token_name,
            'token': token_data['token'],  # Full JWT token with sky_ prefix
            'expires_at': token_data['expires_at'],
            'service_account_user_id': service_account_user_id,
            'creator_user_id': auth_user.id,
            'message': 'Please save this token - it will not be shown again!'
        }

    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Failed to create service account token: {e}')
        raise fastapi.HTTPException(
            status_code=500,
            detail=f'Failed to create service account token: {e}')


@router.post('/service-account-tokens/delete')
def delete_service_account_token(
        request: fastapi.Request,
        token_body: payloads.ServiceAccountTokenDeleteBody) -> Dict[str, str]:
    """Delete a service account token.

    Admins can delete any token, users can only delete their own.
    """
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Authentication required')

    # Get token info first
    token_info = global_user_state.get_service_account_token(
        token_body.token_id)
    if token_info is None:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    # Check permissions using Casbin policy system
    if not permission.permission_service.check_service_account_token_permission(
            auth_user.id, token_info['creator_user_hash'], 'delete'):
        raise fastapi.HTTPException(
            status_code=403,
            detail='You can only delete your own tokens. Only admins can '
            'delete tokens owned by other users.')

    # Try to delete the service account user first to make sure there is no
    # active resources owned by the service account.
    service_account_user_id = token_info['service_account_user_id']
    _delete_user(service_account_user_id)

    # Delete the token
    deleted = global_user_state.delete_service_account_token(
        token_body.token_id)
    if not deleted:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    return {'message': 'Token deleted successfully'}


@router.post('/service-account-tokens/get-role')
def get_service_account_role(
        request: fastapi.Request,
        role_body: payloads.ServiceAccountTokenRoleBody) -> Dict[str, Any]:
    """Get the role of a service account."""
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Authentication required')

    # Get token info to find the service account user ID
    token_info = global_user_state.get_service_account_token(role_body.token_id)
    if token_info is None:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    # Check permissions - only creator or admin can view roles
    if not permission.permission_service.check_service_account_token_permission(
            auth_user.id, token_info['creator_user_hash'], 'view'):
        raise fastapi.HTTPException(
            status_code=403,
            detail='You can only view roles for your own service accounts. '
            'Only admins can view roles for service accounts owned by other '
            'users.')

    # Get service account roles
    service_account_user_id = token_info['service_account_user_id']
    roles = permission.permission_service.get_user_roles(
        service_account_user_id)

    return {
        'token_id': role_body.token_id,
        'service_account_user_id': service_account_user_id,
        'roles': roles
    }


@router.post('/service-account-tokens/update-role')
def update_service_account_role(
        request: fastapi.Request,
        role_body: payloads.ServiceAccountTokenUpdateRoleBody
) -> Dict[str, str]:
    """Update the role of a service account."""
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Authentication required')

    # Get token info to find the service account user ID
    token_info = global_user_state.get_service_account_token(role_body.token_id)
    if token_info is None:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    # Check permissions - only creator or admin can update roles
    if not permission.permission_service.check_service_account_token_permission(
            auth_user.id, token_info['creator_user_hash'], 'update'):
        raise fastapi.HTTPException(
            status_code=403,
            detail='You can only update roles for your own service accounts. '
            'Only admins can update roles for service accounts owned by other '
            'users.')

    try:
        # Update service account role
        service_account_user_id = token_info['service_account_user_id']
        permission.permission_service.update_role(service_account_user_id,
                                                  role_body.role)

        return {
            'message': f'Service account role updated to {role_body.role}',
            'token_id': role_body.token_id,
            'service_account_user_id': service_account_user_id,
            'new_role': role_body.role
        }
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Failed to update service account role: {e}')
        raise fastapi.HTTPException(
            status_code=500, detail='Failed to update service account role')


@router.post('/service-account-tokens/rotate')
def rotate_service_account_token(
        request: fastapi.Request,
        token_body: payloads.ServiceAccountTokenRotateBody) -> Dict[str, Any]:
    """Rotate a service account token.

    Generates a new token value for an existing service account while keeping
    the same service account identity and roles.
    """
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Authentication required')

    # Get token info
    token_info = global_user_state.get_service_account_token(
        token_body.token_id)
    if token_info is None:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    # Check permissions - same as delete permission (only creator or admin)
    if not permission.permission_service.check_service_account_token_permission(
            auth_user.id, token_info['creator_user_hash'], 'delete'):
        raise fastapi.HTTPException(
            status_code=403,
            detail='You can only rotate your own tokens. Only admins can '
            'rotate tokens owned by other users.')

    # Validate expiration if provided (allow 0 as special value for "never
    # expire")
    if (token_body.expires_in_days is not None and
            token_body.expires_in_days < 0):
        raise fastapi.HTTPException(
            status_code=400,
            detail='Expiration days must be positive or 0 for never expire')

    try:
        # Use provided expiration or preserve original expiration logic
        expires_in_days = token_body.expires_in_days
        if expires_in_days == 0:
            # Special value 0 means "never expire"
            expires_in_days = None
        elif expires_in_days is None:
            # No expiration specified, try to preserve original expiration
            if token_info['expires_at']:
                current_time = time.time()
                remaining_seconds = token_info['expires_at'] - current_time
                if remaining_seconds > 0:
                    expires_in_days = max(1,
                                          int(remaining_seconds / (24 * 3600)))
                else:
                    # Token already expired, default to 30 days
                    expires_in_days = 30

        # Generate new JWT token with same service account user ID
        token_data = token_service.token_service.create_token(
            creator_user_id=token_info['creator_user_hash'],
            service_account_user_id=token_info['service_account_user_id'],
            token_name=token_info['token_name'],
            expires_in_days=expires_in_days)

        # Update token in database with new token hash
        global_user_state.rotate_service_account_token(
            token_id=token_body.token_id,
            new_token_hash=token_data['token_hash'],
            new_expires_at=token_data['expires_at'])

        # Return the new JWT token only once (never stored in plain text)
        return {
            'token_id': token_body.token_id,
            'token_name': token_info['token_name'],
            'token': token_data['token'],  # Full JWT token with sky_ prefix
            'expires_at': token_data['expires_at'],
            'service_account_user_id': token_info['service_account_user_id'],
            'message': ('Token rotated successfully! Please save this new '
                        'token - it will not be shown again!')
        }

    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Failed to rotate service account token: {e}')
        raise fastapi.HTTPException(
            status_code=500, detail='Failed to rotate service account token')
