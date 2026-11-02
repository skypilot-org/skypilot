"""Token bearer for the Nebius instance metadata service (IMDS).

`nebius.aio.token.imds` ships with nebius>=0.6.24. This is a small equivalent
for older SDKs. It only depends on classes that exist since nebius 0.6.12.
Like the SDK's, it is meant to be wrapped in `nebius.aio.token.renewable.Bearer`
so tokens are cached and refreshed before they expire, instead of every SDK
request hitting the metadata service (rate limited to 10 requests per second).

Importing this module requires the Nebius SDK.
"""
import asyncio
import datetime
import json
from typing import Any, Dict, Optional

import aiohttp
from nebius.aio.token import token as token_lib
from nebius.base import error as sdk_error

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 0.2
_REQUEST_TIMEOUT_SECONDS = 5
_MAX_RESPONSE_BYTES = 64 << 10


class IMDSError(sdk_error.SDKError):
    """The metadata service could not provide a token."""


def _parse_token(body: bytes) -> token_lib.Token:
    """Parses `{"access_token": ..., "expires_at": ...}`."""
    if len(body) > _MAX_RESPONSE_BYTES:
        raise IMDSError('IMDS response is too large')
    try:
        payload: Any = json.loads(body)
    except (TypeError, ValueError):
        raise IMDSError('could not decode IMDS token response') from None
    if not isinstance(payload, dict):
        raise IMDSError('IMDS token response must be a JSON object')
    access_token = payload.get('access_token')
    if not isinstance(access_token, str) or not access_token:
        raise IMDSError('IMDS token response has no access_token')
    expires_at = payload.get('expires_at')
    if not isinstance(expires_at, str) or not expires_at:
        raise IMDSError('IMDS token response has no valid expires_at')
    # `datetime.fromisoformat` does not accept a 'Z' suffix before py3.11.
    if expires_at.endswith('Z'):
        expires_at = expires_at[:-1] + '+00:00'
    try:
        expiration = datetime.datetime.fromisoformat(expires_at)
    except ValueError:
        raise IMDSError(
            'IMDS token response has an invalid expires_at') from None
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=datetime.timezone.utc)
    return token_lib.Token(access_token,
                           expiration.astimezone(datetime.timezone.utc))


class Receiver(token_lib.Receiver):
    """Fetches a token from the metadata service."""

    def __init__(self, endpoint: str) -> None:
        super().__init__()
        self._endpoint = endpoint
        self._latest = None

    async def _fetch(
            self,
            timeout: Optional[float] = None,
            options: Optional[Dict[str, str]] = None) -> token_lib.Token:
        del options  # Unused.
        fetch = self._fetch_with_retries()
        if timeout is None:
            return await fetch
        return await asyncio.wait_for(fetch, timeout)

    async def _fetch_with_retries(self) -> token_lib.Token:
        client_timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    async with session.get(self._endpoint,
                                           headers={'Metadata': 'true'
                                                   }) as response:
                        if response.status == 200:
                            return _parse_token(await response.read())
                        retryable = (response.status == 429 or
                                     response.status >= 500)
                        if not retryable or attempt == _MAX_ATTEMPTS - 1:
                            raise IMDSError('IMDS token request failed: '
                                            f'HTTP {response.status}')
                except aiohttp.ClientError as e:
                    raise IMDSError(f'request IMDS token: {e}') from e
                await asyncio.sleep(_BACKOFF_SECONDS * (attempt + 1))
        raise IMDSError('failed to get IMDS token')

    def can_retry(self,
                  err: Exception,
                  options: Optional[Dict[str, str]] = None) -> bool:
        del err, options  # Unused.
        return False


class Bearer(token_lib.Bearer):
    """Bearer for the token endpoint of the metadata service."""

    def __init__(self, endpoint: str) -> None:
        if not endpoint:
            raise IMDSError('empty IMDS endpoint')
        self._endpoint = endpoint

    @property
    def name(self) -> str:
        return f'imds:{self._endpoint}'

    @property
    def metrics_provider(self) -> str:
        return 'imds'

    def receiver(self) -> Receiver:
        return Receiver(self._endpoint)
