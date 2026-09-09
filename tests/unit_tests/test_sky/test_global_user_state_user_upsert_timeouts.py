"""The users upsert bounds its own Postgres transaction with SET LOCAL timeouts.

`add_or_update_user` runs on the request authentication path under a
client-side deadline (`sky.server.auth.db_lookup.AUTH_DB_TIMEOUT_SECONDS`)
that frees the caller but not the executor thread. On Postgres the function
therefore also asks the database to give up: `SET LOCAL lock_timeout /
statement_timeout / idle_in_transaction_session_timeout`, issued as the
first statements of the transaction so they cover the upsert. `SET LOCAL` is
transaction-scoped, so it resets at COMMIT and is safe through a
transaction-mode connection pooler.

The three values are derived from the configured deadline
(`SKYPILOT_AUTH_DB_TIMEOUT_SECONDS`, default 5 s), which both modules read
through the one helper `db_utils.get_auth_db_timeout_seconds()`.

These tests pin:

* Postgres: the three SET LOCAL statements are the first statements issued,
  on the same session (hence inside the same auto-begun transaction) as the
  upsert;
* the values: exactly 3900 / 4000 / 5000 ms at the default deadline (the
  values a deployment on the default already runs with), derived from the
  configured deadline otherwise, always `lock < statement < idle <= deadline`,
  and read from the same source as `db_lookup.AUTH_DB_TIMEOUT_SECONDS`; a
  nonsensical setting is refused loudly instead of reaching the database;
* SQLite: no SET LOCAL at all (unsupported there); behavior unchanged.
"""

# pylint: disable=protected-access,redefined-outer-name,missing-class-docstring
import os
import subprocess
import sys
from unittest import mock

import pytest
import sqlalchemy
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import elements

from sky import global_user_state
from sky import models
from sky.server.auth import db_lookup
from sky.skylet import constants
from sky.utils.db import db_utils

_DEADLINE_ENV = constants.ENV_VAR_AUTH_DB_TIMEOUT_SECONDS


def _expected_set_local():
    """The three statements the upsert must issue, for the current env."""
    lock_ms, statement_ms, idle_ms = global_user_state._user_upsert_timeouts_ms(
    )
    return [
        f'SET LOCAL lock_timeout = \'{lock_ms}ms\'',
        f'SET LOCAL statement_timeout = \'{statement_ms}ms\'',
        f'SET LOCAL idle_in_transaction_session_timeout = \'{idle_ms}ms\'',
    ]


class _RecordingSession:
    """Stand-in for `orm.Session` that records what is executed, in order.

    A query issued through `session.query(...)` is recorded as the string
    'QUERY' so its position relative to the SET LOCAL statements is visible.
    """

    def __init__(self):
        self.statements = []
        self.commit_after = None

    def execute(self, statement):
        self.statements.append(statement)
        result = mock.Mock()
        result.fetchone.return_value = mock.Mock(id='u-1',
                                                 name='tester',
                                                 password=None,
                                                 created_at=1,
                                                 type=None,
                                                 preferred_workspace=None,
                                                 was_inserted=True)
        result.rowcount = 1
        return result

    def query(self, *args, **kwargs):
        del args, kwargs
        self.statements.append('QUERY')
        chain = mock.Mock()
        chain.filter.return_value.first.return_value = None
        chain.filter_by.return_value.first.return_value = None
        return chain

    def commit(self):
        self.commit_after = len(self.statements)


@pytest.fixture
def postgres_session():
    """`add_or_update_user` against a fake Postgres engine + recording session.

    The engine is a mock whose dialect says Postgres; the ORM session is the
    recorder above, so the test sees exactly which statements the function
    issues and in what order, without a live database.
    """
    engine = mock.Mock()
    engine.dialect.name = db_utils.SQLAlchemyDialect.POSTGRESQL.value
    session = _RecordingSession()
    with mock.patch.object(global_user_state._db_manager, '_engine', engine), \
            mock.patch('sky.global_user_state.orm.Session') as session_cls:
        session_cls.return_value.__enter__.return_value = session
        yield session


def _set_local_texts(statements):
    return [
        str(s)
        for s in statements
        if isinstance(s, elements.TextClause) and str(s).startswith('SET LOCAL')
    ]


