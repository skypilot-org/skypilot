"""Shared authorization for log download staging and retrieval."""

import fastapi

from sky.server.requests import payloads


def download_user_id(request: fastapi.Request,
                     body: payloads.RequestBody) -> str:
    """Choose a download owner whose ID is a single path component."""
    user_id = (request.state.auth_user.id
               if request.state.auth_user is not None else body.user_hash)
    if (not user_id or user_id in ('.', '..') or '/' in user_id or
            '\\' in user_id):
        raise fastapi.HTTPException(status_code=400, detail='Invalid user ID')
    return user_id
