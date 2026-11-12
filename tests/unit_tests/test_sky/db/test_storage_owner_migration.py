"""The migration that records a storage's creator and workspace, run
against a real database.

A fresh database gets the columns from the table definition before the
migration runs, so the column-adding step is exercised here against a
database built the way an older version left it: a storage table without
the columns, stamped at the previous revision.
"""
import sqlalchemy

from sky import global_user_state
from sky.skylet import constants
from sky.utils.db import migration_utils

_TABLE = 'storage'
_PREVIOUS_REVISION = '027'  # 027_rbac_policy_version.py


def _upgrade(url):
    engine = sqlalchemy.create_engine(url)
    try:
        migration_utils.safe_alembic_upgrade(
            engine, migration_utils.GLOBAL_USER_STATE_DB_NAME,
            migration_utils.GLOBAL_USER_STATE_VERSION)
    finally:
        engine.dispose()


def _inspect(url, fn):
    engine = sqlalchemy.create_engine(url)
    try:
        return fn(sqlalchemy.inspect(engine))
    finally:
        engine.dispose()


def _columns(url):
    return _inspect(url, lambda i: {c['name'] for c in i.get_columns(_TABLE)})


def _execute(url, *statements):
    engine = sqlalchemy.create_engine(url)
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(sqlalchemy.text(statement))
    finally:
        engine.dispose()


def _fresh(tmp_path):
    url = f'sqlite:///{tmp_path}/state.db'
    _upgrade(url)
    return url


def _version_table(url):
    tables = _inspect(url, lambda i: i.get_table_names())
    (name,) = [t for t in tables if t.startswith('alembic_version')]
    return name


def _as_left_by_the_previous_version(tmp_path):
    """A database whose storage table predates the columns, holding one
    row, stamped at the revision before this migration."""
    url = _fresh(tmp_path)
    _execute(
        url, f'DROP TABLE {_TABLE}',
        f'CREATE TABLE {_TABLE} (name TEXT PRIMARY KEY, launched_at INTEGER, '
        'handle BLOB, last_use TEXT, status TEXT)',
        f'INSERT INTO {_TABLE} (name, launched_at, handle, last_use, status) '
        "VALUES ('old', 1, X'00', 'cmd', 'READY')",
        f'UPDATE {_version_table(url)} SET version_num = '
        f"'{_PREVIOUS_REVISION}'")
    assert not {'user_hash', 'workspace'} & _columns(url)
    return url


def test_fresh_database_matches_the_table_definition(tmp_path):
    assert _columns(_fresh(tmp_path)) == {
        c.name for c in global_user_state.storage_table.columns
    }


def test_migration_adds_the_columns(tmp_path):
    url = _as_left_by_the_previous_version(tmp_path)

    _upgrade(url)

    assert _columns(url) == {
        c.name for c in global_user_state.storage_table.columns
    }


def test_rows_from_before_the_columns_get_the_defaults(tmp_path):
    """The old row has no creator and sits in the default workspace."""
    url = _as_left_by_the_previous_version(tmp_path)

    _upgrade(url)

    engine = sqlalchemy.create_engine(url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sqlalchemy.text(
                    f'SELECT user_hash, workspace FROM {_TABLE}')).one()
    finally:
        engine.dispose()
    assert row == (None, constants.SKYPILOT_DEFAULT_WORKSPACE)


def test_upgrade_is_idempotent(tmp_path):
    url = _as_left_by_the_previous_version(tmp_path)
    _upgrade(url)
    before = _columns(url)

    _upgrade(url)

    assert _columns(url) == before
