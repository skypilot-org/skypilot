"""Prometheus metric families for the API server's own view of the database.

Everything here describes one chain, measured as the *client* experiences
it:

    acquire -> connect -> execute -> rowfetch -> commit

Storage-side instrumentation cannot answer which of those a slow request
was waiting on, and it cannot see payload pathologies at all: a query
that returns a hundred megabytes looks like a healthy fast query to every
server-side counter. These families are the app-side counterpart.

Deliberately a leaf module: it imports ``prometheus_client`` and
``sky.skylet.constants`` and nothing else. The rest of the server metrics
live in ``sky.metrics.utils``, which imports ``sky.skypilot_config``,
which imports ``sky.utils.db.db_utils`` — so a metrics module that the DB
layer itself can import must not go through it. That is also why
``ENABLED`` re-reads the env var here instead of importing
``sky.metrics.utils.METRICS_ENABLED``.

The wiring that feeds these families lives in
``sky.utils.db.sql_metrics``; ``record_statement`` /
``observe_statement`` are the entry points for code that talks to the
database without going through SQLAlchemy (a raw ``asyncpg`` pool, say),
so it lands on the same families with the same labels.

Label budget, deliberately small:

* ``db``   — the engine *role*, not the hostname and not the logical
  database. On Postgres every SkyPilot component shares one physical
  database and one engine per role, so the roles (pooled, direct,
  unpooled, async, ...) are what actually have different expected
  profiles. Per-component resolution comes from ``table``.
* ``table`` — the primary table of the statement, taken from the
  compiled SQLAlchemy construct.
* ``op``   — select / insert / update / delete / ddl / other. Carried
  only by ``statements_total`` (which is where "statement rate by op"
  lives) and by ``rows_returned`` (where returned-vs-affected rows are
  genuinely different populations). The latency and byte families are
  labelled by ``db`` + ``table`` alone: a per-op latency breakdown costs
  a third of this module's total series and the questions it answers are
  reachable from the counter.
* ``pool`` — the pool class (``QueuePool`` / ``NullPool`` / ...), on the
  connection-side families only. A role that is expected to pool but
  reports ``NullPool`` is a defect, and nothing else surfaces it.

Statement text, statement hashes, request ids, users and cluster/job
names are deliberately *not* labels.
"""

import contextlib
import logging
import os
import time
from typing import Iterator, Optional

import prometheus_client as prom

from sky.skylet import constants

logger = logging.getLogger(__name__)

# Whether to instrument the database layer at all. Cannot change at
# runtime. When false, ``sky.utils.db.sql_metrics.install`` attaches no
# event listeners, so the cost is not merely "skip the observe" but the
# whole SQLAlchemy has-events code path staying off.
ENABLED = os.environ.get(constants.ENV_VAR_SERVER_METRICS_ENABLED,
                         'false').lower() == 'true'

# Label value used when a statement's primary table cannot be determined
# from a compiled construct (``sqlalchemy.text()`` and driver-level
# statements). We do not regex the SQL text to fill this in.
UNKNOWN_TABLE = 'raw'

# The ``op`` label values. Public because callers outside SQLAlchemy pass
# them to ``record_statement`` / ``observe_statement``, and a label
# vocabulary spelled out at each call site drifts.
OP_SELECT = 'select'
OP_INSERT = 'insert'
OP_UPDATE = 'update'
OP_DELETE = 'delete'
OP_DDL = 'ddl'
OP_OTHER = 'other'

_KIB = 2**10
_MIB = 2**20
_GIB = 2**30

# Statement latency ladder, two buckets per decade (1x and 5x). Starts at
# 1ms: with a pooler and a remote Postgres in the path a point query costs
# hundreds of microseconds at best, so sub-ms resolution would only buy
# precision for SQLite. Tops out at 60s because anything slower is already
# an incident.
#
# Eleven buckets, not the sixteen this started with. Every bucket is a
# time series per label combination, and on a real deployment the latency
# families were 55% of everything this module exports; the five buckets
# dropped (2.5ms, 25ms, 250ms, 2.5s, 30s) each sat next to a neighbour
# within ~2.5x, which buys percentile smoothness rather than a different
# answer.
LATENCY_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 60.0,
                   float('inf'))

# Rows returned per statement. Decade buckets: the question this answers
# is "did something just read the whole table", which is an
# order-of-magnitude question.
ROW_BUCKETS = (1, 10, 100, 1_000, 10_000, 100_000, 1_000_000, float('inf'))

