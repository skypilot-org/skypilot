"""REST API for workspace management."""

import contextlib
import hashlib
import os
import re
import secrets
from typing import Any, Dict, Generator, List

import fastapi
import filelock
from passlib.hash import apr_md5_crypt

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.server.requests import payloads
from sky.skylet import constants
from sky.users import permission
from sky.users import rbac
from sky.utils import common
from sky.utils import common_utils

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
        if user.id.startswith('sa-'):
            continue

        user_roles = permission.permission_service.get_user_roles(user.id)
        all_users.append({
            'id': user.id,
            'name': user.name,
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
    password_hash = apr_md5_crypt.hash(password)
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
            password_hash = apr_md5_crypt.hash(password)
            global_user_state.add_or_update_user(
                models.User(id=user_info.id,
                            name=user_info.name,
                            password=password_hash))
        if role and need_update_role:
            # Update user role in casbin policy
            permission.permission_service.update_role(user_info.id, role)


@router.post('/delete')
async def user_delete(user_delete_body: payloads.UserDeleteBody) -> None:
    user_id = user_delete_body.user_id

    user_info = global_user_state.get_user(user_id)
    if user_info is None:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'User {user_id} does not exist')
    # Disallow deleting the internal users.
    if user_info.id in [common.SERVER_ID, constants.SKYPILOT_SYSTEM_USER_ID]:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Cannot delete internal '
                                    f'API server user {user_info.name}')
    with _user_lock(user_id):
        global_user_state.delete_user(user_id)
        permission.permission_service.delete_user(user_id)


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

            # Check if password is already hashed (APR1 hash)
            if password.startswith('$apr1$'):
                # Password is already hashed, use it directly
                password_hash = password
            else:
                # Password is plain text, hash it
                password_hash = apr_md5_crypt.hash(password)

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
            if user.id.startswith('sa-'):
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


@router.get('/service-account-tokens')
async def get_service_account_tokens(
        request: fastapi.Request) -> List[Dict[str, Any]]:
    """Get service account tokens. All users can see all tokens."""
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Authentication required')

    # Import here to avoid circular imports
    # pylint: disable=import-outside-toplevel
    from sky.users.service_accounts import service_account_manager

    # All authenticated users can see all tokens
    tokens = service_account_manager.get_all_service_account_tokens()

    result = []
    for token in tokens:
        token_info = token.to_dict()

        # Add creator display name
        creator_user = global_user_state.get_user(token.user_hash)
        token_info['creator_name'] = creator_user.name if creator_user else 'Unknown'

        # Add service account name and roles
        if token.service_account:
            token_info['service_account_name'] = token.service_account.name
            roles = permission.permission_service.get_user_roles(token.service_account.id)
            token_info['service_account_roles'] = roles
        else:
            token_info['service_account_name'] = token.token_name
            token_info['service_account_roles'] = []

        result.append(token_info)

    return result


