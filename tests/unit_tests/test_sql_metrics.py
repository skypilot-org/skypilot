"""Tests for the client-side SQL instrumentation.

Every test builds its own table with a unique name so its series do not
collide with another test's — the prometheus registry is process-global
and its counters only ever go up.
"""
import itertools
import threading

import prometheus_client as prom
import pytest
from sqlalchemy import orm
import sqlalchemy as sa

from sky.metrics import db as db_metrics
from sky.utils.db import sql_metrics

_names = itertools.count()


@pytest.fixture(autouse=True)
def _enable_metrics(monkeypatch):
    monkeypatch.setattr(db_metrics, 'ENABLED', True)


def _sample(name, **labels):
    value = prom.REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


def _table(**extra_columns):
    """A fresh table with a name no other test uses."""
    name = f'tbl_{next(_names)}'
    md = sa.MetaData()
    columns = [
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('body', sa.Text),
    ]
    columns.extend(
        sa.Column(k, v) for k, v in extra_columns.items())  # pragma: no cover
    return name, md, sa.Table(name, md, *columns)


def _engine(md,
            db=sql_metrics.DB_STATE,
            poolclass=sa.pool.QueuePool,
            path=None):
    # A file when the test outlives a single connection: an in-memory
    # SQLite database belongs to its connection, so NullPool (a fresh
    # connection per operation) and dispose() would both lose the schema.
    url = f'sqlite:///{path}' if path is not None else 'sqlite://'
    engine = sa.create_engine(url, poolclass=poolclass)
    sql_metrics.install(engine, db)
    md.create_all(engine)
    return engine


def _seed(engine, table, rows, width=100):
    with engine.begin() as conn:
        conn.execute(table.insert(), [{
            'id': i,
            'body': 'x' * width
        } for i in range(rows)])


# --- label derivation ----------------------------------------------------


def test_labels_come_from_the_compiled_construct():
    name, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 3)

    with engine.connect() as conn:
        conn.execute(sa.select(table)).all()
        conn.execute(table.update().where(table.c.id == 1).values(body='y'))
        conn.execute(table.delete().where(table.c.id == 2))

    for op in ('select', 'insert', 'update', 'delete'):
        assert _sample('sky_apiserver_db_statements_total',
                       db='state',
                       table=name,
                       op=op,
                       outcome='ok') >= 1, op
    # The DDL that created the table is attributed to it too.
    assert _sample('sky_apiserver_db_statements_total',
                   db='state',
                   table=name,
                   op='ddl',
                   outcome='ok') == 1


def test_join_is_attributed_to_its_leftmost_table():
    left_name, md, left = _table()
    right_name = f'tbl_{next(_names)}'
    right = sa.Table(right_name, md,
                     sa.Column('id', sa.Integer, primary_key=True))
    engine = _engine(md)
    _seed(engine, left, 2)

    with engine.connect() as conn:
        conn.execute(
            sa.select(left.c.id).join_from(left, right,
                                           left.c.id == right.c.id)).all()

    assert _sample('sky_apiserver_db_execute_seconds_count',
                   db='state',
                   table=left_name,
                   op='select') == 1
    assert _sample('sky_apiserver_db_execute_seconds_count',
                   db='state',
                   table=right_name,
                   op='select') == 0


def test_subquery_does_not_leak_an_anonymous_alias_as_a_label():
    name, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 3)

    sub = sa.select(table).subquery()
    with engine.connect() as conn:
        conn.execute(sa.select(sub.c.id)).all()

    # Unwrapped to the real table, not labelled `anon_1`.
    assert _sample('sky_apiserver_db_execute_seconds_count',
                   db='state',
                   table=name,
                   op='select') == 1


def test_raw_text_is_not_regexed_into_a_table_label():
    _, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 1)

    before = _sample('sky_apiserver_db_execute_seconds_count',
                     db='state',
                     table=db_metrics.UNKNOWN_TABLE,
                     op='other')
    with engine.connect() as conn:
        conn.execute(sa.text(f'select count(*) from {table.name}')).all()
    after = _sample('sky_apiserver_db_execute_seconds_count',
                    db='state',
                    table=db_metrics.UNKNOWN_TABLE,
                    op='other')
    assert after == before + 1


