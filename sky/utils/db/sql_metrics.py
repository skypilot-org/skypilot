"""SQLAlchemy instrumentation feeding the families in ``sky.metrics.db``.

``install(engine, db)`` attaches everything to one engine. Call it once
per engine, at creation, so no statement executed before the instrument
exists goes unseen — ``sky.utils.db.db_utils.get_engine`` does this for
every engine it builds, and anything that builds its own engine should do
the same.

What is measured, and where each number comes from:

* **execute** — ``before_cursor_execute`` to ``after_cursor_execute``.
  Includes the result set coming over the wire, because psycopg2 and
  asyncpg both buffer the whole result during ``execute``.
* **rowfetch** — the wrapped fetch strategy on the result object. This is
  row handoff into Python, not wire time.
* **transaction span** — the ``begin`` event to ``commit``/``rollback``.
  Excludes the commit round trip itself, which SQLAlchemy dispatches
  ``commit`` just *before*; that is measured separately.
* **commit** — the dialect's ``do_commit``, wrapped.
* **rows** — ``cursor.rowcount`` where the driver reports one (psycopg2
  does, for SELECT as well as DML), else counted by the fetch wrapper.
* **result bytes** — summed by the fetch wrapper over the rows handed
  back. No driver reports the real number, so this is computed
  client-side: exact up to a cell budget, sampled above it.
* **statement bytes** — statement text plus bound parameters, on writes.
* **connect / acquire / lifetime / pool saturation** — pool events, plus
  a wrapper on ``Pool.connect`` for total acquisition time.

Every listener is defensive: a defect in here must never break database
access, so each body is wrapped and failures are logged once and then
swallowed. The whole module is inert unless ``sky.metrics.db.ENABLED``.
"""

import logging
import operator
import os
import threading
import time
from typing import Any, Optional, Tuple
import weakref

import sqlalchemy
from sqlalchemy import event
from sqlalchemy.sql import compiler as sql_compiler

from sky.metrics import db as db_metrics

logger = logging.getLogger(__name__)

# Engine role label values. On Postgres every SkyPilot component shares
# one physical database, and ``get_engine`` caches one engine per
# connection string and variant, so the role — not the database name — is
# what separates profiles that genuinely differ.
DB_STATE = 'state'
DB_STATE_DIRECT = 'state_direct'
DB_STATE_NOPOOL = 'state_nopool'
DB_STATE_ASYNC = 'state_async'

_OP_BY_VISIT = {
    'select': db_metrics.OP_SELECT,
    'compound_select': db_metrics.OP_SELECT,
    'insert': db_metrics.OP_INSERT,
    'update': db_metrics.OP_UPDATE,
    'delete': db_metrics.OP_DELETE,
}
_OP_OTHER = db_metrics.OP_OTHER
_OP_DDL = db_metrics.OP_DDL
_WRITE_OPS = frozenset(
    (db_metrics.OP_INSERT, db_metrics.OP_UPDATE, db_metrics.OP_DELETE))

_OUTCOME_OK = 'ok'
_OUTCOME_ERROR = 'error'
_OUTCOME_COMMIT = 'commit'
_OUTCOME_ROLLBACK = 'rollback'

# Keys in the per-connection ``info`` dict. Namespaced: that dict is
# shared with anything else stashing state on a connection.
_KEY_EXEC = '_sky_db_exec_started'
_KEY_RESULT = '_sky_db_result_labels'
_KEY_TX = '_sky_db_tx_started'
_KEY_CONNECT = '_sky_db_connect_started'
_KEY_BORN = '_sky_db_connect_born'

# Derived (table, op) is cached on the compiled construct itself, which
# lives exactly as long as SQLAlchemy's compiled cache entry for that
# statement. So derivation runs once per distinct statement shape, not
# once per execution.
_LABEL_ATTR = '_sky_db_labels'

