"""REST API for workspace management."""

import contextlib
import hashlib
import os
import re
import secrets
import time
from typing import Any, Dict, Generator, List

import fastapi
import filelock

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.server import common as server_common
from sky.server.requests import payloads
from sky.skylet import constants
from sky.users import permission
from sky.users import rbac
from sky.users import token_service
from sky.utils import common
from sky.utils import common_utils
from sky.utils import resource_checker

logger = sky_logging.init_logger(__name__)

# Filelocks for the user management.
USER_LOCK_PATH = os.path.expanduser('~/.sky/.{user_id}.lock')
USER_LOCK_TIMEOUT_SECONDS = 20

router = fastapi.APIRouter()


@router.get('')
async def users() -> List[Dict[str, Any]]:
    """Gets all users."""
    all_users = []
    user_list = global_user_state.get_all_users()
    for user in user_list:
        # Filter out service accounts - they have IDs starting with "sa-"
        if user.is_service_account():
            continue

        user_roles = permission.permission_service.get_user_roles(user.id)
        all_users.append({
            'id': user.id,
            'name': user.name,
            'created_at': user.created_at,
            'role': user_roles[0] if user_roles else ''
        })
    return all_users


@router.get('/role')
async def get_current_user_role(request: fastapi.Request):
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


@router.post('/create')
async def user_create(user_create_body: payloads.UserCreateBody) -> None:
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
    user_hash = hashlib.md5(
        username.encode()).hexdigest()[:common_utils.USER_HASH_LENGTH]
    with _user_lock(user_hash):
        # Check if user already exists
        if global_user_state.get_user_by_name(username):
            raise fastapi.HTTPException(
                status_code=400, detail=f'User {username!r} already exists')
        global_user_state.add_or_update_user(
            models.User(id=user_hash, name=username, password=password_hash))
        permission.permission_service.update_role(user_hash, role)


@router.post('/update')
async def user_update(request: fastapi.Request,
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
    if need_update_role and user_info.id in [
            common.SERVER_ID, constants.SKYPILOT_SYSTEM_USER_ID
    ]:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Cannot update role for internal '
                                    f'API server user {user_info.name}')
    if password and user_info.id == constants.SKYPILOT_SYSTEM_USER_ID:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Cannot update password for internal '
            f'API server user {user_info.name}')

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


def _delete_user(user_id: str) -> None:
    """Delete a user."""
    user_info = global_user_state.get_user(user_id)
    if user_info is None:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'User {user_id} does not exist')
    # Disallow deleting the internal users.
    if user_info.id in [common.SERVER_ID, constants.SKYPILOT_SYSTEM_USER_ID]:
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
async def user_delete(user_delete_body: payloads.UserDeleteBody) -> None:
    user_id = user_delete_body.user_id
    _delete_user(user_id)


@router.post('/import')
async def user_import(
        user_import_body: payloads.UserImportBody) -> Dict[str, Any]:
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

            user_hash = hashlib.md5(
                username.encode()).hexdigest()[:common_utils.USER_HASH_LENGTH]

            with _user_lock(user_hash):
                global_user_state.add_or_update_user(
                    models.User(id=user_hash,
                                name=username,
                                password=password_hash))
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
async def user_export() -> Dict[str, Any]:
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
async def get_service_account_tokens(
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
async def create_service_account_token(
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
                                           name=token_name)
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
async def delete_service_account_token(
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
async def get_service_account_role(
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
async def update_service_account_role(
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
async def rotate_service_account_token(
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