def test_derivation_runs_once_per_compiled_statement(monkeypatch):
    """The acceptance criterion: label derivation is not per-execution."""
    _, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 2)

    calls = []
    original = sql_metrics._compute_labels

    def counting(compiled):
        calls.append(compiled)
        return original(compiled)

    monkeypatch.setattr(sql_metrics, '_compute_labels', counting)

    stmt = sa.select(table)
    for _ in range(25):
        with engine.connect() as conn:
            conn.execute(stmt).all()

    assert len(calls) == 1, (
        f'derived {len(calls)} times for 25 executions of one statement')


# --- payload ------------------------------------------------------------


def test_rows_and_result_bytes_are_exact():
    name, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 40, width=1000)

    with engine.connect() as conn:
        conn.execute(sa.select(table)).all()

    assert _sample('sky_apiserver_db_rows_returned_sum',
                   db='state',
                   table=name,
                   op='select') == 40
    # 40 rows x (1000-char body + an 8-byte id).
    assert _sample('sky_apiserver_db_result_bytes_sum',
                   db='state',
                   table=name,
                   op='select') == 40 * 1008


def test_result_bytes_include_a_single_outlier_row():
    """Regression: sampling either inflated or missed the outlier row.

    Extrapolating from the leading rows multiplied one big row across the
    whole result; striding past it reported the result as small. The
    outlier is the payload this metric exists to catch, so it is measured.
    """
    name, md, table = _table()
    engine = _engine(md)
    big = 4 * 1024 * 1024
    with engine.begin() as conn:
        conn.execute(table.insert(), [{
            'id': i,
            'body': 'x' * (big if i == 137 else 100)
        } for i in range(200)])

    with engine.connect() as conn:
        conn.execute(sa.select(table)).all()

    expected = big + 199 * 100 + 200 * 8
    assert _sample('sky_apiserver_db_result_bytes_sum',
                   db='state',
                   table=name,
                   op='select') == expected


def test_result_bytes_handle_a_null_first_value():
    """A NULL in the first row says nothing about the rest of the column."""
    name, md, table = _table()
    engine = _engine(md)
    with engine.begin() as conn:
        conn.execute(table.insert(), [{
            'id': 0,
            'body': None
        }, {
            'id': 1,
            'body': 'y' * 5000
        }])

    with engine.connect() as conn:
        conn.execute(sa.select(table)).all()

    # The NULL counts as a scalar (8); the real 5000-char value is not lost.
    assert _sample('sky_apiserver_db_result_bytes_sum',
                   db='state',
                   table=name,
                   op='select') == 5000 + 8 + 2 * 8


def test_statement_bytes_capture_a_large_write():
    name, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 1)

    payload = 'z' * (2 * 1024 * 1024)
    with engine.begin() as conn:
        conn.execute(table.update().where(table.c.id == 0).values(body=payload))

    sent = _sample('sky_apiserver_db_statement_bytes_sum',
                   db='state',
                   table=name,
                   op='update')
    assert sent >= len(payload)


def test_statement_bytes_are_not_recorded_for_reads():
    name, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 2)

    with engine.connect() as conn:
        conn.execute(sa.select(table)).all()

    assert _sample('sky_apiserver_db_statement_bytes_count',
                   db='state',
                   table=name,
                   op='select') == 0


@pytest.mark.parametrize('consume', [
    lambda r: r.all(),
    lambda r: r.fetchall(),
    lambda r: r.scalars().all(),
    lambda r: r.mappings().all(),
])
def test_rowfetch_covers_every_bulk_consumption_path(consume):
    name, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 30)

    with engine.connect() as conn:
        consume(conn.execute(sa.select(table)))

    assert _sample('sky_apiserver_db_rowfetch_seconds_count',
                   db='state',
                   table=name,
                   op='select') == 1


def test_rowfetch_is_not_reported_as_empty_for_row_at_a_time_reads():
    """`.first()` uses fetchone, which is deliberately not instrumented.

    Reporting zero seconds and zero bytes would read as a free empty
    result, which is worse than reporting nothing at all.
    """
    name, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 5)

    with engine.connect() as conn:
        assert conn.execute(sa.select(table)).first() is not None

    assert _sample('sky_apiserver_db_rowfetch_seconds_count',
                   db='state',
                   table=name,
                   op='select') == 0


def test_orm_session_is_covered():
    name, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 4)

    with orm.Session(engine) as session:
        session.execute(sa.select(table)).all()

    assert _sample('sky_apiserver_db_rowfetch_seconds_count',
                   db='state',
                   table=name,
                   op='select') == 1


# --- transactions -------------------------------------------------------