@router.post('/service-account-tokens')
async def create_service_account_token(
        request: fastapi.Request,
        token_body: payloads.ServiceAccountTokenCreateBody) -> Dict[str, Any]:
    """Create a new service account token."""
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Authentication required')

    if not token_body.token_name or not token_body.token_name.strip():
        raise fastapi.HTTPException(status_code=400,
                                    detail='Token name is required')

    # Validate expiration (allow 0 as special value for "never expire")
    if (token_body.expires_in_days is not None and
            token_body.expires_in_days < 0):
        raise fastapi.HTTPException(
            status_code=400,
            detail='Expiration days must be positive or 0 for never expire')

    try:
        # Import here to avoid circular imports
        # pylint: disable=import-outside-toplevel
        from sky.users.service_accounts import service_account_manager

        # Create service account using the manager
        service_account = service_account_manager.get_or_create_service_account(
            token_body.token_name.strip(), auth_user.id)

        # Handle expiration: 0 means "never expire"
        expires_in_days = token_body.expires_in_days
        if expires_in_days == 0:
            expires_in_days = None

        # Create token using the service account manager
        token_data = service_account_manager.create_service_account_token(
            service_account=service_account,
            token_name=token_body.token_name.strip(),
            creator_user_hash=auth_user.id,
            expires_in_days=expires_in_days)

        # Return the JWT token only once (never stored in plain text)
        return {
            'token_id': token_data['token_id'],
            'token_name': token_body.token_name.strip(),
            'token': token_data['token'],  # Full JWT token with sky_ prefix
            'expires_at': token_data['expires_at'],
            'service_account_user_id': service_account.id,
            'creator_user_id': auth_user.id,
            'message': 'Please save this token - it will not be shown again!'
        }

    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Failed to create service account token: {e}')
        raise fastapi.HTTPException(
            status_code=500, detail='Failed to create service account token')


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

    # Import here to avoid circular imports
    # pylint: disable=import-outside-toplevel
    from sky.users.service_accounts import service_account_manager

    # Get token info first using the manager
    token = service_account_manager.get_service_account_token_by_id(token_body.token_id)
    if token is None:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    # Check permissions using Casbin policy system
    if not permission.permission_service.check_service_account_token_permission(
            auth_user.id, token.user_hash, 'delete'):
        raise fastapi.HTTPException(
            status_code=403,
            detail='You can only delete your own tokens. Only admins can '
            'delete tokens owned by other users.')

    # Delete the token using the manager
    deleted = service_account_manager.delete_service_account_token(token_body.token_id)
    if not deleted:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    return {'message': 'Token deleted successfully'}


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

    # Import here to avoid circular imports
    # pylint: disable=import-outside-toplevel
    from sky.users.service_accounts import service_account_manager

    # Get token info using the manager
    token = service_account_manager.get_service_account_token_by_id(token_body.token_id)
    if token is None:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    # Check permissions - same as delete permission (only creator or admin)
    if not permission.permission_service.check_service_account_token_permission(
            auth_user.id, token.user_hash, 'delete'):
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
            if token.expires_at:
                import time
                current_time = time.time()
                remaining_seconds = token.expires_at - current_time
                if remaining_seconds > 0:
                    expires_in_days = max(1,
                                          int(remaining_seconds / (24 * 3600)))
                else:
                    # Token already expired, default to 30 days
                    expires_in_days = 30

        # Rotate token using the manager
        token_data = service_account_manager.rotate_service_account_token(
            token_id=token_body.token_id,
            expires_in_days=expires_in_days)

        # Return the new JWT token only once (never stored in plain text)
        return {
            'token_id': token_body.token_id,
            'token_name': token.token_name,
            'token': token_data['token'],  # Full JWT token with sky_ prefix
            'expires_at': token_data['expires_at'],
            'service_account_user_id': token.get_service_account_user_id(),
            'message': ('Token rotated successfully! Please save this new '
                        'token - it will not be shown again!')
        }

    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Failed to rotate service account token: {e}')
        raise fastapi.HTTPException(
            status_code=500, detail='Failed to rotate service account token')


@router.post('/service-account-tokens/get-role')
async def get_service_account_role(
        request: fastapi.Request,
        role_body: payloads.ServiceAccountTokenRoleBody) -> Dict[str, Any]:
    """Get the role of a service account."""
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Authentication required')

    # Import here to avoid circular imports
    # pylint: disable=import-outside-toplevel
    from sky.users.service_accounts import service_account_manager

    # Get token info to find the service account user ID
    token = service_account_manager.get_service_account_token_by_id(role_body.token_id)
    if token is None:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    # Check permissions - only creator or admin can view roles
    if not permission.permission_service.check_service_account_token_permission(
            auth_user.id, token.user_hash, 'view'):
        raise fastapi.HTTPException(
            status_code=403,
            detail='You can only view roles for your own service accounts. '
            'Only admins can view roles for service accounts owned by other '
            'users.')

    # Get service account roles
    service_account_user_id = token.get_service_account_user_id()
    if service_account_user_id is None:
        raise fastapi.HTTPException(status_code=404, detail='Service account not found')
    
    roles = permission.permission_service.get_user_roles(service_account_user_id)

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

    # Import here to avoid circular imports
    # pylint: disable=import-outside-toplevel
    from sky.users.service_accounts import service_account_manager

    # Get token info to find the service account user ID
    token = service_account_manager.get_service_account_token_by_id(role_body.token_id)
    if token is None:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    # Check permissions - only creator or admin can update roles
    if not permission.permission_service.check_service_account_token_permission(
            auth_user.id, token.user_hash, 'update'):
        raise fastapi.HTTPException(
            status_code=403,
            detail='You can only update roles for your own service accounts. '
            'Only admins can update roles for service accounts owned by other '
            'users.')

    try:
        # Update service account role
        service_account_user_id = token.get_service_account_user_id()
        if service_account_user_id is None:
            raise fastapi.HTTPException(status_code=404, detail='Service account not found')
        
        permission.permission_service.update_role(service_account_user_id, role_body.role)

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
