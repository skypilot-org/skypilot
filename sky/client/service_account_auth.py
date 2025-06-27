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
    token = config.get_nested(('api_server', 'service_account_token'),
                              default_value=None)
    if token:
        if not token.startswith('sky_'):
            raise ValueError(
                'Invalid service account token format in config file. '
                'Token must start with "sky_"')
        return token

    return None


def get_service_account_headers() -> dict:
    """Get HTTP headers for service account authentication.

    Returns:
        Dictionary with Authorization header if service account token
        is available, empty dict otherwise.
    """
    token = get_service_account_token()
    if token:
        return {
            'Authorization': f'Bearer {token}',
        }
    return {}


def get_api_url(base_url: str, path: str) -> str:
    """Get API URL for requests.

    Args:
        base_url: Base URL of the API server (e.g., http://server.com)
        path: API path (e.g., /api/v1/status)

    Returns:
        Full URL. Always uses the same path regardless of authentication type.
    """
    # Always use the same URL - no special handling for service accounts
    # OAuth2 proxy will handle authentication based on headers
    return f'{base_url}/{path}' if not path.startswith(
        '/') else f'{base_url}{path}'
