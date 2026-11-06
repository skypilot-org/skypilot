"""Service account token authentication for SkyPilot client."""

import contextlib
import contextvars
import os
from typing import Iterator, Optional

from sky import skypilot_config
from sky.server import constants as server_constants
from sky.skylet import constants

# Set by `impersonate()`. A context variable rather than a global so that
# concurrent callers (threads, asyncio tasks) can each act as a different user.
_impersonated_user: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar('impersonated_user', default=None))


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


@contextlib.contextmanager
def impersonate(user: str) -> Iterator[None]:
    """Sends the requests made in this context as `user`.

    See `sky.client.sdk.impersonate`.
    """
    if not user or not user.strip():
        raise ValueError('The user to impersonate must not be empty.')
    reset_token = _impersonated_user.set(user.strip())
    try:
        yield
    finally:
        _impersonated_user.reset(reset_token)


def get_impersonated_user() -> Optional[str]:
    """Get the user that requests act as, if any.

    Priority order:
    1. The innermost `impersonate()` context
    2. SKYPILOT_IMPERSONATE_USER environment variable

    Returns:
        The user ID or name to act as, or None to act as the caller itself.
    """
    user = _impersonated_user.get()
    if user is not None:
        return user
    user = os.environ.get(constants.IMPERSONATE_USER_ENV_VAR, '').strip()
    return user or None


def get_service_account_headers() -> dict:
    """Get headers for service account authentication.

    Returns:
        Dictionary with Authorization header if token is available, plus the
        impersonation header if a user to act as is set, empty dict otherwise.

    Raises:
        ValueError: If a user to act as is set without a service account
            token.
    """
    token = _get_service_account_token()
    impersonated_user = get_impersonated_user()
    if impersonated_user is not None and not token:
        raise ValueError(
            f'Acting as user {impersonated_user!r} requires a service account '
            f'token. Set {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR} to the '
            'token of an admin service account.')
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if impersonated_user is not None:
        headers[server_constants.IMPERSONATE_USER_HEADER] = impersonated_user
    return headers
