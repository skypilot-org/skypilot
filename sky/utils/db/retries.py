"""Retry helpers for transient DB errors.

The jobs controller and other long-running SkyPilot processes hit
postgres on routine bookkeeping. A brief DB outage (RDS failover, DNS
hiccup, network blip) would otherwise propagate to controller catch-alls
that mark jobs as FAILED_CONTROLLER, or — for longer outages — leave the
catch-all's own DB write failing too, producing a silent RUNNING zombie.

Wrap any DB-touching call site with one of these helpers. They catch
`sqlalchemy.exc.OperationalError` (which wraps `psycopg2.OperationalError`
and many asyncpg connection errors), plus raw driver/network exceptions
that can escape SQLAlchemy, and retry with exponential backoff + jitter.
"""

import asyncio
import logging
import socket
import time
from typing import Awaitable, Callable, Tuple, Type, TypeVar

import sqlalchemy.exc

from sky.utils import common_utils

logger = logging.getLogger(__name__)

T = TypeVar('T')


def _build_retryable_exceptions() -> Tuple[Type[BaseException], ...]:
    # `psycopg2.OperationalError` is the raw driver error; SQLAlchemy
    # wraps it for normal session.execute() paths, but anything that
    # uses `engine.raw_connection()` (notably `PostgresLock`) raises
    # the unwrapped class. Import lazily so sqlite-only installs work.
    psycopg2_excs: Tuple[Type[BaseException], ...] = ()
    try:
        import psycopg2  # pylint: disable=import-outside-toplevel

        # `InterfaceError` covers "connection already closed" — what you
        # get on a stale raw_connection during/after an outage.
        psycopg2_excs = (psycopg2.OperationalError, psycopg2.InterfaceError)
    except ImportError:
        pass
    # `ConnectionError` is the Python builtin; asyncpg raises it
    # ("unexpected connection_lost() call") in some code paths without
    # SQLAlchemy wrapping it.
    # `socket.gaierror` is what asyncpg raises on DNS resolution failure —
    # it propagates uncaught through asyncpg.connect() and is NOT wrapped
    # by SQLAlchemy when going through async_creator.
    return (
        sqlalchemy.exc.OperationalError,
        sqlalchemy.exc.InterfaceError,
        ConnectionError,
        socket.gaierror,
        *psycopg2_excs,
    )


RETRYABLE_EXCEPTIONS = _build_retryable_exceptions()

# 5 attempts × exp backoff capped at 5s ≈ ~10s total retry budget — covers
# typical brief outages (DNS hiccup, sub-second RDS reconnect) and the
# short tail of longer ones; very long outages (multi-second RDS failover)
# will still raise.
_DEFAULT_MAX_RETRIES = 5
_DEFAULT_INITIAL_BACKOFF = 1.0
_DEFAULT_MAX_BACKOFF_FACTOR = 5  # cap = 1.0 * 5 = 5s


def summarize(e: BaseException) -> str:
    """One-line exception summary (SQLAlchemy errors are multi-line)."""
    return f'{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ""}'


def with_db_retries(fn: Callable[[], T],
                    max_retries: int = _DEFAULT_MAX_RETRIES) -> T:
    """Run `fn()` with retry/backoff on transient DB errors."""
    if max_retries < 1:
        raise ValueError(
            f'max_retries must be greater than 0, got {max_retries}')
    backoff = common_utils.Backoff(
        initial_backoff=_DEFAULT_INITIAL_BACKOFF,
        max_backoff_factor=_DEFAULT_MAX_BACKOFF_FACTOR)
    for attempt in range(max_retries):
        try:
            result = fn()
            if attempt > 0:
                logger.info(
                    f'Transient DB error recovered after {attempt} retries.')
            return result
        except RETRYABLE_EXCEPTIONS as e:
            if attempt == max_retries - 1:
                logger.error(f'Transient DB error: giving up after '
                             f'{max_retries} attempts; {summarize(e)}')
                raise
            delay = backoff.current_backoff()
            logger.warning(
                f'Transient DB error (attempt {attempt + 1}/{max_retries}), '
                f'retrying in {delay:.1f}s: {summarize(e)}')
            time.sleep(delay)
    raise AssertionError('with_db_retries: unreachable')


async def with_db_retries_async(coro_fn: Callable[[], Awaitable[T]],
                                max_retries: int = _DEFAULT_MAX_RETRIES) -> T:
    """Async equivalent of with_db_retries."""
    if max_retries < 1:
        raise ValueError(
            f'max_retries must be greater than 0, got {max_retries}')
    backoff = common_utils.Backoff(
        initial_backoff=_DEFAULT_INITIAL_BACKOFF,
        max_backoff_factor=_DEFAULT_MAX_BACKOFF_FACTOR)
    for attempt in range(max_retries):
        try:
            result = await coro_fn()
            if attempt > 0:
                logger.info(
                    f'Transient DB error recovered after {attempt} retries.')
            return result
        except RETRYABLE_EXCEPTIONS as e:
            if attempt == max_retries - 1:
                logger.error(f'Transient DB error: giving up after '
                             f'{max_retries} attempts; {summarize(e)}')
                raise
            delay = backoff.current_backoff()
            logger.warning(
                f'Transient DB error (attempt {attempt + 1}/{max_retries}), '
                f'retrying in {delay:.1f}s: {summarize(e)}')
            await asyncio.sleep(delay)
    raise AssertionError('with_db_retries_async: unreachable')