# Cells (rows x columns) we are willing to walk to size a result exactly.
# Sampling was tried first and rejected in both directions: extrapolating
# from the leading rows blows a single outlier row up across the whole
# result (measured 40x over), and striding across the batch misses the
# outlier entirely (measured 99% under) — and the outlier row is the thing
# this metric exists to catch. The columnar walk below is ~10x cheaper
# than a per-cell loop, which is what makes exactness affordable: 100k
# two-column rows cost ~1.7ms, against an execute that just moved those
# rows over the wire. Past this budget the result is already tens of MiB
# and lands in the top buckets whatever the estimate says, so cost wins.
_EXACT_CELL_BUDGET = 4_000_000
# Same idea for the parameter sets of an executemany.
_PARAM_SAMPLE_SETS = 8
# Assumed size of a non-string scalar (int, float, bool, None, datetime).
# Wrong in detail, irrelevant at the scale these buckets resolve.
_SCALAR_BYTES = 8
# Guard against pathological FROM nesting while walking to a table name.
_MAX_FROM_DEPTH = 6
# Marker attribute on our own wrappers, so re-arming is idempotent.
_WRAPPED_ATTR = '_sky_db_wrapped'

# Engines already instrumented. Weak so a disposed engine can be
# collected; engines from ``get_engine`` are cached for the process
# lifetime anyway.
_instrumented: 'weakref.WeakSet' = weakref.WeakSet()
_instrumented_lock = threading.Lock()

# One warning per process, not one per statement: a broken instrument
# would otherwise out-log the application.
_warned = False

# Bound metric children, keyed by family and label values.
# ``Histogram.labels()`` already memoizes its children, so this caches
# nothing the library does not — it only skips the label validation and
# lock that ``labels()`` redoes on every call, which is ~1.4us of the
# ~1.5us it costs. Bounded by the number of real label combinations, the
# same bound prometheus_client itself lives under.
_children: dict = {}


def _child(family: Any, *labels: str) -> Any:
    key = (family, labels)
    child = _children.get(key)
    if child is None:
        child = family.labels(*labels)
        _children[key] = child
    return child


def _warn_once(message: str) -> None:
    global _warned
    if _warned:
        return
    _warned = True
    logger.warning('%s Database metrics may be incomplete.',
                   message,
                   exc_info=True)


def install(engine: Any, db: str) -> None:
    """Instrument one engine so its statements land on ``sky.metrics.db``.

    Args:
        engine: a SQLAlchemy ``Engine`` or ``AsyncEngine``. For an
            ``AsyncEngine`` the listeners go on its ``sync_engine``,
            which is where SQLAlchemy emits these events.
        db: the engine role label, e.g. ``DB_STATE``.

    Idempotent per engine, and a no-op when metrics are disabled.
    """
    if not db_metrics.ENABLED:
        return
    target = getattr(engine, 'sync_engine', engine)
    with _instrumented_lock:
        if target in _instrumented:
            return
        _instrumented.add(target)
    try:
        _install(target, db)
    except Exception:  # pylint: disable=broad-except
        _warn_once(f'Failed to instrument the {db!r} database engine.')


# --- label derivation -----------------------------------------------------


def _table_name(obj: Any, depth: int) -> Optional[str]:
    """Walk a FROM element down to the table the statement is about."""
    if obj is None or depth > _MAX_FROM_DEPTH:
        return None
    if isinstance(obj, sqlalchemy.Table):
        return obj.name
    # A join: attribute the statement to its leftmost table.
    left = getattr(obj, 'left', None)
    if left is not None:
        return _table_name(left, depth + 1)
    # An alias, subquery or CTE: unwrap it. We deliberately never use the
    # alias's own name — for an anonymous subquery that is generated per
    # construct, which is unbounded label cardinality.
    element = getattr(obj, 'element', None)
    if element is not None:
        return _table_name(element, depth + 1)
    final_froms = getattr(obj, 'get_final_froms', None)
    if final_froms is not None:
        inner = final_froms()
        if inner:
            return _table_name(inner[0], depth + 1)
    return None


def _compute_labels(compiled: Any) -> Tuple[str, str]:
    if isinstance(compiled, sql_compiler.DDLCompiler):
        target = getattr(getattr(compiled, 'statement', None), 'element', None)
        name = getattr(target, 'name', None)
        return (name if isinstance(name, str) else db_metrics.UNKNOWN_TABLE,
                _OP_DDL)
    statement = getattr(compiled, 'statement', None)
    if statement is None:
        return (db_metrics.UNKNOWN_TABLE, _OP_OTHER)
    op = _OP_BY_VISIT.get(getattr(statement, '__visit_name__', ''), _OP_OTHER)
    if op == _OP_OTHER:
        # ``sqlalchemy.text()`` and friends. We do not regex SQL text to
        # invent a table label.
        return (db_metrics.UNKNOWN_TABLE, _OP_OTHER)
    name = None
    try:
        if op == db_metrics.OP_SELECT:
            target = statement
            selects = getattr(target, 'selects', None)
            if selects:
                # A UNION: attribute it to the first branch.
                target = selects[0]
            froms = target.get_final_froms()
            if froms:
                name = _table_name(froms[0], 0)
        else:
            name = _table_name(statement.table, 0)
    except Exception:  # pylint: disable=broad-except
        name = None
    return (name or db_metrics.UNKNOWN_TABLE, op)


