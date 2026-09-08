"""The users upsert bounds its own Postgres transaction with SET LOCAL timeouts.

`add_or_update_user` runs on the request authentication path under a
client-side deadline (`sky.server.auth.db_lookup.AUTH_DB_TIMEOUT_SECONDS`)
that frees the caller but not the executor thread. On Postgres the function
therefore also asks the database to give up: `SET LOCAL lock_timeout /
statement_timeout / idle_in_transaction_session_timeout`, issued as the
first statements of the transaction so they cover the upsert. `SET LOCAL` is
transaction-scoped, so it resets at COMMIT and is safe through a
transaction-mode connection pooler.

These tests pin:

* Postgres: the three SET LOCAL statements are the first statements issued,
  on the same session (hence inside the same auto-begun transaction) as the
  upsert, and the values stay at or below the auth deadline;
* SQLite: no SET LOCAL at all (unsupported there); behavior unchanged.
"""

# pylint: disable=protected-access,redefined-outer-name,missing-class-docstring
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

_EXPECTED_SET_LOCAL = (
    f'SET LOCAL lock_timeout = '
    f'\'{global_user_state._USER_UPSERT_LOCK_TIMEOUT_MS}ms\'',
    f'SET LOCAL statement_timeout = '
    f'\'{global_user_state._USER_UPSERT_STATEMENT_TIMEOUT_MS}ms\'',
    f'SET LOCAL idle_in_transaction_session_timeout = '
    f'\'{global_user_state._USER_UPSERT_IDLE_IN_TRANSACTION_TIMEOUT_MS}ms\'',
)


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
        assert _set_local_texts(statements[:3]) == list(_EXPECTED_SET_LOCAL)
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
        assert _set_local_texts(statements[:3]) == list(_EXPECTED_SET_LOCAL)
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


class TestTimeoutValues:
    """The server-side bounds must be at or below the caller's deadline."""

    def test_values_are_at_or_below_the_auth_deadline(self):
        deadline_ms = db_lookup.AUTH_DB_TIMEOUT_SECONDS * 1000
        assert global_user_state._USER_UPSERT_LOCK_TIMEOUT_MS <= deadline_ms
        assert (global_user_state._USER_UPSERT_STATEMENT_TIMEOUT_MS <=
                deadline_ms)
        assert (global_user_state._USER_UPSERT_IDLE_IN_TRANSACTION_TIMEOUT_MS <=
                deadline_ms)

    def test_lock_timeout_wins_over_statement_timeout(self):
        """Lower lock_timeout so a row-lock wait reports the distinct
        'lock not available' error (55P03) rather than a generic cancel."""
        assert (global_user_state._USER_UPSERT_LOCK_TIMEOUT_MS <
                global_user_state._USER_UPSERT_STATEMENT_TIMEOUT_MS)

    def test_idle_in_transaction_is_the_largest(self):
        """It terminates the session, so it must only fire for a session that
        has really gone quiet mid-transaction, after the statement bounds."""
        assert (global_user_state._USER_UPSERT_IDLE_IN_TRANSACTION_TIMEOUT_MS >=
                global_user_state._USER_UPSERT_STATEMENT_TIMEOUT_MS)


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
    assert compiled == list(_EXPECTED_SET_LOCAL)
    for text in compiled:
        assert ';' not in text
        assert isinstance(sqlalchemy.text(text), elements.TextClause)
