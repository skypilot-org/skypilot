"""Utilities for the API server."""

from typing import Optional, Type, TypeVar

import fastapi

from sky.server.requests import payloads
from sky.skylet import constants

_BodyT = TypeVar('_BodyT', bound=payloads.RequestBody)


# TODO(aylei): remove this and disable request body construction at server-side
def build_body_at_server(request: Optional[fastapi.Request],
                         body_type: Type[_BodyT], **data) -> _BodyT:
    """Builds the request body at the server.

    For historical reasons, some handlers mimic a client request body
    at server-side in order to coordinate with the interface of executor.
    This will cause issues where the client info like user identity is not
    respected in these handlers. This function is a helper to build the request
    body at server-side with the auth user overridden.
    """
    request_body = body_type(**data)
    if request is not None:
        auth_user = getattr(request.state, 'auth_user', None)
        if auth_user:
            request_body.env_vars[constants.USER_ID_ENV_VAR] = auth_user.id
            request_body.env_vars[constants.USER_ENV_VAR] = auth_user.name
    return request_body
