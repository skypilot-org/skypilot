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
    try:
        config = skypilot_config.get_user_config()
        token = config.get_nested(('api_server', 'service_account_token'),
                                  default_value=None)
        if token:
            if not token.startswith('sky_'):
                raise ValueError(
                    'Invalid service account token format in config file. '
                    'Token must start with "sky_"')
            return token
    except Exception:  # pylint: disable=broad-except
        # If config file doesn't exist or is malformed, continue
        pass

    return None


def get_service_account_headers() -> dict:
    """Get HTTP headers for service account authentication.

    Returns:
        Dictionary with Authorization header if token is available,
        empty dict otherwise.
    """
    token = get_service_account_token()
    if token:
        return {'Authorization': f'Bearer {token}'}
    return {}


def should_use_service_account_path() -> bool:
    """Check if we should use the /sa/* path for OAuth2 proxy bypass.

    Returns:
        True if service account token is available, False otherwise.
    """
    return get_service_account_token() is not None


def get_api_url_with_service_account_path(base_url: str, path: str) -> str:
    """Get API URL with service account path prefix if needed.

    Args:
        base_url: Base URL of the API server (e.g., http://server.com)
        path: API path (e.g., /api/v1/status)

    Returns:
        Full URL with /sa prefix if using service account token,
        otherwise regular URL.
    """
    if should_use_service_account_path():
        # Add /sa prefix for OAuth2 proxy bypass
        if path.startswith('/'):
            path = path[1:]  # Remove leading slash
        return f'{base_url}/sa/{path}'
    else:
        # Regular path for OAuth2 proxy flow
        return f'{base_url}/{path}' if not path.startswith(
            '/') else f'{base_url}{path}'