# Bytes moved by one statement, in either direction. Deliberately reaches
# 1GiB: the write payloads that motivated this instrument are that large,
# and a histogram whose top finite bucket is 128MiB reports "big" for
# both a 200MiB read and a 1GiB write.
PAYLOAD_BUCKETS = (_KIB, 16 * _KIB, 128 * _KIB, _MIB, 8 * _MIB, 32 * _MIB,
                   128 * _MIB, 512 * _MIB, _GIB, float('inf'))

# --- statement side -------------------------------------------------------

# The server round trip for the statement itself: the span of
# ``cursor.execute()``. Note this INCLUDES pulling the result set over the
# wire — both psycopg2 and asyncpg buffer the whole result client-side
# during execute — so a large read shows up here as latency, and
# ``sky_apiserver_db_rows_returned`` / ``_result_bytes`` are what
# distinguish it from a genuinely slow query.
SKY_APISERVER_DB_EXECUTE_SECONDS = prom.Histogram(
    'sky_apiserver_db_execute_seconds',
    'Duration of cursor.execute() for a statement, client-side',
    ['db', 'table'],
    buckets=LATENCY_BUCKETS,
)

# Time spent handing already-buffered rows to the caller: SQLAlchemy row
# construction, not wire time (see above). This is the "we dragged a
# large result set into Python objects" cost, which lands on whichever
# thread or event loop made the call.
SKY_APISERVER_DB_ROWFETCH_SECONDS = prom.Histogram(
    'sky_apiserver_db_rowfetch_seconds',
    'Duration of row fetch / materialization after execute, client-side',
    ['db', 'table'],
    buckets=LATENCY_BUCKETS,
)

# begin -> commit/rollback. Long-held transactions are the
# `idle in transaction` turf and they hold back vacuum. Not labeled by
# table: a transaction spans whatever tables it likes. Labeled by outcome
# because a read-only block ends in rollback, and mixing those into one
# series buries the write transactions.
SKY_APISERVER_DB_TRANSACTION_SECONDS = prom.Histogram(
    'sky_apiserver_db_transaction_seconds',
    'Duration of a database transaction, begin to commit/rollback',
    ['db', 'outcome'],
    buckets=LATENCY_BUCKETS,
)

# Broken out from the transaction span because under a transaction-mode
# pooler, commit is where the server connection is actually released —
# its latency is a pooler signal, not only a database one.
SKY_APISERVER_DB_COMMIT_SECONDS = prom.Histogram(
    'sky_apiserver_db_commit_seconds',
    'Duration of a commit, client-side',
    ['db'],
    buckets=LATENCY_BUCKETS,
)

# Counts *round trips*, not logical statements, and that is deliberate:
# it is the unit the database and the pooler actually see, and it is the
# same unit `execute_seconds` has to use to mean anything. The two differ
# where SQLAlchemy's insertmanyvalues splits one `insert().returning()`
# into several cursor executes — measured: 3000 rows becomes 3 executes at
# the default page size of 1000. So this rate can exceed the number of
# `execute()` calls the application made. Compare it against database-side
# counters, not against application operations.
SKY_APISERVER_DB_STATEMENTS_TOTAL = prom.Counter(
    'sky_apiserver_db_statements_total',
    'Round trips executed, by outcome (see the note above on batching)',
    ['db', 'table', 'op', 'outcome'],
)

# The direct instrument for full-table reads. Exact where the driver
# reports it (psycopg2 sets rowcount for SELECT); otherwise counted from
# the rows actually handed to the caller.
#
# This is the one payload family that keeps `op`, and it has to: for a
# SELECT the number means rows *returned*, for DML it means rows
# *affected*. Those are different populations, and merging them would
# make "something read the whole table" indistinguishable from "something
# deleted the whole table".
SKY_APISERVER_DB_ROWS_RETURNED = prom.Histogram(
    'sky_apiserver_db_rows_returned',
    'Rows returned or affected by one statement',
    ['db', 'table', 'op'],
    buckets=ROW_BUCKETS,
)

# No driver reports result size, so this is summed client-side over the
# rows handed back: exact for anything up to a few million cells, sampled
# above that. Two caveats. Character counts stand in for encoded bytes,
# so non-ASCII text under-counts. And rows consumed one at a time
# (``.first()``, ``.scalar()``, plain iteration) are not counted at all —
# see the fetch wrapper in ``sky.utils.db.sql_metrics``.
SKY_APISERVER_DB_RESULT_BYTES = prom.Histogram(
    'sky_apiserver_db_result_bytes',
    'Bytes of result data returned by one statement, measured client-side',
    ['db', 'table'],
    buckets=PAYLOAD_BUCKETS,
)

