"""Give a bounded DB call a deadline the DB layer itself honours.

The authentication middlewares run their DB lookups on a small thread
executor under an ``asyncio.wait_for`` deadline. That deadline releases the
*caller* -- the request gets a fast 503 -- but not the *thread*: a thread
parked in libpq's ``poll()`` (a lock wait, a slow statement, an unreachable
server, or a socket whose fd was closed under it) keeps running until the DB
layer lets go. Enough pinned threads and the executor saturates, and every
authenticated request fails.

This module makes the DB work give up at (or before) the caller's deadline,
so the thread is freed too. Two layers, both keyed off one thread-local
deadline that the caller sets around the DB function:

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

2. Client side -- a psycopg2 wait callback ("green mode") that polls the
   libpq socket in bounded slices and gives up when the thread's deadline
   passes. This is the only thing that frees the *thread* in the two classes
   the server cannot help with: a frozen/unreachable server, and a socket
   whose fd number was closed and reused under the thread. It also enforces
   ``connect_timeout`` (libpq applies it only in its own blocking connect
   loop, not in the ``PQconnectPoll`` path green mode uses) and refuses to
   hand a fd back to libpq once the fd no longer identifies the connection's
   own socket -- letting libpq ``recv()`` on a foreign blocking socket would
   freeze the whole process, and ``send()`` on it would inject bytes into
   someone else's connection.

The wait callback is process-global to psycopg2, so it is installed
explicitly from the API-server process (see ``install_wait_callback``),
never as an import side effect: a plugin importing this module in a
request-executor process must not turn green mode on there. The engine
listener is inert without a thread-local deadline, so attaching it to every
Postgres engine is harmless in every process.
"""
import logging
import os
import select
import threading
import time
from typing import FrozenSet, Optional, Tuple
import weakref

from sqlalchemy import event

logger = logging.getLogger(__name__)

# psycopg2 is a server-only dependency; this module is imported by
# ``db_utils.get_engine`` which also runs in client-only (sqlite) installs.
# Import lazily-at-module-load and degrade: without psycopg2 the wait
# callback is never installed and ``DBDeadlineExceeded`` is only ever used as
# an isinstance target that cannot match.
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


class DBDeadlineExceeded(_OperationalErrorBase):  # type: ignore
    """The DB client gave up waiting on the socket.

    Subclasses ``psycopg2.OperationalError`` so that, when raised from the
    wait callback, psycopg2 closes the connection (``green_panic`` ->
    ``PQfinish``) and SQLAlchemy wraps it as ``OperationalError`` with this as
    ``.orig`` and ``connection_invalidated=True``. Raised during the connect
    handshake it propagates from ``psycopg2.connect()`` unchanged.

    ``reason`` names how the wait ended, for the metric label:
    ``client_deadline`` (the thread's deadline passed; the socket was still
    ours), ``client_deadline_fd_stolen`` / ``client_deadline_fd_closed`` (the
    fd no longer identified our socket -- the incident's stolen-fd class;
    raised whether or not a deadline is set), or ``connect_timeout`` (the
    DSN's ``connect_timeout`` passed during the handshake).
    """

    def __init__(self, *args, reason: str = 'client_deadline') -> None:
        super().__init__(*args)
        self.reason = reason


# Postgres SQLSTATEs the server-side bounds raise, mapped to metric-label
# reasons. 57014 = statement_timeout (query cancelled), 55P03 = lock_timeout
# (lock not available), 25P03 = idle_in_transaction_session_timeout (the
# killed session's own next statement, when the client reads the FATAL; if
# nobody was reading, that client sees a closed connection instead).
TIMEOUT_PGCODE_REASONS = {
    '57014': 'statement_timeout',
    '55P03': 'lock_timeout',
    '25P03': 'idle_in_transaction',
}

