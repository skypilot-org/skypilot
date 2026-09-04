"""Retry helpers for transient DB errors.

The jobs controller and other long-running SkyPilot processes hit
postgres on routine bookkeeping. A brief DB outage (RDS failover, DNS
hiccup, network blip) would otherwise propagate to controller catch-alls
that mark jobs as FAILED_CONTROLLER, or — for longer outages — leave the
catch-all's own DB write failing too, producing a silent RUNNING zombie.

Wrap any DB-touching call site with one of these helpers. They catch
`sqlalchemy.exc.OperationalError` (which wraps `psycopg2.OperationalError`
and many asyncpg connection errors), plus raw driver/network exceptions
that can escape SQLAlchemy, also when a driver error is the `__cause__` of
the raised exception, and retry with exponential backoff + jitter.
"""

import asyncio
import functools
import logging
import socket
import sys
import time
from typing import Awaitable, Callable, Optional, Set, Tuple, Type, TypeVar

import sqlalchemy.exc

from sky.utils import common_utils

logger = logging.getLogger(__name__)

T = TypeVar('T')


def _build_driver_exceptions() -> Tuple[Type[BaseException], ...]:
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
    return (
        sqlalchemy.exc.OperationalError,
        sqlalchemy.exc.InterfaceError,
        *psycopg2_excs,
    )


_DRIVER_EXCEPTIONS = _build_driver_exceptions()


def _asyncpg_exceptions() -> Tuple[Type[BaseException], ...]:
    # asyncpg.connect() runs inside the engine's async_creator, so an
    # ErrorResponse received while connecting is raised as asyncpg's own
    # class, not wrapped by SQLAlchemy: class 08 connection errors (which a
    # pooler such as PgBouncer uses for every rejection it issues, 08P01),
    # 57P03 (server starting up / shutting down) and 53300 (too many
    # connections). Resolved from sys.modules rather than imported: the
    # import costs ~40ms per process, and an asyncpg error can only be in
    # flight in a process that has already imported asyncpg.
    asyncpg = sys.modules.get('asyncpg')
    if asyncpg is None:
        return ()
    return (
        asyncpg.exceptions.PostgresConnectionError,
        asyncpg.exceptions.CannotConnectNowError,
        asyncpg.exceptions.TooManyConnectionsError,
    )


# `ConnectionError` is the Python builtin; asyncpg raises it
# ("unexpected connection_lost() call") in some code paths without
# SQLAlchemy wrapping it.
# `socket.gaierror` is what asyncpg raises on DNS resolution failure —
# it propagates uncaught through asyncpg.connect() and is NOT wrapped
# by SQLAlchemy when going through async_creator.
# `TimeoutError` / `asyncio.TimeoutError`: asyncpg.connect() wraps its
# internal `asyncio.wait_for(...)` and raises asyncio.TimeoutError on
# connect-timeout (e.g. RDS failover black-holing packets). SQLAlchemy
# does NOT recognize it as a DBAPI error, so it escapes unwrapped. On
# Python 3.11+ asyncio.TimeoutError is aliased to the builtin
# TimeoutError; on 3.8–3.10 they are distinct classes, so we list both.
_NETWORK_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    ConnectionError,
    socket.gaierror,
    TimeoutError,
    asyncio.TimeoutError,
)

RETRYABLE_EXCEPTIONS = _DRIVER_EXCEPTIONS + _NETWORK_EXCEPTIONS


def is_transient(error: BaseException) -> bool:
    """Whether `error` is a transient DB error, directly or by `__cause__`.

    Along the cause chain only driver errors count: the bare network
    builtins are retryable only when raised at the top level.
    """
    driver_excs = _DRIVER_EXCEPTIONS + _asyncpg_exceptions()
    if isinstance(error, driver_excs + _NETWORK_EXCEPTIONS):
        return True
    seen: Set[int] = set()
    current = error.__cause__
    while current is not None and id(current) not in seen:
        if isinstance(current, driver_excs):
            return True
        seen.add(id(current))
        current = current.__cause__
    return False


# 5 attempts × exp backoff capped at 5s ≈ ~10s of backoff sleeps — covers
# typical brief outages (DNS hiccup, sub-second RDS reconnect) and the
# short tail of longer ones; very long outages (multi-second RDS failover)
# will still raise.
# NB: this is backoff *sleep* time only. On a TCP black-hole each attempt
# also blocks on asyncpg's connect timeout (15s, see _make_asyncpg_creator
# in db_utils), so worst-case wall time before giving up is closer to
# ~10s backoff + 5×15s connect ≈ 85s, not ~10s.
_DEFAULT_MAX_RETRIES = 5
_DEFAULT_INITIAL_BACKOFF = 1.0
_DEFAULT_MAX_BACKOFF = 5.0