# Statement text plus bound parameters, on the way out. Exact, and the
# instrument for the multi-hundred-megabyte write.
SKY_APISERVER_DB_STATEMENT_BYTES = prom.Histogram(
    'sky_apiserver_db_statement_bytes',
    'Bytes of statement text plus bound parameters sent for one statement',
    ['db', 'table'],
    buckets=PAYLOAD_BUCKETS,
)

# --- connection side ------------------------------------------------------

# Fresh DBAPI connections opened. A counter, not a census: a census of
# live backends is structurally blind to sub-second sessions and to
# everything still in the pre-fork handshake, which is exactly the
# population that saturates connection admission.
SKY_APISERVER_DB_CONNECTS_TOTAL = prom.Counter(
    'sky_apiserver_db_connects_total',
    'Fresh DBAPI connections opened',
    ['db', 'pool'],
)

# How long opening one fresh connection took: the connection admission
# wait as this process experiences it.
#
# Read it knowing what is at the other end. Direct to the database, this
# IS the database's admission latency. With a connection pooler in the
# path, it is the connect to the *pooler*, which is cheap and says nothing
# about how long the pooler then waited for a server connection upstream —
# that half is only visible from the pooler's own metrics. The `db` label
# separates the two cases, since the direct-role engines bypass the
# pooler by construction.
SKY_APISERVER_DB_CONNECT_SECONDS = prom.Histogram(
    'sky_apiserver_db_connect_seconds',
    'Duration of opening a fresh DBAPI connection',
    ['db', 'pool'],
    buckets=LATENCY_BUCKETS,
)

# Total time to obtain a usable connection: pool wait plus, if the pool
# had none to give, the fresh connect. Read against
# ``sky_apiserver_db_connect_seconds``: both high means the database is
# slow to admit, acquire high while connect is low means we ran out of
# pool.
SKY_APISERVER_DB_ACQUIRE_SECONDS = prom.Histogram(
    'sky_apiserver_db_acquire_seconds',
    'Time to obtain a connection from the pool, including any fresh connect',
    ['db', 'pool'],
    buckets=LATENCY_BUCKETS,
)

SKY_APISERVER_DB_CONNECTION_LIFETIME_SECONDS = prom.Histogram(
    'sky_apiserver_db_connection_lifetime_seconds',
    'Lifetime of a DBAPI connection, open to close',
    ['db', 'pool'],
    buckets=LATENCY_BUCKETS,
)

SKY_APISERVER_DB_INVALIDATIONS_TOTAL = prom.Counter(
    'sky_apiserver_db_invalidations_total',
    'Connections invalidated and discarded by the pool',
    ['db', 'pool'],
)

# Pool saturation is a per-process condition: a fleet-wide sum can exceed
# any single pool's limit while no individual pool is full, so per-pid
# series are kept. multiprocess_mode must be 'liveall' — the aggregating
# modes strip any label named 'pid', including this user-defined one, and
# merge every process into one series.
SKY_APISERVER_DB_POOL_CHECKED_OUT = prom.Gauge(
    'sky_apiserver_db_pool_checked_out',
    'Connections currently checked out of the pool, per process',
    ['db', 'pool', 'pid'],
    multiprocess_mode='liveall',
)

# The configured limits, exported so dashboards and alerts can compute
# utilization without hardcoding helm values. Every process reports the
# same number, hence 'max' and no pid label.
SKY_APISERVER_DB_POOL_SIZE = prom.Gauge(
    'sky_apiserver_db_pool_size',
    'Configured pool size',
    ['db', 'pool'],
    multiprocess_mode='max',
)

SKY_APISERVER_DB_POOL_MAX_OVERFLOW = prom.Gauge(
    'sky_apiserver_db_pool_max_overflow',
    'Configured pool max overflow',
    ['db', 'pool'],
    multiprocess_mode='max',
)

# One warning per process, not one per statement: a broken instrument
# would otherwise out-log the application.
_warned = False


def _warn_once(message: str) -> None:
    global _warned
    if _warned:
        return
    _warned = True
    logger.warning('%s Database metrics may be incomplete.',
                   message,
                   exc_info=True)