# Margin between the server-side bounds and the client deadline, so the
# server's error (which carries a SQLSTATE) normally arrives first.
_SERVER_MARGIN_MS = 500
# lock_timeout is set this far under statement_timeout so a lock wait reports
# the distinct 55P03 rather than 57014.
_LOCK_UNDER_MS = 100
# Floor for any server-side timeout: a value <= 0 would be "no timeout".
_MIN_TIMEOUT_MS = 50
# Longest a single poll slice sleeps while a deadline is set. Bounds how long
# a thread can sleep on a fd that was closed and reused under it (the kernel
# keeps a sleeping poll on the old socket) before the identity check runs.
_MAX_SLICE_MS = 1000


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
# Socket identity (st_dev, st_ino) of each connection's libpq socket, recorded
# while the connection is established and compared before every later libpq
# I/O to detect a stolen/closed fd. Keyed by the psycopg2 connection object
# (weak, so it does not keep connections alive).
_sock_ident: 'weakref.WeakKeyDictionary' = weakref.WeakKeyDictionary()
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


# --- exception classification (shared by callers and retry logic) ----------


def deadline_reason(exc: BaseException) -> Optional[str]:
    """The metric reason if ``exc`` is a deadline our bounds made, else None.

    Handles both the wrapped case (SQLAlchemy ``DBAPIError`` with ``.orig`` a
    ``DBDeadlineExceeded`` or a server timeout SQLSTATE) and the unwrapped
    driver exception.
    """
    orig = getattr(exc, 'orig', exc)
    if isinstance(orig, DBDeadlineExceeded):
        return orig.reason
    pgcode = getattr(orig, 'pgcode', None)
    if pgcode is None:
        return None
    return TIMEOUT_PGCODE_REASONS.get(pgcode)


def is_deadline_error(exc: BaseException) -> bool:
    """Whether ``exc`` was produced by a deadline bound (client or server)."""
    return deadline_reason(exc) is not None


def is_transient_driver_error(exc: BaseException) -> bool:
    """Whether ``exc`` is a psycopg2 operational/interface error.

    A connection dropped or closed under the call (one of the fd-corruption
    victims), a lost server, a pooler that went away: an operational failure
    of the database layer, never the request's fault, so the caller answers
    with a retryable status. "Transient" is the common case, not a guarantee:
    psycopg2 reports a bad password (28P01) as an OperationalError too, and a
    retry will not fix that -- the log line the caller writes carries the real
    error. Gated on the psycopg2 classes on purpose -- ``sqlite3
    .OperationalError`` also covers schema errors ("no such table"), which are
    programming errors and propagate unchanged.
    """
    if psycopg2 is None:
        return False
    orig = getattr(exc, 'orig', exc)
    return isinstance(orig,
                      (psycopg2.OperationalError, psycopg2.InterfaceError))


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
    # no server-side bound (the client-side deadline still frees the thread);
    # the auth path issues only single-row statements and never streams.
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


# --- client-side psycopg2 wait callback ------------------------------------


def _ident(fd: int) -> Optional[Tuple[int, int]]:
    try:
        st = os.fstat(fd)
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def _fileno(conn) -> int:
    try:
        return conn.fileno()
    except Exception:  # pylint: disable=broad-except
        return -1


def _record_ident(conn, fd: int) -> None:
    ident = _ident(fd)
    if ident is not None:
        _sock_ident[conn] = ident


def _fd_status(conn) -> str:
    """Classify the connection's current fd against its recorded identity.

    ``fd_closed`` -- the number is closed (``EBADF``); ``fd_stolen`` -- the
    number is open but now identifies a different file (reused under us);
    ``ok`` -- still our socket, or nothing recorded yet.
    """
    recorded = _sock_ident.get(conn)
    if recorded is None:
        return 'ok'
    now = _ident(_fileno(conn))
    if now is None:
        return 'fd_closed'
    if now != recorded:
        return 'fd_stolen'
    return 'ok'


