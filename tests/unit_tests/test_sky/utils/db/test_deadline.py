"""Unit tests for the auth-path DB deadline machinery.

These cover the parts that need no Postgres server:

* the SET LOCAL listener: attached only to Postgres psycopg2 (sync) engines,
  emits the prefix on the first statement of a bounded transaction and
  nothing else -- keyed on the driver's own transaction state, so exactly
  once per transaction whatever other engine listeners run first -- resets
  per transaction, skips executemany / named cursors / autocommit.

The listener tests run real SQLAlchemy machinery on a sqlite connection class
that presents psycopg2's ``status`` / ``autocommit`` surface.
"""
# pylint: disable=protected-access,missing-class-docstring,redefined-outer-name
import re
import sqlite3
import time
from unittest import mock

import psycopg2
import pytest
import sqlalchemy
from sqlalchemy import orm

from sky.utils.db import db_utils
from sky.utils.db import deadline


@pytest.fixture(autouse=True)
def _clean_deadline():
    """Never leak a thread-local deadline between tests."""
    deadline.clear_deadline()
    yield
    deadline.clear_deadline()


_PREFIX_RE = re.compile(r'^(SET LOCAL [^;]+; )+')


class _PsycopgLikeCursor(sqlite3.Cursor):
    """Flips the connection to BEGIN on the first statement, like psycopg2."""

    def execute(self, *args, **kwargs):
        result = super().execute(*args, **kwargs)
        self.connection.status = psycopg2.extensions.STATUS_BEGIN
        return result

    def executemany(self, *args, **kwargs):
        result = super().executemany(*args, **kwargs)
        self.connection.status = psycopg2.extensions.STATUS_BEGIN
        return result


class _PsycopgLikeConnection(sqlite3.Connection):
    """A sqlite3 connection presenting psycopg2's transaction-state surface.

    The listener reads two driver attributes: ``status`` (READY before the
    first statement of a transaction, BEGIN until commit/rollback) and
    ``autocommit`` (a bool on psycopg2; Python 3.12+ sqlite3 has its own
    int-valued ``autocommit`` -- LEGACY_TRANSACTION_CONTROL is -1 -- shadowed
    here). Everything else -- events, ``retval``, autobegin, pooling -- is
    real SQLAlchemy machinery on a real DBAPI connection.
    """
    autocommit = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.status = psycopg2.extensions.STATUS_READY

    def cursor(self, factory=None):
        return super().cursor(factory or _PsycopgLikeCursor)

    def commit(self):
        super().commit()
        self.status = psycopg2.extensions.STATUS_READY

    def rollback(self):
        super().rollback()
        self.status = psycopg2.extensions.STATUS_READY