def test_transaction_span_separates_commit_from_rollback():
    _, md, table = _table()
    engine = _engine(md)
    commits = _sample('sky_apiserver_db_transaction_seconds_count',
                      db='state',
                      outcome='commit')
    rollbacks = _sample('sky_apiserver_db_transaction_seconds_count',
                        db='state',
                        outcome='rollback')

    with engine.begin() as conn:
        conn.execute(table.insert(), {'id': 1, 'body': 'a'})
    with engine.connect() as conn:
        conn.execute(sa.select(table)).all()

    assert _sample('sky_apiserver_db_transaction_seconds_count',
                   db='state',
                   outcome='commit') == commits + 1
    # A read-only block returns its connection without committing.
    assert _sample('sky_apiserver_db_transaction_seconds_count',
                   db='state',
                   outcome='rollback') == rollbacks + 1


def test_commit_is_timed_separately_from_the_span():
    _, md, table = _table()
    engine = _engine(md)
    before = _sample('sky_apiserver_db_commit_seconds_count', db='state')

    with engine.begin() as conn:
        conn.execute(table.insert(), {'id': 1, 'body': 'a'})

    assert _sample('sky_apiserver_db_commit_seconds_count',
                   db='state') == before + 1


def test_failed_statements_are_counted_as_errors():
    _, md, _ = _table()
    engine = _engine(md)
    before = _sample('sky_apiserver_db_statements_total',
                     db='state',
                     table=db_metrics.UNKNOWN_TABLE,
                     op='other',
                     outcome='error')

    with pytest.raises(sa.exc.OperationalError):
        with engine.connect() as conn:
            conn.execute(sa.text('select * from does_not_exist'))

    assert _sample('sky_apiserver_db_statements_total',
                   db='state',
                   table=db_metrics.UNKNOWN_TABLE,
                   op='other',
                   outcome='error') == before + 1


# --- connection and pool ------------------------------------------------


def test_connect_and_acquire_are_recorded_with_the_pool_class():
    _, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 1)

    assert _sample(
        'sky_apiserver_db_connects_total', db='state', pool='QueuePool') >= 1
    assert _sample('sky_apiserver_db_connect_seconds_count',
                   db='state',
                   pool='QueuePool') >= 1
    assert _sample('sky_apiserver_db_acquire_seconds_count',
                   db='state',
                   pool='QueuePool') >= 1


def test_pool_config_is_exported():
    _, md, _ = _table()
    _engine(md)

    assert _sample('sky_apiserver_db_pool_size', db='state',
                   pool='QueuePool') > 0
    assert prom.REGISTRY.get_sample_value('sky_apiserver_db_pool_max_overflow',
                                          {
                                              'db': 'state',
                                              'pool': 'QueuePool'
                                          }) is not None


def test_pool_class_is_visible_so_an_unpooled_engine_stands_out(tmp_path):
    """The signal for an engine that should pool but does not."""
    _, md, table = _table()
    engine = _engine(md,
                     db='state_nopool',
                     poolclass=sa.pool.NullPool,
                     path=tmp_path / 'nopool.db')
    _seed(engine, table, 1)

    assert _sample('sky_apiserver_db_connects_total',
                   db='state_nopool',
                   pool='NullPool') >= 1
    # NullPool keeps no checkout count, so no saturation gauge for it.
    assert prom.REGISTRY.get_sample_value('sky_apiserver_db_pool_size', {
        'db': 'state_nopool',
        'pool': 'NullPool'
    }) is None


def test_connection_lifetime_is_recorded_on_close():
    _, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 1)
    before = _sample('sky_apiserver_db_connection_lifetime_seconds_count',
                     db='state',
                     pool='QueuePool')

    engine.dispose()

    assert _sample('sky_apiserver_db_connection_lifetime_seconds_count',
                   db='state',
                   pool='QueuePool') == before + 1


def test_acquire_timing_survives_engine_dispose(tmp_path):
    """dispose() replaces the pool object, taking the wrapper with it."""
    _, md, table = _table()
    engine = _engine(md, path=tmp_path / 'dispose.db')
    _seed(engine, table, 1)
    engine.dispose()

    before = _sample('sky_apiserver_db_acquire_seconds_count',
                     db='state',
                     pool='QueuePool')
    # First acquire on the fresh pool re-arms the wrapper; the second is
    # measured again.
    for _ in range(3):
        with engine.connect() as conn:
            conn.execute(sa.select(table)).all()

    assert _sample('sky_apiserver_db_acquire_seconds_count',
                   db='state',
                   pool='QueuePool') > before


# --- installation contract ----------------------------------------------