def summarize(e: BaseException) -> str:
    """One-line exception summary (SQLAlchemy errors are multi-line)."""
    return f'{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ""}'


def _new_backoff(initial_backoff: float,
                 max_backoff: float) -> common_utils.Backoff:
    return common_utils.Backoff(initial_backoff=initial_backoff,
                                max_backoff_factor=int(max_backoff //
                                                       initial_backoff))


def _check_bounds(max_retries: Optional[int], deadline: Optional[float]):
    if max_retries is None and deadline is None:
        raise ValueError('Either max_retries or deadline must be set')
    if max_retries is not None and max_retries < 1:
        raise ValueError(
            f'max_retries must be greater than 0, got {max_retries}')


def _budget_spent(failures: int, max_retries: Optional[int],
                  deadline: Optional[float]) -> bool:
    if max_retries is not None and failures >= max_retries:
        return True
    return deadline is not None and time.monotonic() >= deadline


def _log_retry(failures: int, max_retries: Optional[int], delay: float,
               e: BaseException) -> None:
    budget = f'/{max_retries}' if max_retries is not None else ''
    logger.warning(f'Transient DB error (attempt {failures}{budget}), '
                   f'retrying in {delay:.1f}s: {summarize(e)}')


def with_db_retries(fn: Callable[[int], T],
                    max_retries: Optional[int] = _DEFAULT_MAX_RETRIES,
                    *,
                    initial_backoff: float = _DEFAULT_INITIAL_BACKOFF,
                    max_backoff: float = _DEFAULT_MAX_BACKOFF,
                    deadline: Optional[float] = None) -> T:
    """Run `fn(attempt)` with retry/backoff on transient DB errors.

    `fn` receives the attempt index (0 = first call, 1+ = retry replay). Use
    it to keep otherwise-non-idempotent operations safe across commit-lost
    retries (e.g. accept the target state as already-applied on attempt>0).
    Callers that don't need attempt-awareness can just ignore the arg.

    Gives up after `max_retries` attempts or once `time.monotonic()` passes
    `deadline`, whichever comes first; pass `max_retries=None` to bound the
    loop by the deadline alone.
    """
    _check_bounds(max_retries, deadline)
    backoff = _new_backoff(initial_backoff, max_backoff)
    attempt = 0
    while True:
        try:
            result = fn(attempt)
            if attempt > 0:
                logger.info(
                    f'Transient DB error recovered after {attempt} retries.')
            return result
        except Exception as e:  # pylint: disable=broad-except
            if not is_transient(e):
                raise
            attempt += 1
            if _budget_spent(attempt, max_retries, deadline):
                logger.error(f'Transient DB error: giving up after '
                             f'{attempt} attempts; {summarize(e)}')
                raise
            delay = backoff.current_backoff()
            _log_retry(attempt, max_retries, delay, e)
            time.sleep(delay)


async def with_db_retries_async(
        coro_fn: Callable[[int], Awaitable[T]],
        max_retries: Optional[int] = _DEFAULT_MAX_RETRIES,
        *,
        initial_backoff: float = _DEFAULT_INITIAL_BACKOFF,
        max_backoff: float = _DEFAULT_MAX_BACKOFF,
        deadline: Optional[float] = None) -> T:
    """Async equivalent of with_db_retries."""
    _check_bounds(max_retries, deadline)
    backoff = _new_backoff(initial_backoff, max_backoff)
    attempt = 0
    while True:
        try:
            result = await coro_fn(attempt)
            if attempt > 0:
                logger.info(
                    f'Transient DB error recovered after {attempt} retries.')
            return result
        except Exception as e:  # pylint: disable=broad-except
            if not is_transient(e):
                raise
            attempt += 1
            if _budget_spent(attempt, max_retries, deadline):
                logger.error(f'Transient DB error: giving up after '
                             f'{attempt} attempts; {summarize(e)}')
                raise
            delay = backoff.current_backoff()
            _log_retry(attempt, max_retries, delay, e)
            await asyncio.sleep(delay)


def retry(fn):
    """Decorator: retry a sync function on transient DB errors.

    Safe only when the entire function body is idempotent under retry — use
    on pure-leaf DB functions (single session, no external side effects).
    For functions with non-DB side effects (event log, callback, log lines
    outside the session block), wrap just the session block inline with
    `with_db_retries` instead.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return with_db_retries(lambda _attempt: fn(*args, **kwargs))

    return wrapper


def retry_async(coro_fn):
    """Decorator: retry an async function on transient DB errors.

    Same idempotency caveat as `retry`.
    """

    @functools.wraps(coro_fn)
    async def wrapper(*args, **kwargs):
        return await with_db_retries_async(
            lambda _attempt: coro_fn(*args, **kwargs))

    return wrapper
