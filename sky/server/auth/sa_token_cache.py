"""Per-process cache of the DB rows behind service-account Bearer auth.

Holds the token row and its user for a successfully authenticated token, keyed
by the token's sha256 hash, for `constants.ENV_VAR_SA_AUTH_CACHE_TTL_SECONDS`.
Revoking or rotating a token, or deleting its user, therefore takes effect on
each server process within that TTL. Failed lookups are never cached.

Accessed only from the server's event loop, so no lock is needed.
"""
import math
import os
from typing import Any, Dict, Optional, Tuple

import cachetools

from sky import global_user_state
from sky import models
from sky.skylet import constants

_MAX_ENTRIES = 4096

TokenRow = Dict[str, Any]


def _get_ttl_seconds() -> float:
    raw = os.environ.get(constants.ENV_VAR_SA_AUTH_CACHE_TTL_SECONDS)
    if raw is None:
        return constants.DEFAULT_SA_AUTH_CACHE_TTL_SECONDS
    error = ValueError(
        f'{constants.ENV_VAR_SA_AUTH_CACHE_TTL_SECONDS}={raw!r} must be a '
        'non-negative number of seconds (0 disables the cache).')
    try:
        seconds = float(raw)
    except ValueError:
        raise error from None
    if not math.isfinite(seconds) or seconds < 0:
        raise error
    return seconds


TTL_SECONDS = _get_ttl_seconds()

_entries: 'cachetools.TTLCache[str, Tuple[TokenRow, models.User]]' = (
    cachetools.TTLCache(maxsize=_MAX_ENTRIES, ttl=TTL_SECONDS))


def fetch_token_and_user(
        token_hash: str,
        user_id: str) -> Tuple[Optional[TokenRow], Optional[models.User]]:
    """Read the token row and, if it exists, the user. Sync: DB queries."""
    token_row = global_user_state.get_service_account_token_by_hash(token_hash)
    if token_row is None:
        return None, None
    return token_row, global_user_state.get_user(user_id)


def get(token_hash: str) -> Optional[Tuple[TokenRow, models.User]]:
    return _entries.get(token_hash)


def put(token_hash: str, token_row: TokenRow, user: models.User) -> None:
    if TTL_SECONDS > 0:
        _entries[token_hash] = (token_row, user)


def clear() -> None:
    _entries.clear()
