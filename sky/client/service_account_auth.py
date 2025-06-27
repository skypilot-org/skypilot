"""Service account token authentication for SkyPilot client."""

import os
from typing import Optional

from sky import skypilot_config


def get_service_account_token() -> Optional[str]:
    """Get service account token from environment variable or config file.

    Priority order:
    1. SKYPILOT_TOKEN environment variable
    2. ~/.sky/config.yaml service_account_token field

    Returns:
        The service account token if found, None otherwise.
    """
    # Check environment variable first
    token = os.environ.get('SKYPILOT_TOKEN')
    if token:
        if not token.startswith('sky_'):
            raise ValueError('Invalid service account token format. '
                             'Token must start with "sky_"')
        return token

    # Check config file
    config = skypilot_config.get_user_config()
    if config is not None:
        token = config.get_nested(('service_account_token',), None)
        if token and not token.startswith('sky_'):
            raise ValueError('Invalid service account token format in config. '
                             'Token must start with "sky_"')
        return token

    return None


def get_service_account_headers() -> dict:
    """Get headers for service account authentication.

    Returns:
        Dictionary with Authorization header if token is available,
        empty dict otherwise.
    """
    token = get_service_account_token()
    if token:
        return {'Authorization': f'Bearer {token}'}
    return {}
