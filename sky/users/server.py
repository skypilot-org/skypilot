"""REST API for workspace management."""

import contextlib
import hashlib
import os
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

        for user in user_list:
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

        csv_content = '\n'.join(csv_lines)

        return {'csv_content': csv_content, 'user_count': len(user_list)}

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
    """Get service account tokens. Admins see all tokens, users see only their own."""
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Authentication required')

    # Check if user is admin
    user_roles = permission.permission_service.get_user_roles(auth_user.id)
    is_admin = user_roles and user_roles[0] == rbac.RoleName.ADMIN.value

    if is_admin:
        # Admin can see all tokens
        tokens = global_user_state.get_all_service_account_tokens()
    else:
        # Regular users can only see their own tokens
        tokens = global_user_state.get_user_service_account_tokens(auth_user.id)

    # Add owner information and don't return the token hash for security
    result = []
    for token in tokens:
        token_info = {
            'token_id': token['token_id'],
            'token_name': token['token_name'],
            'created_at': token['created_at'],
            'last_used_at': token['last_used_at'],
            'expires_at': token['expires_at'],
            'user_hash': token['user_hash'],  # Owner's user hash
        }
        
        # Add owner display name if available
        if is_admin or token['user_hash'] != auth_user.id:
            owner_user = global_user_state.get_user(token['user_hash'])
            token_info['owner_name'] = owner_user.name if owner_user else 'Unknown'
        else:
            token_info['owner_name'] = 'You'
            
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

    # Validate expiration
    if (token_body.expires_in_days is not None and
            token_body.expires_in_days <= 0):
        raise fastapi.HTTPException(status_code=400,
                                    detail='Expiration days must be positive')

    try:
        # Import here to avoid circular imports
        # pylint: disable=import-outside-toplevel
        from sky.users.token_service import token_service

        # Create JWT-based token
        token_data = token_service.create_token(
            user_id=auth_user.id,
            token_name=token_body.token_name.strip(),
            expires_in_days=token_body.expires_in_days)

        # Store token metadata in database (still using hash for revocation
        # capability)
        global_user_state.add_service_account_token(
            token_id=token_data['token_id'],
            user_hash=auth_user.id,
            token_name=token_body.token_name.strip(),
            token_hash=token_data['token_hash'],
            expires_at=token_data['expires_at'])

        # Return the JWT token only once (never stored in plain text)
        return {
            'token_id': token_data['token_id'],
            'token_name': token_body.token_name.strip(),
            'token': token_data['token'],  # Full JWT token with sky_ prefix
            'expires_at': token_data['expires_at'],
            'message': 'Please save this token - it will not be shown again!'
        }

    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Failed to create service account token: {e}')
        raise fastapi.HTTPException(
            status_code=500, detail='Failed to create service account token')


@router.delete('/service-account-tokens')
async def delete_service_account_token(
        request: fastapi.Request,
        token_body: payloads.ServiceAccountTokenDeleteBody) -> Dict[str, str]:
    """Delete a service account token. Admins can delete any token, users can only delete their own."""
    auth_user = request.state.auth_user
    if auth_user is None:
        raise fastapi.HTTPException(status_code=401,
                                    detail='Authentication required')

    # Get token info first
    token_info = global_user_state.get_service_account_token(
        token_body.token_id)
    if token_info is None:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    # Check if user is admin
    user_roles = permission.permission_service.get_user_roles(auth_user.id)
    is_admin = user_roles and user_roles[0] == rbac.RoleName.ADMIN.value

    # Check permissions: admin can delete any token, users can only delete their own
    if not is_admin and token_info['user_hash'] != auth_user.id:
        raise fastapi.HTTPException(
            status_code=403, 
            detail='You can only delete your own tokens. Only admins can delete tokens owned by other users.')

    # Delete the token
    deleted = global_user_state.delete_service_account_token(
        token_body.token_id)
    if not deleted:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')

    return {'message': 'Token deleted successfully'}