def _check_fd(conn) -> None:
    """Raise before libpq touches a fd that is no longer this connection's.

    The incident's class: the fd number was closed and reused under the
    thread. Handing it to libpq would ``recv()``/``send()`` on someone else's
    file -- a blocking socket freezes the process with the GIL held. This is
    a mitigation, not a proof: it cannot close the microsecond race between
    this check and psycopg2's own syscall, and it does not run during the
    connection handshake (see ``_connecting``: libpq swaps sockets there, so
    the identity is not stable) -- a fd stolen inside those few milliseconds
    is caught at the next wait if the deadline expires, or produces a garbage
    error from libpq reading the foreign (readable, hence non-blocking) fd.
    The real fix is to stop closing the fd twice.

    When this raises, psycopg2 answers with ``PQfinish`` on the connection,
    which closes the fd NUMBER -- now the new owner's. That is one collateral
    stray close per detected theft (the same class of event as the root
    cause, at ~1 per detection), unavoidable from Python.
    """
    status = _fd_status(conn)
    if status != 'ok':
        raise DBDeadlineExceeded(
            f'DB connection fd {_fileno(conn)} is no longer this '
            f'connection\'s socket ({status})',
            reason=f'client_deadline_{status}')


def _connecting(conn) -> bool:
    """Whether the connection handshake (``PQconnectPoll``) is still running.

    Two things are different while it runs. libpq may close and reopen its
    socket (address fallback, e.g. ``localhost`` -> ``::1`` refused ->
    ``127.0.0.1``; multi-host DSNs), so the fd identity is not stable and the
    fd gate must not run. And it is the phase ``connect_timeout`` bounds.

    Known limitation of green mode in this phase: psycopg2 drives
    ``PQconnectPoll`` with the GIL held, and libpq resolves every host of a
    multi-host DSN *after the first* inside ``PQconnectPoll`` (the first host
    is resolved in ``PQconnectStart``, which psycopg2 wraps in
    ``Py_BEGIN_ALLOW_THREADS``). So a DSN with several *hostnames* whose
    earlier host fails does a DNS lookup for the fallback host with the GIL
    held -- during a resolver outage that stalls the whole process for the
    resolver timeout, where sync psycopg2 stalled one thread. Single-host
    DSNs, IP literals and ``hostaddr=`` are unaffected; prefer those for
    multi-host DSNs on the API server.
    """
    return conn.status not in _STEADY_STATUSES


def _connect_timeout(conn) -> Optional[float]:
    """The connection's ``connect_timeout`` in seconds, or None if unset.

    libpq applies ``connect_timeout`` only inside its own blocking connect
    loop; the ``PQconnectPoll`` path green mode uses leaves it to the caller,
    so the callback enforces it. The value is readable from the DSN during
    the handshake.
    """
    try:
        raw = conn.get_dsn_parameters().get('connect_timeout')
    except Exception:  # pylint: disable=broad-except
        return None
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _effective_deadline(
        thread_at: Optional[float],
        connect_at: Optional[float]) -> Tuple[Optional[float], Optional[str]]:
    """The earliest of the thread deadline and the connect-phase deadline."""
    if connect_at is None:
        return (thread_at, 'client') if thread_at is not None else (None, None)
    if thread_at is None or connect_at <= thread_at:
        return connect_at, 'connect'
    return thread_at, 'client'


def _raise_deadline(conn, fd: int, reason: Optional[str],
                    connecting: bool) -> None:
    """Raise ``DBDeadlineExceeded`` for the wait that just ran out of time.

    During the handshake the connection is closed here first. psycopg2 only
    calls ``PQfinish`` for a connect that failed inside the wait callback when
    the connection object is deallocated (``conn_connect`` marks it
    ``closed = 2``, "still requires cleanup"), and the exception's traceback
    holds this frame -- and with it ``conn`` -- so that deallocation waits for
    the next cyclic GC pass. Sync psycopg2 closes a timed-out connect at once;
    doing the same here keeps a burst of connect deadlines (a pooler that is
    down or queueing) from parking half-open client sockets on the pooler
    until the collector runs. ``close()`` takes the connection lock, which
    nothing holds during the handshake, and the later deallocation finds
    ``pgconn`` already gone. After the handshake psycopg2 itself closes the
    connection on a callback error (``green_panic`` -> ``PQfinish``), so
    nothing is done here.
    """
    if connecting:
        conn.close()
    if reason == 'connect':
        # libpq's own wording for its connect_timeout. One deadline covers
        # the whole handshake: a multi-host DSN fails at the first host that
        # does not answer instead of moving on to the next (libpq's blocking
        # connect applies the timeout per host).
        raise DBDeadlineExceeded('timeout expired', reason='connect_timeout')
    status = 'ok' if connecting else _fd_status(conn)
    label = 'client_deadline' if status == 'ok' else f'client_deadline_{status}'
    raise DBDeadlineExceeded(
        f'DB deadline exceeded while waiting for the socket '
        f'(fd={fd}, fd_check={status})',
        reason=label)


