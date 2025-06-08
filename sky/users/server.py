"""REST API for workspace management."""

from typing import Any, Dict, List

import fastapi

from sky import global_user_state
from sky import sky_logging
from sky.server.requests import payloads
from sky.users import permission
from sky.users import rbac

logger = sky_logging.init_logger(__name__)

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
    if not auth_user:
        return {'name': '', 'role': rbac.RoleName.ADMIN.value}
    user_roles = permission.permission_service.get_user_roles(auth_user.id)
    return {'name': auth_user.name, 'role': user_roles[0] if user_roles else ''}


@router.post('/update')
async def user_update(user_update_body: payloads.UserUpdateBody) -> None:
    """Updates the user role."""
    user_id = user_update_body.user_id
    role = user_update_body.role
    supported_roles = rbac.get_supported_roles()
    if role not in supported_roles:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Invalid role: {role}')
    user_info, user_exists = global_user_state.get_user_with_existence(user_id)
    if not user_exists:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'User {user_id} does not exist')

    # Update user role in casbin policy
    permission.permission_service.update_role(user_info.id, role)
