"""Service account token authentication for SkyPilot client."""

import os
from typing import Optional

from sky import skypilot_config
from sky.skylet import constants


def _get_service_account_token() -> Optional[str]:
    """Get service account token from environment variable or config file.

    Priority order:
    1. SKYPILOT_SERVICE_ACCOUNT_TOKEN environment variable
    2. ~/.sky/config.yaml service_account_token field

    Returns:
        The service account token if found, None otherwise.
    """
    # Check environment variable first
    token = os.environ.get(constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR)
    if token:
        if not token.startswith('sky_'):
            raise ValueError('Invalid service account token format. '
                             'Token must start with "sky_"')
        return token

    # Check config file
    token = skypilot_config.get_nested(('api_server', 'service_account_token'),
                                       default_value=None)
    if token and not token.startswith('sky_'):
        raise ValueError('Invalid service account token format in config. '
                         'Token must start with "sky_"')
    return token


def get_service_account_headers() -> dict:
    """Get headers for service account authentication.

    Returns:
        Dictionary with Authorization header if token is available,
        empty dict otherwise.
    """
    token = _get_service_account_token()
    if token:
        return {'Authorization': f'Bearer {token}'}
    return {}