def test_install_is_idempotent():
    name, md, table = _table()
    engine = sa.create_engine('sqlite://', poolclass=sa.pool.QueuePool)
    for _ in range(4):
        sql_metrics.install(engine, sql_metrics.DB_STATE)
    md.create_all(engine)
    _seed(engine, table, 2)

    with engine.connect() as conn:
        conn.execute(sa.select(table)).all()

    assert _sample('sky_apiserver_db_execute_seconds_count',
                   db='state',
                   table=name,
                   op='select') == 1


def test_disabled_attaches_nothing(monkeypatch):
    monkeypatch.setattr(db_metrics, 'ENABLED', False)
    name, md, table = _table()
    engine = sa.create_engine('sqlite://')
    sql_metrics.install(engine, sql_metrics.DB_STATE)
    md.create_all(engine)
    _seed(engine, table, 2)
    with engine.connect() as conn:
        conn.execute(sa.select(table)).all()

    assert _sample('sky_apiserver_db_execute_seconds_count',
                   db='state',
                   table=name,
                   op='select') == 0
    # Not merely "skip the observe": no listener is attached at all, so
    # SQLAlchemy never takes its has-events code path.
    assert not engine._has_events  # pylint: disable=protected-access


def test_async_engine_is_instrumented_through_its_sync_engine():
    async_engine = sa.ext.asyncio.create_async_engine('sqlite+aiosqlite://')
    sql_metrics.install(async_engine, sql_metrics.DB_STATE_ASYNC)
    assert async_engine.sync_engine._has_events  # pylint: disable=protected-access


def test_a_broken_listener_does_not_break_queries(monkeypatch):
    """A defect in the instrument must never fail a database call."""
    name, md, table = _table()
    engine = _engine(md)
    _seed(engine, table, 3)

    def boom(_):
        raise RuntimeError('instrument is broken')

    monkeypatch.setattr(sql_metrics, '_compute_labels', boom)
    monkeypatch.setattr(sql_metrics, '_warned', False)

    with engine.connect() as conn:
        rows = conn.execute(sa.select(table)).all()
    assert len(rows) == 3
    del name


def test_concurrent_install_attaches_once():
    name, md, table = _table()
    engine = sa.create_engine('sqlite://', poolclass=sa.pool.QueuePool)
    threads = [
        threading.Thread(target=sql_metrics.install,
                         args=(engine, sql_metrics.DB_STATE)) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    md.create_all(engine)
    _seed(engine, table, 1)

    with engine.connect() as conn:
        conn.execute(sa.select(table)).all()
    assert _sample('sky_apiserver_db_execute_seconds_count',
                   db='state',
                   table=name,
                   op='select') == 1


# --- the external entry point used by non-SQLAlchemy callers -------------


def test_record_statement_lands_on_the_same_families():
    before = _sample('sky_apiserver_db_statements_total',
                     db='ha_asyncpg',
                     table='requests',
                     op='insert',
                     outcome='ok')
    db_metrics.record_statement('ha_asyncpg',
                                'requests',
                                'insert',
                                0.01,
                                rows=1,
                                statement_bytes=4096)

    assert _sample('sky_apiserver_db_statements_total',
                   db='ha_asyncpg',
                   table='requests',
                   op='insert',
                   outcome='ok') == before + 1
    assert _sample('sky_apiserver_db_statement_bytes_sum',
                   db='ha_asyncpg',
                   table='requests',
                   op='insert') >= 4096


def test_observe_statement_marks_failures():
    before = _sample('sky_apiserver_db_statements_total',
                     db='ha_asyncpg',
                     table='requests',
                     op='select',
                     outcome='error')
    with pytest.raises(ValueError):
        with db_metrics.observe_statement('ha_asyncpg', 'requests', 'select'):
            raise ValueError('boom')

    assert _sample('sky_apiserver_db_statements_total',
                   db='ha_asyncpg',
                   table='requests',
                   op='select',
                   outcome='error') == before + 1


def test_observe_statement_is_inert_when_disabled(monkeypatch):
    monkeypatch.setattr(db_metrics, 'ENABLED', False)
    before = _sample('sky_apiserver_db_statements_total',
                     db='ha_asyncpg',
                     table='clusters',
                     op='select',
                     outcome='ok')
    with db_metrics.observe_statement('ha_asyncpg', 'clusters',
                                      'select') as obs:
        obs.rows = 5
    assert _sample('sky_apiserver_db_statements_total',
                   db='ha_asyncpg',
                   table='clusters',
                   op='select',
                   outcome='ok') == before
