"""Shared authorization for the per-user directory a request may touch."""

from typing import Optional

import fastapi

from sky.utils import common_utils


def owner_user_id(request: fastapi.Request, user_hash: Optional[str]) -> str:
    """The user dir this request may touch: the auth id if set, else user_hash.

    The result is joined as a directory component under API_SERVER_CLIENT_DIR by
    the upload and download handlers, so it must be a single safe path
    component. Rejecting a bad value here stops a request from writing into
    another user's tree or escaping the clients dir.
    """
    user_id = (request.state.auth_user.id
               if request.state.auth_user is not None else user_hash)
    if not common_utils.is_single_path_component(user_id):
        raise fastapi.HTTPException(status_code=400, detail='Invalid user ID')
    assert user_id is not None  # narrowed by the check above
    return user_id
