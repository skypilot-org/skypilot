"""Give a bounded DB call a deadline the DB layer itself honours.

The authentication middlewares run their DB lookups on a small thread
executor under an ``asyncio.wait_for`` deadline. That deadline releases the
*caller* -- the request gets a fast 503 -- but not the *thread*: a thread
parked in libpq's ``poll()`` (a lock wait, a slow statement, an unreachable
server, or a socket whose fd was closed under it) keeps running until the DB
layer lets go. Enough pinned threads and the executor saturates, and every
authenticated request fails.

This module makes the DB work give up at (or before) the caller's deadline,
so the thread is freed too. The bound is keyed off a thread-local deadline
that the caller sets around the DB function:

1. Server side -- an engine listener that prepends ``SET LOCAL
   statement_timeout / lock_timeout / idle_in_transaction_session_timeout``
   to the first statement of every transaction opened while a deadline is
   active. Postgres then leaves a lock queue, cancels a slow statement, and
   kills an orphaned transaction (releasing its row locks). These are the
   only things that free the *server*. ``SET LOCAL`` reaches the server on a
   direct connection and through a transaction-mode pooler, and is scoped to
   the one transaction. Prepending to the statement text (rather than a
   separate ``cursor.execute``) costs no extra round trip and keeps the
   statement inside SQLAlchemy's DBAPI error handling. "First statement of
   the transaction" is read from the driver's own transaction state
   (psycopg2 ``conn.status``), not from a per-connection mark, so it holds
   whatever other engine listeners run first (see ``_bounded_statement``).

The engine listener is inert without a thread-local deadline, so attaching
it to every Postgres engine is harmless in every process.
"""
import logging
import threading
import time
from typing import FrozenSet, Optional, Tuple
import weakref

from sqlalchemy import event

logger = logging.getLogger(__name__)

# psycopg2 is a server-only dependency; this module is imported by
# ``db_utils.get_engine`` which also runs in client-only (sqlite) installs.
# Import lazily-at-module-load and degrade: without psycopg2 no Postgres
# engine exists for the listener to attach to, and ``_STATUS_READY`` is None
# so ``_bounded_statement`` passes every statement through unchanged.
try:
    import psycopg2  # pylint: disable=import-outside-toplevel
    import psycopg2.extensions as _psycopg2_ext
    _OperationalErrorBase: type = psycopg2.OperationalError
    # psycopg2's exported steady connection states. While the connection
    # handshake runs, ``conn.status`` holds an internal, unexported value
    # (CONN_STATUS_SETUP before the first poll, then CONN_STATUS_CONNECTING);
    # ``STATUS_SETUP`` itself is never observable after the first ``poll()``.
    _STEADY_STATUSES: FrozenSet[int] = frozenset(
        (_psycopg2_ext.STATUS_READY, _psycopg2_ext.STATUS_BEGIN,
         _psycopg2_ext.STATUS_PREPARED))
    # "No transaction open": psycopg2 sends its implicit BEGIN together with
    # the first statement (status READY -> BEGIN) and is back to READY after
    # COMMIT/ROLLBACK. Read by the SET LOCAL listener to find the first
    # statement of each transaction.
    _STATUS_READY: Optional[int] = _psycopg2_ext.STATUS_READY
except ImportError:  # pragma: no cover - exercised only in sqlite-only installs
    psycopg2 = None  # type: ignore
    _psycopg2_ext = None  # type: ignore
    _OperationalErrorBase = Exception
    _STEADY_STATUSES = frozenset()
    _STATUS_READY = None

# Margin between the server-side bounds and the client deadline, so the
# server's error (which carries a SQLSTATE) normally arrives first.
_SERVER_MARGIN_MS = 500
# lock_timeout is set this far under statement_timeout so a lock wait reports
# the distinct 55P03 rather than 57014.
_LOCK_UNDER_MS = 100
# Floor for any server-side timeout: a value <= 0 would be "no timeout".
_MIN_TIMEOUT_MS = 50


def _timeout_values_ms(total_ms: int) -> Tuple[int, int, int]:
    """(statement_timeout, lock_timeout, idle_in_transaction) in ms.

    lock < statement so a lock wait reports the distinct 55P03, and
    idle_in_transaction >= statement so it never preempts a running statement
    (it only bounds an *idle* -- i.e. orphaned -- transaction).
    """
    statement_ms = max(_MIN_TIMEOUT_MS, total_ms - _SERVER_MARGIN_MS)
    lock_ms = max(_MIN_TIMEOUT_MS, statement_ms - _LOCK_UNDER_MS)
    idle_ms = max(_MIN_TIMEOUT_MS, total_ms)
    return statement_ms, lock_ms, idle_ms


def _set_local_prefix(total_ms: int) -> str:
    """The ``SET LOCAL ...; `` prefix for a transaction with ``total_ms`` left.

    No ``%`` in the text, so psycopg2's parameter interpolation of the
    statement it is prepended to is unaffected.
    """
    statement_ms, lock_ms, idle_ms = _timeout_values_ms(total_ms)
    return (f'SET LOCAL statement_timeout = {statement_ms}; '
            f'SET LOCAL lock_timeout = {lock_ms}; '
            f'SET LOCAL idle_in_transaction_session_timeout = {idle_ms}; ')


