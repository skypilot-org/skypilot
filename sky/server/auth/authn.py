"""Authentication module."""
import json
from typing import Optional

import fastapi

from sky import models
from sky import sky_logging
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)


# TODO(hailong): Remove this function and use request.state.auth_user instead.
async def override_user_info_in_request_body(request: fastapi.Request,
                                             auth_user: Optional[models.User]):
    # Skip for upload requests to avoid consuming the body prematurely, which
    # will break the streaming upload.
    if request.url.path.startswith('/upload'):
        return
    if auth_user is None:
        return

    body = await request.body()
    if body:
        try:
            original_json = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f'Error parsing request JSON: {e}')
        else:
            logger.debug(f'Overriding user for {request.state.request_id}: '
                         f'{auth_user.name}, {auth_user.id}')
            if 'env_vars' in original_json:
                if isinstance(original_json.get('env_vars'), dict):
                    original_json['env_vars'][
                        constants.USER_ID_ENV_VAR] = auth_user.id
                    original_json['env_vars'][
                        constants.USER_ENV_VAR] = auth_user.name
                else:
                    logger.warning(
                        f'"env_vars" in request body is not a dictionary '
                        f'for request {request.state.request_id}. '
                        'Skipping user info injection into body.')
            else:
                original_json['env_vars'] = {}
                original_json['env_vars'][
                    constants.USER_ID_ENV_VAR] = auth_user.id
                original_json['env_vars'][
                    constants.USER_ENV_VAR] = auth_user.name
            request._body = json.dumps(original_json).encode('utf-8')  # pylint: disable=protected-access