def _labels(context: Any) -> Tuple[str, str]:
    """(table, op) for an execution context, cached on the compiled form."""
    compiled = getattr(context, 'compiled', None)
    if compiled is None:
        # Driver-level statements (PRAGMA, isolation level probes) and
        # anything executed without a compiled construct.
        return (db_metrics.UNKNOWN_TABLE, _OP_OTHER)
    cached = getattr(compiled, _LABEL_ATTR, None)
    if cached is not None:
        return cached
    labels = _compute_labels(compiled)
    try:
        setattr(compiled, _LABEL_ATTR, labels)
    except (AttributeError, TypeError):
        # A compiled form that refuses attributes just re-derives; the
        # instrument stays correct, only the caching is lost.
        pass
    return labels


# --- payload sizing -------------------------------------------------------


def _value_bytes(value: Any) -> int:
    # len() on a str is the character count, which is the byte count only
    # for ASCII. Every SkyPilot payload worth catching here is JSON or
    # base64, so the error is small and always an under-estimate.
    if type(value) is str:  # pylint: disable=unidiomatic-typecheck
        return len(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    return _SCALAR_BYTES


def _param_set_bytes(params: Any) -> int:
    if params is None:
        return 0
    if isinstance(params, dict):
        return sum(_value_bytes(v) for v in params.values())
    if isinstance(params, (list, tuple)):
        return sum(_value_bytes(v) for v in params)
    return _value_bytes(params)


def _statement_bytes(statement: Any, parameters: Any, executemany: bool) -> int:
    """Bytes going out for one statement: text plus bound parameters."""
    total = len(statement) if isinstance(statement, str) else 0
    if parameters is None:
        return total
    if executemany and isinstance(parameters, (list, tuple)):
        count = len(parameters)
        if not count:
            return total
        # Sample rather than walk: an executemany can carry tens of
        # thousands of parameter sets, and this runs on the caller's
        # thread right after a database round trip.
        sampled = count if count < _PARAM_SAMPLE_SETS else _PARAM_SAMPLE_SETS
        subtotal = sum(_param_set_bytes(p) for p in parameters[:sampled])
        return total + subtotal * count // sampled
    return total + _param_set_bytes(parameters)


def _column_bytes(rows: Any, idx: int, count: int, first: Any) -> int:
    """Bytes of one column across a fetched batch.

    Classifies the column from its first value and then sums it with
    C-level ``map``/``len``, which is where the speed comes from. Falls
    back to a per-cell walk for that column alone whenever the
    classification cannot be trusted: a NULL first value says nothing
    about the rest, and SQLite columns are dynamically typed.
    """
    getter = operator.itemgetter(idx)
    if type(first) is str or isinstance(  # pylint: disable=unidiomatic-typecheck
            first, (bytes, bytearray, memoryview)):
        try:
            return sum(map(len, map(getter, rows)))
        except TypeError:
            # A NULL further down the column, or mixed types.
            pass
    elif first is not None:
        # A numeric / bool / datetime column. Postgres column types are
        # fixed, so one value classifies the column.
        return _SCALAR_BYTES * count
    return sum(map(_value_bytes, map(getter, rows)))


def _rows_bytes(rows: Any, count: int) -> int:
    """Total bytes of a fetched batch, column by column."""
    first_row = rows[0]
    cells = count * len(first_row)
    if cells > _EXACT_CELL_BUDGET:
        stride = cells // _EXACT_CELL_BUDGET + 1
        sample = rows[::stride]
        return _rows_bytes(sample, len(sample)) * count // len(sample)
    total = 0
    for idx, first in enumerate(first_row):
        total += _column_bytes(rows, idx, count, first)
    return total


def _rowcount(cursor: Any) -> Optional[int]:
    """Rows the driver reports for the statement just executed, or None.

    psycopg2 reports this for SELECT as well as for DML. sqlite3 reports
    -1 for SELECT, in which case the fetch wrapper counts instead.
    """
    try:
        rows = cursor.rowcount
    except Exception:  # pylint: disable=broad-except
        return None
    if not isinstance(rows, int) or rows < 0:
        return None
    return rows


class _PoolGaugeState:
    """Last-written value and bound child for one engine's saturation gauge.

    pid is tracked because a forked child inherits this state and must not
    report its parent's pid — the multiprocess collector keys its files on
    the real one.
    """

    __slots__ = ('pid', 'child', 'last')

    def __init__(self) -> None:
        self.pid: int = 0
        self.child: Any = None
        # Last value written. -1 so the first observation always differs.
        self.last: float = -1.0


# --- result consumption ---------------------------------------------------


class _InstrumentedFetch:
    """Times and sizes row consumption for one result.

    Swapped onto ``CursorResult.cursor_strategy``, which every
    consumption path funnels through — ``.all()``, ``.fetchall()``,
    iteration, ``.scalars()``, ``.mappings()``, ``yield_per()`` and the
    ORM.

    ``fetchone`` is deliberately *not* wrapped. It is called once per row,
    and timing it costs about a third of the runtime of row-at-a-time
    iteration to produce a number the batch paths already give. The
    consequence is that results consumed one row at a time (``.first()``,
    ``.scalar()``, plain iteration) report no rowfetch time and no result
    bytes; their row count still comes from ``cursor.rowcount``.
    """

    __slots__ = ('_inner', '_db', '_table', '_op', '_count_rows', '_seconds',
                 '_rows', '_bytes', '_batches', '_emitted', '_closed',
                 '_fetching')

    def __init__(self, inner: Any, db: str, table: str, op: str,
                 count_rows: bool) -> None:
        self._inner = inner
        self._db = db
        self._table = table
        self._op = op
        self._count_rows = count_rows
        self._seconds = 0.0
        self._rows = 0
        self._bytes = 0
        self._batches = 0
        self._emitted = False
        self._closed = False
        # The underlying strategy soft-closes the result from *inside*
        # fetchall/fetchmany once the cursor is exhausted, i.e. before it
        # hands the rows back. Emitting on close alone would therefore
        # always report an empty result. This flag defers the emit until
        # the rows have been accounted for.
        self._fetching = False

    def __getattr__(self, name: str) -> Any:
        # _inner is a slot; reaching __getattr__ for it means __init__ has
        # not run, and recursing would blow the stack instead of saying so.
        if name == '_inner':
            raise AttributeError(name)
        return getattr(self._inner, name)

    def _account(self, rows: Any) -> None:
        count = len(rows)
        if not count:
            return
        # Size first, then commit all three together. If sizing raises —
        # a driver whose rows are not integer-indexable, say — a
        # half-updated batch would emit as zero bytes over a nonzero row
        # count, which reads as a free empty result. Better to record
        # nothing for the batch than to record a lie.
        size = _rows_bytes(rows, count)
        self._batches += 1
        self._rows += count
        self._bytes += size

    def _finish_batch(self, rows: Any, start: float) -> Any:
        self._seconds += time.perf_counter() - start
        self._fetching = False
        try:
            self._account(rows)
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to size a SQL result.')
        if self._closed:
            self._emit()
        return rows

    def fetchall(self, result: Any, dbapi_cursor: Any) -> Any:
        self._fetching = True
        start = time.perf_counter()
        try:
            rows = self._inner.fetchall(result, dbapi_cursor)
        except BaseException:
            self._fetching = False
            raise
        return self._finish_batch(rows, start)

    def fetchmany(self,
                  result: Any,
                  dbapi_cursor: Any,
                  size: Optional[int] = None) -> Any:
        self._fetching = True
        start = time.perf_counter()
        try:
            rows = self._inner.fetchmany(result, dbapi_cursor, size)
        except BaseException:
            self._fetching = False
            raise
        return self._finish_batch(rows, start)

    def yield_per(self, result: Any, dbapi_cursor: Any, num: int) -> Any:
        # yield_per REPLACES result.cursor_strategy with a buffered one.
        # Re-wrap the replacement, otherwise switching to streaming
        # silently drops the instrument.
        ret = self._inner.yield_per(result, dbapi_cursor, num)
        replaced = getattr(result, 'cursor_strategy', None)
        if (replaced is not None and replaced is not self and
                not isinstance(replaced, _InstrumentedFetch)):
            self._inner = replaced
            result.cursor_strategy = self
        return ret

    def soft_close(self, result: Any, dbapi_cursor: Any) -> Any:
        ret = self._inner.soft_close(result, dbapi_cursor)
        self._closed = True
        if not self._fetching:
            self._emit()
        return ret

    def hard_close(self, result: Any, dbapi_cursor: Any) -> Any:
        ret = self._inner.hard_close(result, dbapi_cursor)
        self._closed = True
        if not self._fetching:
            self._emit()
        return ret

    def _emit(self) -> None:
        if self._emitted:
            return
        self._emitted = True
        if not self._batches:
            # Consumed one row at a time, or not at all. Reporting zero
            # bytes and zero seconds here would read as a free empty
            # result, which is worse than reporting nothing.
            return
        try:
            _child(db_metrics.SKY_APISERVER_DB_ROWFETCH_SECONDS, self._db,
                   self._table).observe(self._seconds)
            _child(db_metrics.SKY_APISERVER_DB_RESULT_BYTES, self._db,
                   self._table).observe(self._bytes)
            if self._count_rows:
                _child(db_metrics.SKY_APISERVER_DB_ROWS_RETURNED, self._db,
                       self._table, self._op).observe(self._rows)
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to record SQL result metrics.')


# --- wiring ---------------------------------------------------------------


def _install(engine: Any, db: str) -> None:
    """Attach every listener. Raises only if the engine is unusable."""
    pool_label = type(engine.pool).__name__

    # -- statement side --

    @event.listens_for(engine, 'before_cursor_execute')
    def _before_cursor_execute(conn, cursor, statement, parameters, context,
                               executemany):
        # Deliberately minimal: this runs immediately before the round
        # trip, so label derivation and payload sizing wait until after.
        del cursor, statement, parameters, context, executemany
        try:
            conn.info[_KEY_EXEC] = time.perf_counter()
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to start SQL statement timing.')

    @event.listens_for(engine, 'after_cursor_execute')
    def _after_cursor_execute(conn, cursor, statement, parameters, context,
                              executemany):
        try:
            started = conn.info.pop(_KEY_EXEC, None)
            if started is None:
                return
            duration = time.perf_counter() - started
            table, op = _labels(context)
            _child(db_metrics.SKY_APISERVER_DB_EXECUTE_SECONDS, db,
                   table).observe(duration)
            _child(db_metrics.SKY_APISERVER_DB_STATEMENTS_TOTAL, db, table, op,
                   _OUTCOME_OK).inc()
            rows = _rowcount(cursor)
            if rows is not None:
                _child(db_metrics.SKY_APISERVER_DB_ROWS_RETURNED, db, table,
                       op).observe(rows)
            if op in _WRITE_OPS:
                sent = _statement_bytes(statement, parameters, executemany)
                if sent:
                    _child(db_metrics.SKY_APISERVER_DB_STATEMENT_BYTES, db,
                           table).observe(sent)
            conn.info[_KEY_RESULT] = (table, op, rows is None)
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to record a SQL statement.')

    @event.listens_for(engine, 'after_execute')
    def _after_execute(conn, clauseelement, multiparams, params,
                       execution_options, result):
        del clauseelement, multiparams, params, execution_options
        try:
            meta = conn.info.pop(_KEY_RESULT, None)
            if meta is None or not getattr(result, 'returns_rows', False):
                return
            table, op, count_rows = meta
            strategy = getattr(result, 'cursor_strategy', None)
            if strategy is None:
                return
            result.cursor_strategy = _InstrumentedFetch(strategy, db, table, op,
                                                        count_rows)
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to instrument a SQL result.')

    @event.listens_for(engine, 'handle_error')
    def _handle_error(exception_context):
        try:
            conn = exception_context.connection
            if conn is not None:
                conn.info.pop(_KEY_EXEC, None)
                conn.info.pop(_KEY_RESULT, None)
            table, op = _labels(exception_context.execution_context)
            _child(db_metrics.SKY_APISERVER_DB_STATEMENTS_TOTAL, db, table, op,
                   _OUTCOME_ERROR).inc()
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to record a SQL error.')

    # -- transaction side --

    @event.listens_for(engine, 'begin')
    def _begin(conn):
        try:
            conn.info[_KEY_TX] = time.perf_counter()
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to start transaction timing.')

    def _close_transaction(conn, outcome: str) -> None:
        try:
            started = conn.info.pop(_KEY_TX, None)
            if started is None:
                return
            _child(db_metrics.SKY_APISERVER_DB_TRANSACTION_SECONDS, db,
                   outcome).observe(time.perf_counter() - started)
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to record a transaction span.')

    @event.listens_for(engine, 'commit')
    def _commit(conn):
        _close_transaction(conn, _OUTCOME_COMMIT)

    @event.listens_for(engine, 'rollback')
    def _rollback(conn):
        _close_transaction(conn, _OUTCOME_ROLLBACK)

    _wrap_do_commit(engine, db)

    # -- connection side --

    @event.listens_for(engine, 'do_connect')
    def _do_connect(dialect, conn_rec, cargs, cparams):
        del dialect, cargs, cparams
        try:
            if conn_rec is not None:
                conn_rec.info[_KEY_CONNECT] = time.perf_counter()
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to start connect timing.')
        # Returning None lets the dialect connect normally.
        return None

    @event.listens_for(engine, 'connect')
    def _connect(dbapi_connection, conn_rec):
        del dbapi_connection
        try:
            now = time.perf_counter()
            started = conn_rec.info.pop(_KEY_CONNECT, None)
            conn_rec.info[_KEY_BORN] = now
            _child(db_metrics.SKY_APISERVER_DB_CONNECTS_TOTAL, db,
                   pool_label).inc()
            if started is not None:
                _child(db_metrics.SKY_APISERVER_DB_CONNECT_SECONDS, db,
                       pool_label).observe(now - started)
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to record a database connect.')

    gauge_state = _PoolGaugeState()

    def _sync_pool_gauge(adjust: int = 0) -> None:
        checked_out = getattr(engine.pool, 'checkedout', None)
        if checked_out is None:
            # NullPool and friends keep no such count.
            return
        # `adjust` exists because the checkin event fires BEFORE the
        # connection is handed back to the pool, so checkedout() still
        # counts it there — verified against SQLAlchemy 2.0: checkout
        # reports 1, checkin also reports 1, and only after the handler
        # returns does it reach 0. Reading it unadjusted at checkin means
        # the gauge can never observe the pool going idle, no matter what
        # the throttle does.
        value = checked_out() + adjust
        if value < 0:
            value = 0
        pid = os.getpid()
        same_pid = pid == gauge_state.pid
        if same_pid and value == gauge_state.last:
            # The common case, and the cheapest: nothing changed. An idle
            # pool therefore costs nothing at all.
            return
        # Deliberately not time-throttled. A throttle was tried and
        # removed: this fires on checkout and checkin, so suppressing
        # falls leaves the gauge reporting a connection held through the
        # whole idle period after a short statement (measured on a live
        # deployment: every per-pid series read exactly 1, forever), while
        # suppressing rises hides the peaks a saturation gauge exists to
        # show. Neither direction is safe to drop, so the only sound rule
        # is to write whenever the value changes — which for a gauge that
        # changes twice per statement is what it should cost. Measured at
        # ~1us per write against ~25us of total per-statement
        # instrumentation.
        child = gauge_state.child
        if child is None or not same_pid:
            gauge_state.pid = pid
            child = db_metrics.SKY_APISERVER_DB_POOL_CHECKED_OUT.labels(
                db, pool_label, str(pid))
            gauge_state.child = child
        # Set rather than inc/dec: a gauge that drifts is worse than one
        # that is a scrape behind, and an invalidated connection does not
        # always pair its checkout with a checkin.
        child.set(value)
        gauge_state.last = value

    @event.listens_for(engine, 'checkout')
    def _checkout(dbapi_connection, conn_rec, conn_proxy):
        del dbapi_connection, conn_rec, conn_proxy
        try:
            _sync_pool_gauge()
            # engine.dispose() replaces the pool object, dropping the
            # acquire-timing wrapper with it. Re-arm here; the first
            # acquire on a fresh pool goes unmeasured, which beats losing
            # the metric for the rest of the process's life.
            _wrap_pool_connect(engine, db, pool_label)
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to record a pool checkout.')

    @event.listens_for(engine, 'checkin')
    def _checkin(dbapi_connection, conn_rec):
        del dbapi_connection, conn_rec
        try:
            # -1: this connection is still counted as checked out here.
            _sync_pool_gauge(-1)
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to record a pool checkin.')

    @event.listens_for(engine, 'close')
    def _close(dbapi_connection, conn_rec):
        del dbapi_connection
        try:
            born = conn_rec.info.pop(_KEY_BORN, None)
            if born is None:
                return
            _child(db_metrics.SKY_APISERVER_DB_CONNECTION_LIFETIME_SECONDS, db,
                   pool_label).observe(time.perf_counter() - born)
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to record a connection lifetime.')

    @event.listens_for(engine, 'invalidate')
    def _invalidate(dbapi_connection, conn_rec, exception):
        del dbapi_connection, conn_rec, exception
        try:
            _child(db_metrics.SKY_APISERVER_DB_INVALIDATIONS_TOTAL, db,
                   pool_label).inc()
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to record a connection invalidation.')

    _export_pool_config(engine, db, pool_label)
    _wrap_pool_connect(engine, db, pool_label)


def _wrap_do_commit(engine: Any, db: str) -> None:
    """Time the commit round trip itself.

    SQLAlchemy dispatches its ``commit`` event *before* the dialect
    commits, so the event alone cannot measure it. Under a
    transaction-mode pooler this is where the server connection is
    actually released, which makes it a pooler signal and not only a
    database one.
    """
    dialect = engine.dialect
    if getattr(dialect, '_sky_db_commit_wrapped', False):
        return
    original = dialect.do_commit

    def _timed_do_commit(dbapi_connection):
        start = time.perf_counter()
        try:
            return original(dbapi_connection)
        finally:
            try:
                _child(db_metrics.SKY_APISERVER_DB_COMMIT_SECONDS,
                       db).observe(time.perf_counter() - start)
            except Exception:  # pylint: disable=broad-except
                _warn_once('Failed to record a commit.')

    try:
        dialect.do_commit = _timed_do_commit
        dialect._sky_db_commit_wrapped = True  # pylint: disable=protected-access
    except (AttributeError, TypeError):
        _warn_once('Could not time commits on this dialect.')


def _wrap_pool_connect(engine: Any, db: str, pool_label: str) -> None:
    """Measure total time to obtain a connection from the pool.

    SQLAlchemy has no pre-checkout event, so the wait a caller actually
    experiences is not reachable from the event API. ``Pool.connect`` is
    public and is the single chokepoint for it.
    """
    pool = engine.pool
    if getattr(pool.connect, _WRAPPED_ATTR, False):
        return
    original = pool.connect

    def _timed_connect():
        start = time.perf_counter()
        try:
            return original()
        finally:
            try:
                _child(db_metrics.SKY_APISERVER_DB_ACQUIRE_SECONDS, db,
                       pool_label).observe(time.perf_counter() - start)
            except Exception:  # pylint: disable=broad-except
                _warn_once('Failed to record a pool acquire.')

    # Marked on the wrapper itself rather than on the pool, so re-arming
    # after engine.dispose() (which builds a fresh pool) is a plain
    # attribute check and nothing is left behind on SQLAlchemy's object.
    setattr(_timed_connect, _WRAPPED_ATTR, True)
    try:
        pool.connect = _timed_connect
    except (AttributeError, TypeError):
        _warn_once('Could not time pool acquisition on this pool.')


def _export_pool_config(engine: Any, db: str, pool_label: str) -> None:
    """Publish the pool's configured limits.

    So a dashboard can compute utilization without hardcoding a value
    that lives in helm values, and so a role that is supposed to pool but
    reports ``NullPool`` is visible.
    """
    pool = engine.pool
    # QueuePool exposes size() as a method; SingletonThreadPool exposes it
    # as a plain int; NullPool has neither.
    size = getattr(pool, 'size', None)
    if callable(size):
        size = size()
    if isinstance(size, int):
        db_metrics.SKY_APISERVER_DB_POOL_SIZE.labels(db, pool_label).set(size)
    max_overflow = getattr(pool, '_max_overflow', None)
    if isinstance(max_overflow, int):
        db_metrics.SKY_APISERVER_DB_POOL_MAX_OVERFLOW.labels(
            db, pool_label).set(max_overflow)