class TestPostgresUpsertIsBounded:

    def test_set_local_timeouts_precede_the_upsert_in_one_session(
            self, postgres_session):
        global_user_state.add_or_update_user(
            models.User(id='u-1', name='tester'))

        statements = postgres_session.statements
        # Exactly the three SET LOCALs, first, in this order.
        assert _set_local_texts(statements[:3]) == _expected_set_local()
        # Then the upsert itself, then COMMIT -- all on the same session, so
        # SQLAlchemy's autobegin puts the SET LOCALs and the INSERT in one
        # transaction and COMMIT resets the timeouts.
        assert len(statements) == 4
        assert isinstance(statements[3], postgresql.Insert)
        assert statements[3].table is global_user_state.user_table
        assert postgres_session.commit_after == 4

    def test_set_local_comes_before_the_duplicate_name_check_too(
            self, postgres_session):
        """With allow_duplicate_name=False the first statement is a SELECT;
        the timeouts must still be issued before it, or the SELECT (which
        can wait on the same row lock) runs unbounded."""
        global_user_state.add_or_update_user(models.User(id='u-1',
                                                         name='tester'),
                                             allow_duplicate_name=False)

        statements = postgres_session.statements
        assert _set_local_texts(statements[:3]) == _expected_set_local()
        assert statements[3] == 'QUERY'
        assert isinstance(statements[4], postgresql.Insert)

    def test_set_local_is_issued_through_the_session_not_a_hook(
            self, postgres_session):
        """Issued via `session.execute(text(...))`, so a failure (e.g. the
        pooler never assigns a server) surfaces through SQLAlchemy's normal
        error handling, not from inside an engine event listener."""
        global_user_state.add_or_update_user(
            models.User(id='u-1', name='tester'))
        for statement in postgres_session.statements[:3]:
            assert isinstance(statement, elements.TextClause)

    def test_no_bind_parameters_in_set_local(self, postgres_session):
        """SET does not accept bind parameters; the values are literals."""
        global_user_state.add_or_update_user(
            models.User(id='u-1', name='tester'))
        for statement in postgres_session.statements[:3]:
            assert not statement.compile().params

    def test_values_follow_the_configured_deadline(self, postgres_session,
                                                   monkeypatch):
        """The statements the database receives carry the derived values."""
        monkeypatch.setenv(_DEADLINE_ENV, '8')
        global_user_state.add_or_update_user(
            models.User(id='u-1', name='tester'))
        assert _set_local_texts(postgres_session.statements[:3]) == [
            'SET LOCAL lock_timeout = \'6240ms\'',
            'SET LOCAL statement_timeout = \'6400ms\'',
            'SET LOCAL idle_in_transaction_session_timeout = \'8000ms\'',
        ]


class TestAuthDbTimeoutSetting:
    """`get_auth_db_timeout_seconds` is the one source of the deadline."""

    def test_default_is_five_seconds(self, monkeypatch):
        monkeypatch.delenv(_DEADLINE_ENV, raising=False)
        assert db_utils.get_auth_db_timeout_seconds() == 5.0
        assert constants.DEFAULT_AUTH_DB_TIMEOUT_SECONDS == 5.0

    @pytest.mark.parametrize('raw, expected', [('8', 8.0), ('2.5', 2.5),
                                               ('0.05', 0.05)])
    def test_reads_the_environment(self, monkeypatch, raw, expected):
        monkeypatch.setenv(_DEADLINE_ENV, raw)
        assert db_utils.get_auth_db_timeout_seconds() == expected

    @pytest.mark.parametrize('raw',
                             ['abc', '', '0', '-1', 'nan', 'inf', '2147484'])
    def test_nonsensical_values_are_refused_loudly(self, monkeypatch, raw):
        """Not silently replaced by the default: a non-positive deadline
        would fail every auth call, `0` disables a Postgres timeout, and a
        deadline above Postgres' 32-bit millisecond range would make the
        SET LOCAL itself fail on every upsert."""
        monkeypatch.setenv(_DEADLINE_ENV, raw)
        with pytest.raises(ValueError, match=_DEADLINE_ENV):
            db_utils.get_auth_db_timeout_seconds()

    def test_largest_accepted_deadline_fits_postgres_milliseconds(
            self, monkeypatch):
        """Boundary: the largest accepted value derives timeouts that are
        still valid Postgres settings (<= 2147483647 ms); one more second
        is refused (see above)."""
        monkeypatch.setenv(_DEADLINE_ENV,
                           str(db_utils.AUTH_DB_TIMEOUT_MAX_SECONDS))
        assert (db_utils.get_auth_db_timeout_seconds() ==
                db_utils.AUTH_DB_TIMEOUT_MAX_SECONDS)
        for value_ms in global_user_state._user_upsert_timeouts_ms():
            assert 0 < value_ms <= 2147483647

    def test_db_lookup_deadline_comes_from_the_same_source(self):
        """`db_lookup.AUTH_DB_TIMEOUT_SECONDS` is read at import through the
        same helper; in the (unchanged) environment of this process the two
        agree, so the client-side deadline and the server-side bounds cannot
        be configured apart."""
        assert (db_lookup.AUTH_DB_TIMEOUT_SECONDS ==
                db_utils.get_auth_db_timeout_seconds())