_local = threading.local()
# Engines the SET LOCAL listener is attached to (see ``install`` /
# ``is_bounding``). Weak, so it never keeps an engine alive.
_installed: 'weakref.WeakSet' = weakref.WeakSet()

# --- thread-local deadline -------------------------------------------------


def set_deadline(deadline_monotonic: float) -> None:
    """Set the current thread's DB deadline (a ``time.monotonic()`` value)."""
    _local.deadline = deadline_monotonic


def clear_deadline() -> None:
    _local.deadline = None


def get_deadline() -> Optional[float]:
    return getattr(_local, 'deadline', None)


# --- server-side SET LOCAL listener ----------------------------------------


def _is_async_engine(engine) -> bool:
    # AsyncEngine proxies a sync_engine; the deadline machinery is psycopg2
    # (sync) only, so async engines get no listener.
    return hasattr(engine, 'sync_engine')


def _bounded_statement(conn, cursor, statement: str, executemany: bool) -> str:
    """``statement``, prefixed with SET LOCAL if it opens a bounded transaction.

    The prefix goes on the first statement of a transaction executed while a
    thread-local deadline is set. Everything else passes through unchanged.

    "First statement of the transaction" is the driver's own transaction
    state: psycopg2 sends its implicit BEGIN with the first statement (status
    READY -> BEGIN) and is back to READY after COMMIT/ROLLBACK, so a statement
    that finds the connection READY is the one that opens the transaction.
    Reading that (rather than keeping a per-connection mark cleared by the
    ``begin`` event) needs no state that can go stale on a pooled connection,
    and does not depend on listener order: a statement another ``begin``
    listener issues (e.g. an engine-wide idle-in-transaction bound) is simply
    the first statement, gets the prefix, and the caller's own first statement
    then does not -- exactly one prefix per transaction either way.
    """
    deadline = get_deadline()
    if deadline is None:
        return statement
    dbapi_conn = conn.connection.dbapi_connection
    if _STATUS_READY is None or getattr(dbapi_conn, 'status',
                                        None) != _STATUS_READY:
        # A transaction is already open (or this is not a psycopg2
        # connection): the transaction's first statement carried the prefix.
        return statement
    # SET LOCAL outside a transaction block is a WARNING no-op, and an
    # autocommit connection never has one (advisory-lock holders use
    # autocommit and reach here). Skip rather than spam the server log.
    if getattr(dbapi_conn, 'autocommit', False):
        return statement
    # executemany repeats the statement text per parameter set, so the prefix
    # would run once per row; skip it. A server-side (named) cursor wraps the
    # text in `DECLARE ... CURSOR FOR <statement>`, where a leading SET LOCAL
    # is a syntax error. A transaction OPENED by either of them therefore gets
    # no server-side bound; the auth path issues only single-row statements
    # and never streams.
    if executemany or getattr(cursor, 'name', None):
        return statement
    # idle_in_transaction_session_timeout only counts time the transaction
    # sits idle between statements; the auth path is idle for microseconds
    # (execute -> fetchone -> commit), so it only bites an *orphaned*
    # transaction -- exactly the incident's blocker -- bounding it to the
    # budget instead of forever.
    total_ms = int((deadline - time.monotonic()) * 1000)
    # One round trip: prepend to the statement text (simple-query
    # multi-statement) instead of a separate cursor.execute, so the bounds
    # are set inside SQLAlchemy's DBAPI error handling. psycopg2 sends its
    # implicit BEGIN before this, so the SET LOCALs land inside the
    # transaction they bound.
    return _set_local_prefix(total_ms) + statement


def install(engine) -> None:
    """Attach the SET LOCAL listener to a Postgres psycopg2 (sync) engine.

    No-op for async engines, non-Postgres dialects and other drivers (the
    listener reads psycopg2's connection status). Safe to call in any process:
    the listener does nothing unless a thread-local deadline is set, which only
    the auth path does. Idempotent.
    """
    if _is_async_engine(engine):
        return
    if engine.dialect.name != 'postgresql':
        return
    if engine.dialect.driver != 'psycopg2':
        return
    if engine in _installed:
        return

    @event.listens_for(engine, 'before_cursor_execute', retval=True)
    def _before_cursor_execute(  # pylint: disable=unused-variable
            conn, cursor, statement, parameters, context, executemany):
        del context
        return (_bounded_statement(conn, cursor, statement,
                                   executemany), parameters)

    _installed.add(engine)


def is_bounding(engine) -> bool:
    """Whether a transaction this thread opens on ``engine`` now gets bounded.

    True when the SET LOCAL listener is attached to ``engine`` AND the thread
    has a deadline set -- i.e. the transaction's first statement will carry
    the deadline-sized ``SET LOCAL`` trio. Callers that otherwise issue their
    own fixed ``SET LOCAL`` statements use this to skip them (no duplicate
    round trips), and only then: an engine without the listener keeps the
    caller's own bounds even under a deadline.
    """
    return get_deadline() is not None and engine in _installed
