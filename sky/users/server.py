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

permission_service = permission.PermissionService()
permission_service.init_policies()


@router.get('')
async def users() -> List[Dict[str, Any]]:
    """Gets all users."""
    user_list = global_user_state.get_all_users()
    return [user.to_dict() for user in user_list]


@router.post('/update')
async def user_update(user_update_body: payloads.UserUpdateBody) -> None:
    """Updates the user role."""
    user_id = user_update_body.user_id
    role = user_update_body.role
    supported_roles = rbac.get_supported_roles()
    if role not in supported_roles:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Invalid role: {role}')
    user_info = global_user_state.get_user(user_id)
    if not user_info.role:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'User {user_id} does not exist')
    if role == user_info.role:
        return
    # Update user role in database and casbin policy
    global_user_state.update_user(user_id, role)
    permission_service.update_role(user_id, user_info.role, role)