class TestTimeoutValues:
    """The server-side bounds derive from, and stay under, the deadline."""

    def test_default_deadline_gives_exactly_3900_4000_5000(self, monkeypatch):
        """The values a deployment running on the default deadline already
        has; deriving them must not change them."""
        monkeypatch.delenv(_DEADLINE_ENV, raising=False)
        assert global_user_state._user_upsert_timeouts_ms() == (3900, 4000,
                                                                5000)

    def test_deadline_of_8s_gives_6240_6400_8000(self, monkeypatch):
        monkeypatch.setenv(_DEADLINE_ENV, '8')
        lock_ms, statement_ms, idle_ms = (
            global_user_state._user_upsert_timeouts_ms())
        assert (lock_ms, statement_ms, idle_ms) == (6240, 6400, 8000)
        assert lock_ms < statement_ms < idle_ms

    @pytest.mark.parametrize('raw', [None, '8', '2.5', '0.05', '12.345'])
    def test_values_are_at_or_below_the_auth_deadline(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv(_DEADLINE_ENV, raising=False)
        else:
            monkeypatch.setenv(_DEADLINE_ENV, raw)
        deadline_ms = db_utils.get_auth_db_timeout_seconds() * 1000
        for value_ms in global_user_state._user_upsert_timeouts_ms():
            assert isinstance(value_ms, int)
            assert 0 < value_ms <= deadline_ms

    @pytest.mark.parametrize('raw', [None, '8', '2.5', '0.05', '12.345'])
    def test_lock_below_statement_below_idle(self, monkeypatch, raw):
        """lock_timeout < statement_timeout so a row-lock wait reports the
        distinct 'lock not available' error (55P03) rather than a generic
        cancel; idle_in_transaction_session_timeout is the largest because it
        terminates the session, so it must only fire for a session that has
        really gone quiet mid-transaction, after the statement bounds."""
        if raw is None:
            monkeypatch.delenv(_DEADLINE_ENV, raising=False)
        else:
            monkeypatch.setenv(_DEADLINE_ENV, raw)
        lock_ms, statement_ms, idle_ms = (
            global_user_state._user_upsert_timeouts_ms())
        assert lock_ms < statement_ms < idle_ms

    def test_a_deadline_too_small_to_order_the_values_is_refused(
            self, monkeypatch):
        """10 ms would round to 8 / 8 / 10: lock and statement collide, and
        a smaller deadline still could round a value to `0ms`, which Postgres
        reads as *disabled*. Refuse rather than send that."""
        monkeypatch.setenv(_DEADLINE_ENV, '0.01')
        with pytest.raises(ValueError, match=_DEADLINE_ENV):
            global_user_state._user_upsert_timeouts_ms()

    @pytest.mark.parametrize('raw', ['abc', '0', '-1'])
    def test_a_nonsensical_deadline_never_reaches_the_database(
            self, postgres_session, monkeypatch, raw):
        monkeypatch.setenv(_DEADLINE_ENV, raw)
        with pytest.raises(ValueError, match=_DEADLINE_ENV):
            global_user_state.add_or_update_user(
                models.User(id='u-1', name='tester'))
        assert not postgres_session.statements


_IMPORT_DB_LOOKUP = ('from sky.server.auth import db_lookup; '
                     'from sky import global_user_state; '
                     'print(db_lookup.AUTH_DB_TIMEOUT_SECONDS, '
                     'global_user_state._user_upsert_timeouts_ms())')


def _import_db_lookup_in_a_fresh_process(env_value):
    """`db_lookup` reads the deadline at import (server startup); run that
    import in a child so this process's module state is untouched."""
    env = dict(os.environ)
    env.pop(_DEADLINE_ENV, None)
    if env_value is not None:
        env[_DEADLINE_ENV] = env_value
    return subprocess.run([sys.executable, '-c', _IMPORT_DB_LOOKUP],
                          env=env,
                          capture_output=True,
                          text=True,
                          check=False)


class TestServerStartupReadsTheSetting:

    def test_override_is_seen_by_both_modules_at_import(self):
        result = _import_db_lookup_in_a_fresh_process('8')
        assert result.returncode == 0, result.stderr
        # Last line only: the import may log to stdout before the print.
        assert result.stdout.strip().splitlines()[-1] == ('8.0 (6240, 6400, '
                                                          '8000)')

    def test_a_nonsensical_value_fails_server_startup_with_its_name(self):
        result = _import_db_lookup_in_a_fresh_process('0')
        assert result.returncode != 0
        assert 'ValueError' in result.stderr
        assert _DEADLINE_ENV in result.stderr


def _fresh_sqlite_db(tmp_path, monkeypatch):
    """Point the global state DB at a tmp sqlite file (real engine)."""
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.setattr(
        global_user_state,
        '_db_manager',
        db_utils.DatabaseManager(
            'state',
            global_user_state.create_table,
            post_init_fn=lambda _: global_user_state._sqlite_supports_returning(
            ),
        ),
    )
    return global_user_state._db_manager.get_engine()


class TestSqliteUpsertIsUnchanged:

    def test_sqlite_issues_no_set_local(self, tmp_path, monkeypatch):
        engine = _fresh_sqlite_db(tmp_path, monkeypatch)
        assert engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value
        executed = []

        def _capture(conn, cursor, statement, parameters, context, executemany):
            del conn, cursor, parameters, context, executemany
            executed.append(statement)

        event.listen(engine, 'before_cursor_execute', _capture)
        try:
            newly_added = global_user_state.add_or_update_user(
                models.User(id='u-sqlite', name='tester'))
            again = global_user_state.add_or_update_user(
                models.User(id='u-sqlite', name='tester-renamed'))
        finally:
            event.remove(engine, 'before_cursor_execute', _capture)

        assert newly_added is True
        assert again is False
        assert executed, 'the upsert must have reached the database'
        assert not [s for s in executed if 'SET LOCAL' in s.upper()]
        stored = global_user_state.get_user('u-sqlite')
        assert stored is not None and stored.name == 'tester-renamed'

    def test_sqlite_engine_is_not_asked_for_postgres_settings(
            self, tmp_path, monkeypatch):
        """Belt and braces: SET LOCAL is a syntax error on SQLite, so the
        Postgres-only helper must never see a SQLite session."""
        _fresh_sqlite_db(tmp_path, monkeypatch)
        with mock.patch.object(global_user_state,
                               '_bound_user_upsert_transaction') as bound:
            global_user_state.add_or_update_user(
                models.User(id='u-sqlite-2', name='tester'))
        bound.assert_not_called()


def test_set_local_statement_text_is_valid_sql():
    """Compile the statements as Postgres SQL (no live DB): each is exactly
    one `SET LOCAL <parameter> = '<n>ms'`."""
    session = _RecordingSession()
    global_user_state._bound_user_upsert_transaction(session)
    compiled = [
        str(s.compile(dialect=postgresql.dialect())) for s in session.statements
    ]
    assert compiled == _expected_set_local()
    for text in compiled:
        assert ';' not in text
        assert isinstance(sqlalchemy.text(text), elements.TextClause)