def record_statement(
    db: str,
    table: str,
    op: str,
    execute_seconds: float,
    *,
    outcome: str = 'ok',
    rows: Optional[int] = None,
    result_bytes: Optional[int] = None,
    statement_bytes: Optional[int] = None,
) -> None:
    """Record one statement on the families above.

    For callers that do not go through SQLAlchemy and therefore get no
    event hooks — a raw ``asyncpg`` pool, for instance. SQLAlchemy
    engines are fed by ``sky.utils.db.sql_metrics.install`` instead.

    A no-op when metrics are disabled, and it never raises: call sites
    need no guard of their own. That matters more here than it looks —
    these are called from inside `finally` blocks and from paths holding a
    checked-out connection, so an exception escaping would strand real
    resources over a metrics defect.
    """
    if not ENABLED:
        return
    try:
        SKY_APISERVER_DB_EXECUTE_SECONDS.labels(db,
                                                table).observe(execute_seconds)
        SKY_APISERVER_DB_STATEMENTS_TOTAL.labels(db, table, op, outcome).inc()
        if rows is not None and rows >= 0:
            SKY_APISERVER_DB_ROWS_RETURNED.labels(db, table, op).observe(rows)
        if result_bytes is not None:
            SKY_APISERVER_DB_RESULT_BYTES.labels(db,
                                                 table).observe(result_bytes)
        if statement_bytes is not None:
            SKY_APISERVER_DB_STATEMENT_BYTES.labels(
                db, table).observe(statement_bytes)
    except Exception:  # pylint: disable=broad-except
        _warn_once('Failed to record a database statement.')


def record_acquire(db: str, pool: str, seconds: float) -> None:
    """Record the time taken to obtain a connection from a pool.

    For pools SQLAlchemy does not manage, such as a raw ``asyncpg.Pool``
    used directly on an event loop. Those emit no ``PoolEvents``, so the
    engine instrumentation cannot see them, and a pool running dry is
    exactly what this number is for. Never raises; see
    :func:`record_statement`.
    """
    if not ENABLED:
        return
    try:
        SKY_APISERVER_DB_ACQUIRE_SECONDS.labels(db, pool).observe(seconds)
    except Exception:  # pylint: disable=broad-except
        _warn_once('Failed to record a pool acquire.')


def record_pool(db: str,
                pool: str,
                checked_out: Optional[int] = None,
                size: Optional[int] = None,
                max_overflow: Optional[int] = None) -> None:
    """Publish a non-SQLAlchemy pool's saturation and configured limits.

    Every value is optional and an omitted one is **left untouched**, not
    reset — so a caller can publish the configured limits once at pool
    creation and then report only ``checked_out`` on each acquire without
    clobbering the saturation denominator.

    Never raises; see :func:`record_statement`.
    """
    if not ENABLED:
        return
    try:
        if checked_out is not None:
            SKY_APISERVER_DB_POOL_CHECKED_OUT.labels(db, pool, str(
                os.getpid())).set(checked_out)
        if size is not None:
            SKY_APISERVER_DB_POOL_SIZE.labels(db, pool).set(size)
        if max_overflow is not None:
            SKY_APISERVER_DB_POOL_MAX_OVERFLOW.labels(db,
                                                      pool).set(max_overflow)
    except Exception:  # pylint: disable=broad-except
        _warn_once('Failed to record pool state.')


class StatementObservation:
    """Mutable result carrier for :func:`observe_statement`.

    Set ``rows`` / ``result_bytes`` / ``statement_bytes`` inside the
    block; whatever is set when the block exits gets recorded. ``outcome``
    is set to ``error`` automatically if the block raises.
    """

    __slots__ = ('rows', 'result_bytes', 'statement_bytes', 'outcome')

    def __init__(self) -> None:
        self.rows: Optional[int] = None
        self.result_bytes: Optional[int] = None
        self.statement_bytes: Optional[int] = None
        self.outcome: str = 'ok'


@contextlib.contextmanager
def observe_statement(db: str, table: str,
                      op: str) -> Iterator[StatementObservation]:
    """Time a statement and record it, including on failure."""
    obs = StatementObservation()
    if not ENABLED:
        yield obs
        return
    start = time.perf_counter()
    try:
        yield obs
    except BaseException:
        obs.outcome = 'error'
        raise
    finally:
        # Guarded a second time, on top of record_statement's own guard.
        # An exception raised from a `finally` replaces whatever the block
        # was doing — including a successful return — so a caller that
        # acquired a connection inside the block would lose it. Cheap
        # insurance for the one place where the cost of raising is not
        # just a missing sample.
        try:
            record_statement(db,
                             table,
                             op,
                             time.perf_counter() - start,
                             outcome=obs.outcome,
                             rows=obs.rows,
                             result_bytes=obs.result_bytes,
                             statement_bytes=obs.statement_bytes)
        except Exception:  # pylint: disable=broad-except
            _warn_once('Failed to record an observed statement.')