class TestSetLocalListener:
    """The engine listener: attach conditions, emitted text, reset."""

    def _spy_engine(self,
                    url='sqlite://',
                    pretend_postgres=False,
                    before_install=None):
        """A sqlite engine, optionally presenting as Postgres + psycopg2.

        With ``pretend_postgres`` the listener attaches (it keys on the
        dialect name and driver) and the DBAPI connections are
        ``_PsycopgLikeConnection``; a second listener registered after it
        records what the driver would receive and strips the prefix again so
        sqlite can run the statement. ``before_install(engine)`` registers
        other listeners ahead of ours (listener-order tests).
        """
        if pretend_postgres:
            engine = sqlalchemy.create_engine(
                url,
                creator=lambda: sqlite3.connect(':memory:',
                                                factory=_PsycopgLikeConnection,
                                                check_same_thread=False))
            engine.dialect.name = 'postgresql'
            engine.dialect.driver = 'psycopg2'
        else:
            engine = sqlalchemy.create_engine(url)
        if before_install is not None:
            before_install(engine)
        deadline.install(engine)
        emitted = []

        @sqlalchemy.event.listens_for(engine,
                                      'before_cursor_execute',
                                      retval=True)
        def _capture(conn, cursor, statement, parameters, context, executemany):  # pylint: disable=unused-variable
            del conn, cursor, context, executemany
            emitted.append(statement)
            return _PREFIX_RE.sub('', statement), parameters

        return engine, emitted

    @staticmethod
    def _prefixes(emitted):
        return [s for s in emitted if s.startswith('SET LOCAL ')]

    def test_sqlite_engine_gets_no_listener_and_no_set_local(self):
        engine, emitted = self._spy_engine('sqlite://')
        deadline.set_deadline(time.monotonic() + 5)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('SELECT 1'))
        assert not self._prefixes(emitted), emitted
        assert not deadline.is_bounding(engine)

    def test_other_postgres_driver_gets_no_listener(self):
        # The listener reads psycopg2's connection status; on another driver
        # it would silently never fire, so it is not attached at all.
        engine = sqlalchemy.create_engine('sqlite://')
        engine.dialect.name = 'postgresql'
        engine.dialect.driver = 'psycopg'
        deadline.install(engine)
        deadline.set_deadline(time.monotonic() + 5)
        assert not deadline.is_bounding(engine)

    def test_get_engine_attaches_the_listener_to_every_sync_engine(
            self, monkeypatch):
        """The wiring in `db_utils.get_engine`: every sync Postgres engine it
        builds (pooled, direct, no_pool) carries the listener; the asyncpg
        engine does not. No database is needed: `create_engine` is lazy."""
        monkeypatch.setenv('IS_SKYPILOT_SERVER', '1')
        monkeypatch.setenv('SKYPILOT_DB_CONNECTION_URI',
                           'postgresql://u:p@127.0.0.1:1/dbx')
        monkeypatch.setenv('SKYPILOT_DB_POOL_CONNECTION_URI',
                           'postgresql://u:p@127.0.0.1:2/dbx?sslmode=disable')
        monkeypatch.delenv('SKYPILOT_DB_POOL_HOSTPORT', raising=False)
        monkeypatch.setattr(db_utils, '_postgres_engine_cache', {})
        pooled = db_utils.get_engine('state')
        direct = db_utils.get_engine('state', direct=True)
        no_pool = db_utils.get_engine('state', no_pool=True)
        async_engine = db_utils.get_engine('state', async_engine=True)
        assert len({id(pooled), id(direct), id(no_pool)}) == 3
        for engine in (pooled, direct, no_pool):
            assert engine.dialect.driver == 'psycopg2', engine.url
            deadline.set_deadline(time.monotonic() + 5)
            assert deadline.is_bounding(engine), engine.url
            deadline.clear_deadline()
            assert not deadline.is_bounding(engine)
        deadline.set_deadline(time.monotonic() + 5)
        assert not deadline.is_bounding(async_engine)

    def test_install_is_idempotent(self):
        engine, emitted = self._spy_engine(pretend_postgres=True)
        deadline.install(engine)  # second call: no second listener
        deadline.set_deadline(time.monotonic() + 4.5)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('SELECT 1'))
        assert len(emitted) == 1
        assert len(_PREFIX_RE.match(emitted[0]).group(0).split('; ')) == 4

    def test_no_deadline_emits_no_set_local(self):
        engine, emitted = self._spy_engine(pretend_postgres=True)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('SELECT 1'))
        assert not self._prefixes(emitted), emitted
        assert not deadline.is_bounding(engine)

    def test_first_statement_of_a_bounded_transaction_gets_the_prefix(self):
        engine, emitted = self._spy_engine(pretend_postgres=True)
        deadline.set_deadline(time.monotonic() + 4.5)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('SELECT 1'))
            conn.execute(sqlalchemy.text('SELECT 2'))
        assert len(emitted) == 2
        first, second = emitted[0], emitted[1]
        assert first.startswith('SET LOCAL statement_timeout = ')
        assert 'SET LOCAL lock_timeout = ' in first
        assert 'SET LOCAL idle_in_transaction_session_timeout = ' in first
        assert first.endswith('; SELECT 1')
        # Only the first statement of the transaction.
        assert second == 'SELECT 2'

    def test_a_new_transaction_is_prefixed_again(self):
        # Same DBAPI connection (one Connection held open), three transactions
        # in a row: bounded, unbounded, bounded. COMMIT puts the driver back to
        # READY, so the third gets the prefix although the connection already
        # carried it once -- no per-connection mark to go stale.
        engine, emitted = self._spy_engine(pretend_postgres=True)
        with engine.connect() as conn:
            deadline.set_deadline(time.monotonic() + 4.5)
            with conn.begin():
                conn.execute(sqlalchemy.text('SELECT 1'))
            deadline.clear_deadline()
            with conn.begin():
                conn.execute(sqlalchemy.text('SELECT 2'))
            deadline.set_deadline(time.monotonic() + 4.5)
            with conn.begin():
                conn.execute(sqlalchemy.text('SELECT 3'))
                conn.execute(sqlalchemy.text('SELECT 4'))
        bare = [_PREFIX_RE.sub('', s) for s in emitted]
        assert bare == ['SELECT 1', 'SELECT 2', 'SELECT 3', 'SELECT 4']
        prefixed = [s.startswith('SET LOCAL') for s in emitted]
        assert prefixed == [True, False, True, False]

    def test_session_autobegin_is_prefixed(self):
        engine, emitted = self._spy_engine(pretend_postgres=True)
        deadline.set_deadline(time.monotonic() + 4.5)
        with orm.Session(engine) as session:
            session.execute(sqlalchemy.text('SELECT 1'))
            session.execute(sqlalchemy.text('SELECT 2'))
            session.commit()
        prefixed = [s.startswith('SET LOCAL') for s in emitted]
        assert prefixed == [True, False]

    def test_executemany_is_not_prefixed(self):
        engine, emitted = self._spy_engine(pretend_postgres=True)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('CREATE TABLE t (x INTEGER)'))
        emitted.clear()
        deadline.set_deadline(time.monotonic() + 4.5)
        with engine.begin() as conn:
            # Opens the transaction; per-row text, so no prefix. The
            # transaction is then open (BEGIN): the next statement is not its
            # first and gets no prefix either -- a transaction OPENED by an
            # executemany has no server-side bound (documented; the auth path
            # never does this).
            conn.execute(sqlalchemy.text('INSERT INTO t (x) VALUES (:x)'), [{
                'x': 1
            }, {
                'x': 2
            }])
            conn.execute(sqlalchemy.text('SELECT count(*) FROM t'))
        with engine.begin() as conn:
            # A new transaction is bounded again.
            conn.execute(sqlalchemy.text('SELECT count(*) FROM t'))
        prefixed = [s.startswith('SET LOCAL') for s in emitted]
        assert prefixed == [False, False, True], emitted

    @pytest.mark.parametrize('other_listener_first', [True, False])
    def test_exactly_one_prefix_with_a_begin_listener_in_either_order(
            self, other_listener_first):
        # Another engine listener runs a statement from the `begin` event
        # (the shape of an engine-wide idle-in-transaction bound). Whatever
        # the registration order, that statement is the transaction's first
        # and carries the one prefix; the caller's own statements do not.
        def other(engine):

            @sqlalchemy.event.listens_for(engine, 'begin')
            def _begin(conn):  # pylint: disable=unused-variable
                conn.exec_driver_sql('SELECT 42').close()

        if other_listener_first:
            engine, emitted = self._spy_engine(pretend_postgres=True,
                                               before_install=other)
        else:
            engine, emitted = self._spy_engine(pretend_postgres=True)
            other(engine)
        deadline.set_deadline(time.monotonic() + 4.5)
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('SELECT 1'))
            conn.execute(sqlalchemy.text('SELECT 2'))
        with engine.begin() as conn:  # and again for the next transaction
            conn.execute(sqlalchemy.text('SELECT 3'))
        bare = [_PREFIX_RE.sub('', s) for s in emitted]
        assert bare == [
            'SELECT 42', 'SELECT 1', 'SELECT 2', 'SELECT 42', 'SELECT 3'
        ]
        prefixed = [s.startswith('SET LOCAL') for s in emitted]
        assert prefixed == [True, False, False, True, False], emitted

    def test_is_bounding_needs_the_listener_and_a_deadline(self):
        engine, _ = self._spy_engine(pretend_postgres=True)
        assert not deadline.is_bounding(engine)
        deadline.set_deadline(time.monotonic() + 4.5)
        assert deadline.is_bounding(engine)
        deadline.clear_deadline()
        assert not deadline.is_bounding(engine)
        # An engine without the listener is never "bounding", deadline or not.
        deadline.set_deadline(time.monotonic() + 4.5)
        assert not deadline.is_bounding(sqlalchemy.create_engine('sqlite://'))

    def _stub_conn(self, autocommit=False, status='ready'):
        conn = mock.Mock()
        conn.info = {}
        conn.connection.dbapi_connection.autocommit = autocommit
        conn.connection.dbapi_connection.status = (
            psycopg2.extensions.STATUS_READY
            if status == 'ready' else psycopg2.extensions.STATUS_BEGIN)
        return conn

    def test_named_cursor_is_not_prefixed(self):
        # A server-side cursor wraps the text in DECLARE ... FOR <statement>;
        # a leading SET LOCAL there is a syntax error.
        deadline.set_deadline(time.monotonic() + 4.5)
        conn = self._stub_conn()
        cursor = mock.Mock()
        cursor.name = 'stream'
        assert deadline._bounded_statement(conn, cursor, 'SELECT 1',
                                           False) == 'SELECT 1'
        cursor.name = None
        assert deadline._bounded_statement(conn, cursor, 'SELECT 1',
                                           False).endswith('; SELECT 1')

    def test_autocommit_connection_is_not_prefixed(self):
        deadline.set_deadline(time.monotonic() + 4.5)
        conn = self._stub_conn(autocommit=True)
        cursor = mock.Mock()
        cursor.name = None
        assert deadline._bounded_statement(conn, cursor, 'SELECT 1',
                                           False) == 'SELECT 1'

    def test_an_open_transaction_is_not_prefixed_again(self):
        deadline.set_deadline(time.monotonic() + 4.5)
        conn = self._stub_conn(status='begin')
        cursor = mock.Mock()
        cursor.name = None
        assert deadline._bounded_statement(conn, cursor, 'SELECT 1',
                                           False) == 'SELECT 1'

    def test_a_connection_without_a_driver_status_is_left_alone(self):
        deadline.set_deadline(time.monotonic() + 4.5)
        conn = self._stub_conn()
        del conn.connection.dbapi_connection.status
        cursor = mock.Mock()
        cursor.name = None
        assert deadline._bounded_statement(conn, cursor, 'SELECT 1',
                                           False) == 'SELECT 1'

    def test_prefix_is_sized_from_the_remaining_budget(self):
        deadline.set_deadline(time.monotonic() + 2.0)
        conn = self._stub_conn()
        cursor = mock.Mock()
        cursor.name = None
        text = deadline._bounded_statement(conn, cursor, 'SELECT 1', False)
        statement_ms = int(
            re.search(r'statement_timeout = (\d+)', text).group(1))
        # 2000 remaining - 500 server margin, minus the microseconds elapsed.
        assert 1400 <= statement_ms <= 1500, text

    def test_prefix_values_are_ordered(self):
        text = deadline._set_local_prefix(4500)

        def _val(name):
            return int(re.search(rf'{name} = (\d+)', text).group(1))

        assert _val('lock_timeout') < _val('statement_timeout')
        assert (_val('idle_in_transaction_session_timeout') >=
                _val('statement_timeout'))
        # statement fires the server margin before the client deadline.
        assert _val('statement_timeout') == 4500 - deadline._SERVER_MARGIN_MS
        assert '%' not in text

    def test_prefix_values_floor_at_minimum(self):
        text = deadline._set_local_prefix(10)  # tiny remaining budget

        def _val(name):
            return int(re.search(rf'{name} = (\d+)', text).group(1))

        assert _val('statement_timeout') == deadline._MIN_TIMEOUT_MS
        assert _val('lock_timeout') == deadline._MIN_TIMEOUT_MS
