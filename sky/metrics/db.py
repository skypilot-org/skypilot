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
* ``op``   — select / insert / update / delete / ddl / other.
* ``pool`` — the pool class (``QueuePool`` / ``NullPool`` / ...), on the
  connection-side families only. A role that is expected to pool but
  reports ``NullPool`` is a defect, and nothing else surfaces it.

Statement text, statement hashes, request ids, users and cluster/job
names are deliberately *not* labels.
"""

import contextlib
import os
import time
from typing import Iterator, Optional

import prometheus_client as prom

from sky.skylet import constants

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

# Statement latency ladder. Starts at 1ms: with a pooler and a remote
# Postgres in the path, a point query costs hundreds of microseconds at
# best, and sub-ms resolution would only buy precision for SQLite. Tops
# out at 60s because anything slower is already an incident, and every
# bucket multiplies the series count of every labeled histogram.
LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0,
                   2.5, 5.0, 10.0, 30.0, 60.0, float('inf'))

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
    ['db', 'table', 'op'],
    buckets=LATENCY_BUCKETS,
)

# Time spent handing already-buffered rows to the caller: SQLAlchemy row
# construction, not wire time (see above). This is the "we dragged a
# large result set into Python objects" cost, which lands on whichever
# thread or event loop made the call.
SKY_APISERVER_DB_ROWFETCH_SECONDS = prom.Histogram(
    'sky_apiserver_db_rowfetch_seconds',
    'Duration of row fetch / materialization after execute, client-side',
    ['db', 'table', 'op'],
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

SKY_APISERVER_DB_STATEMENTS_TOTAL = prom.Counter(
    'sky_apiserver_db_statements_total',
    'Statements executed, by outcome',
    ['db', 'table', 'op', 'outcome'],
)

# The direct instrument for full-table reads. Exact where the driver
# reports it (psycopg2 sets rowcount for SELECT); otherwise counted from
# the rows actually handed to the caller.
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
    ['db', 'table', 'op'],
    buckets=PAYLOAD_BUCKETS,
)

# Statement text plus bound parameters, on the way out. Exact, and the
# instrument for the multi-hundred-megabyte write.
SKY_APISERVER_DB_STATEMENT_BYTES = prom.Histogram(
    'sky_apiserver_db_statement_bytes',
    'Bytes of statement text plus bound parameters sent for one statement',
    ['db', 'table', 'op'],
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

    A no-op when metrics are disabled, so call sites need no guard.
    """
    if not ENABLED:
        return
    SKY_APISERVER_DB_EXECUTE_SECONDS.labels(db, table,
                                            op).observe(execute_seconds)
    SKY_APISERVER_DB_STATEMENTS_TOTAL.labels(db, table, op, outcome).inc()
    if rows is not None and rows >= 0:
        SKY_APISERVER_DB_ROWS_RETURNED.labels(db, table, op).observe(rows)
    if result_bytes is not None:
        SKY_APISERVER_DB_RESULT_BYTES.labels(db, table,
                                             op).observe(result_bytes)
    if statement_bytes is not None:
        SKY_APISERVER_DB_STATEMENT_BYTES.labels(db, table,
                                                op).observe(statement_bytes)


def record_acquire(db: str, pool: str, seconds: float) -> None:
    """Record the time taken to obtain a connection from a pool.

    For pools SQLAlchemy does not manage, such as a raw ``asyncpg.Pool``
    used directly on an event loop. Those emit no ``PoolEvents``, so the
    engine instrumentation cannot see them, and a pool running dry is
    exactly what this number is for.
    """
    if not ENABLED:
        return
    SKY_APISERVER_DB_ACQUIRE_SECONDS.labels(db, pool).observe(seconds)


def record_pool(db: str,
                pool: str,
                checked_out: Optional[int] = None,
                size: Optional[int] = None,
                max_overflow: Optional[int] = None) -> None:
    """Publish a non-SQLAlchemy pool's saturation and configured limits."""
    if not ENABLED:
        return
    if checked_out is not None:
        SKY_APISERVER_DB_POOL_CHECKED_OUT.labels(db, pool, str(
            os.getpid())).set(checked_out)
    if size is not None:
        SKY_APISERVER_DB_POOL_SIZE.labels(db, pool).set(size)
    if max_overflow is not None:
        SKY_APISERVER_DB_POOL_MAX_OVERFLOW.labels(db, pool).set(max_overflow)


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
        record_statement(db,
                         table,
                         op,
                         time.perf_counter() - start,
                         outcome=obs.outcome,
                         rows=obs.rows,
                         result_bytes=obs.result_bytes,
                         statement_bytes=obs.statement_bytes)
