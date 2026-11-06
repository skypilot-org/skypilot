"""Per-process cache of the DB rows behind service-account Bearer auth.

Holds the token row and its user for a successfully authenticated token, keyed
by the token's sha256 hash, for `constants.ENV_VAR_SA_AUTH_CACHE_TTL_SECONDS`.
A hit on an entry older than half the TTL re-reads the rows in the background,
so a token in steady use is never looked up on the request path. Revoking or
rotating a token, or deleting its user, therefore takes effect on each server
process within that TTL. Failed lookups are never cached.

Accessed only from the server's event loop, so no lock is needed.
"""
import asyncio
import math
import os
from typing import Any, Coroutine, Dict, Optional, Set, Tuple

import cachetools

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.server.auth import db_lookup
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

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

# token hash -> (token row, user, cache time the rows were read).
_entries: 'cachetools.TTLCache[str, Tuple[TokenRow, models.User, float]]' = (
    cachetools.TTLCache(maxsize=_MAX_ENTRIES, ttl=TTL_SECONDS))
# Hashes whose rows a background task is re-reading.
_refreshing: Set[str] = set()
_background_tasks: Set[asyncio.Task] = set()


def fetch_token_and_user(
        token_hash: str,
        user_id: str) -> Tuple[Optional[TokenRow], Optional[models.User]]:
    """Read the token row and, if it exists, the user. Sync: DB queries."""
    token_row = global_user_state.get_service_account_token_by_hash(token_hash)
    if token_row is None:
        return None, None
    return token_row, global_user_state.get_user(user_id)


def get(token_hash: str) -> Optional[Tuple[TokenRow, models.User]]:
    """The cached rows, re-read in the background once half the TTL old."""
    entry = _entries.get(token_hash)
    if entry is None:
        return None
    token_row, user, read_at = entry
    if (_entries.timer() - read_at >= TTL_SECONDS / 2 and
            token_hash not in _refreshing):
        _refreshing.add(token_hash)
        run_in_background(_refresh(token_hash, user.id))
    return token_row, user


def put(token_hash: str, token_row: TokenRow, user: models.User) -> None:
    if TTL_SECONDS > 0:
        _entries[token_hash] = (token_row, user, _entries.timer())


def clear() -> None:
    _entries.clear()
    _refreshing.clear()


def run_in_background(coro: Coroutine[Any, Any, None]) -> None:
    """Run auth bookkeeping off the request path on the current loop."""
    task = asyncio.get_running_loop().create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _refresh(token_hash: str, user_id: str) -> None:
    try:
        token_row, user = await db_lookup.call_with_deadline(
            fetch_token_and_user, token_hash, user_id)
    except Exception as e:  # pylint: disable=broad-except
        # The entry stays until its TTL; the next request after that looks
        # the token up itself.
        logger.debug(f'Failed to refresh a cached service account token: {e}')
        return
    finally:
        _refreshing.discard(token_hash)
    if token_row is None or user is None:
        _entries.pop(token_hash, None)
    else:
        put(token_hash, token_row, user)
