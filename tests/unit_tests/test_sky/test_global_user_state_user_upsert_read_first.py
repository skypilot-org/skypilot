"""`add_or_update_user` writes the users row only for a new or changed user.

Every authenticated request calls it with the caller's identity, and nearly
always with values the row already holds. It reads the row first and writes
only when the row is missing or one of the fields the upsert sets differs, so
concurrent requests of one user do not each take that row's lock.

These run the real function against a real SQLite state DB and record every
statement that reaches the database.
"""

# pylint: disable=protected-access,redefined-outer-name
from typing import Iterator, List

import pytest
from sqlalchemy import event

from sky import global_user_state
from sky import models
from sky.skylet import constants
from sky.utils.db import db_utils


@pytest.fixture
def executed(tmp_path, monkeypatch) -> Iterator[List[str]]:
    """A fresh SQLite state DB; yields the SQL of every statement run."""
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
    engine = global_user_state._db_manager.get_engine()
    statements: List[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        del conn, cursor, parameters, context, executemany
        statements.append(statement)

    event.listen(engine, 'before_cursor_execute', _capture)
    yield statements
    event.remove(engine, 'before_cursor_execute', _capture)


def _writes(statements: List[str]) -> List[str]:
    return [
        s for s in statements
        if s.lstrip().upper().startswith(('INSERT', 'UPDATE'))
    ]


def test_new_user_is_inserted(executed):
    assert global_user_state.add_or_update_user(
        models.User(id='u-1', name='alice', user_type='legacy')) is True
    assert _writes(executed)
    stored = global_user_state.get_user('u-1')
    assert stored is not None
    assert (stored.name, stored.user_type) == ('alice', 'legacy')


def test_unchanged_user_is_not_written(executed):
    user = models.User(id='u-1', name='alice', user_type='legacy')
    global_user_state.add_or_update_user(user)
    executed.clear()

    assert global_user_state.add_or_update_user(user) is False
    assert executed, 'the row must still be read'
    assert not _writes(executed)


def test_unchanged_user_with_return_user_gets_the_stored_row(executed):
    global_user_state.add_or_update_user(
        models.User(id='u-1', name='alice', user_type='legacy', password='h1'))
    global_user_state.set_user_preferred_workspace('u-1', 'team-a')
    stored = global_user_state.get_user('u-1')
    executed.clear()

    newly_added, user = global_user_state.add_or_update_user(models.User(
        id='u-1', name='alice'),
                                                             return_user=True)

    assert newly_added is False
    assert not _writes(executed)
    assert user == stored


@pytest.mark.parametrize(
    'update, expected',
    [
        # A changed field is written.
        (models.User(id='u-1', name='alice2', user_type='legacy',
                     password='h1'), ('alice2', 'legacy', 'h1')),
        (models.User(id='u-1', name='alice', user_type='sso', password='h1'),
         ('alice', 'sso', 'h1')),
        (models.User(id='u-1', name='alice', user_type='legacy', password='h2'),
         ('alice', 'legacy', 'h2')),
    ])
def test_changed_user_is_written(executed, update, expected):
    global_user_state.add_or_update_user(
        models.User(id='u-1', name='alice', user_type='legacy', password='h1'))
    executed.clear()

    assert global_user_state.add_or_update_user(update) is False
    assert _writes(executed)
    stored = global_user_state.get_user('u-1')
    assert stored is not None
    assert (stored.name, stored.user_type, stored.password) == expected


@pytest.mark.parametrize(
    'update',
    [
        # The upsert never clears the type or password, so leaving them out
        # is not a change.
        models.User(id='u-1', name='alice'),
        models.User(id='u-1', name='alice', user_type='legacy'),
        models.User(id='u-1', name='alice', password='h1'),
    ])
def test_omitted_type_or_password_is_not_a_change(executed, update):
    global_user_state.add_or_update_user(
        models.User(id='u-1', name='alice', user_type='legacy', password='h1'))
    executed.clear()

    assert global_user_state.add_or_update_user(update) is False
    assert not _writes(executed)
    stored = global_user_state.get_user('u-1')
    assert stored is not None
    assert (stored.name, stored.user_type, stored.password) == ('alice',
                                                                'legacy', 'h1')