def wait_callback(conn) -> None:
    """psycopg2 wait callback: poll(2)-based, deadline- and fd-aware.

    With no thread-local deadline this waits exactly like libpq (poll(fd, -1)),
    so a process with the callback installed behaves like sync psycopg2 for
    unbounded callers -- except it still enforces ``connect_timeout`` and still
    refuses to touch a fd that is no longer the connection's socket.

    ``conn.poll()`` is only ever called when the socket reported readiness (or
    an error), as libpq requires: calling ``PQconnectPoll`` on a socket that
    is not ready confuses its state machine (it tries to send the startup
    packet and fails with a misleading error). A poll slice that expires
    without readiness only re-checks the deadline and the fd identity.
    """
    # select.poll(), never select.select(): select fails at fd >= 1024 and
    # uvicorn workers that spawn kubectl children exceed that.
    connect_at: Optional[float] = None
    connecting = _connecting(conn)
    if not connecting:
        # Before the first libpq I/O of this wait (for a query, that is the
        # flush of the statement just sent).
        _check_fd(conn)
    state = conn.poll()
    while state != _psycopg2_ext.POLL_OK:
        if state == _psycopg2_ext.POLL_READ:
            flags = select.POLLIN
        elif state == _psycopg2_ext.POLL_WRITE:
            flags = select.POLLOUT
        else:
            raise psycopg2.OperationalError(f'bad poll state: {state!r}')
        fd = conn.fileno()
        still_connecting = _connecting(conn)
        if connecting or still_connecting or conn not in _sock_ident:
            # Track the socket libpq holds *now*: during the handshake it may
            # have swapped sockets since the last wait, and the identity
            # recorded on the last handshake wait (or on the first wait after
            # it) is the socket libpq kept.
            _record_ident(conn, fd)
        connecting = still_connecting
        if connecting and connect_at is None:
            timeout = _connect_timeout(conn)
            if timeout is not None:
                connect_at = time.monotonic() + timeout
        deadline_at, reason = _effective_deadline(
            get_deadline(), connect_at if connecting else None)
        poller = select.poll()
        poller.register(fd, flags)
        while True:
            if deadline_at is None:
                poller.poll()
                ready = True
            else:
                remaining = deadline_at - time.monotonic()
                if remaining <= 0:
                    _raise_deadline(conn, fd, reason, connecting)
                ready = bool(
                    poller.poll(min(int(remaining * 1000) + 1, _MAX_SLICE_MS)))
            if not connecting:
                # Also after a slice that expired without readiness: the fd
                # may have been closed/reused while we slept, and the kernel
                # keeps a sleeping poll on the *old* socket.
                _check_fd(conn)
            if ready:
                break
        state = conn.poll()


def install_wait_callback() -> bool:
    """Install the process-global psycopg2 wait callback (green mode).

    Call once from the API-server process (its startup), so it lands only in
    processes that serve the app. Idempotent. Returns False (with a warning)
    when psycopg2 cannot be imported: a sqlite-only deployment must keep
    starting -- there is no psycopg2 wait to bound there.

    Process-global: any child created with the ``fork`` start method inherits
    it. The API server creates its worker processes with ``spawn``, which
    imports the app afresh and never runs this.
    """
    if _psycopg2_ext is None:
        logger.warning('psycopg2 is not importable; DB waits in this process '
                       'are not bounded by the auth-path deadline (only '
                       'relevant with a Postgres state database).')
        return False
    _psycopg2_ext.set_wait_callback(wait_callback)
    return True


def uninstall_wait_callback() -> None:
    if _psycopg2_ext is not None:
        _psycopg2_ext.set_wait_callback(None)


def get_wait_callback():
    """The currently installed wait callback, or None (for tests)."""
    if _psycopg2_ext is None:
        return None
    return _psycopg2_ext.get_wait_callback()
