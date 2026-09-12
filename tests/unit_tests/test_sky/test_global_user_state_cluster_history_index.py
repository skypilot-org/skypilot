"""Unit tests for the cluster_history indexes in global_user_state."""
from typing import Dict, List

import sqlalchemy

from sky import global_user_state
from sky.skylet import constants
from sky.utils.db import db_utils


def _fresh_db(tmp_path, monkeypatch):
    """Point the global state DB at a tmp sqlite file (mirrors the helper in
    test_global_user_state_cluster_events.py).

    The database is built by running the alembic migrations from scratch, so
    these tests exercise the migrations rather than the table metadata.
    """
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


def _indexes(table_name: str) -> Dict[str, List[str]]:
    """Returns {index name: indexed columns} for a table of the state DB.

    Reflected through a connection opened after the migrations ran: SQLite
    answers PRAGMA index_list from the schema its connection loaded when it
    was opened, and the state engine's pooled connection predates the
    migration, so reflecting through that connection reports no indexes at
    all.
    """
    url = global_user_state._db_manager.get_engine().url
    engine = sqlalchemy.create_engine(url)
    try:
        indexes = sqlalchemy.inspect(engine).get_indexes(table_name)
    finally:
        engine.dispose()
    return {index['name']: list(index['column_names']) for index in indexes}


def test_fresh_db_indexes_cluster_history_name(tmp_path, monkeypatch):
    """cluster_history is looked up by name and never pruned, so `name` must
    be indexed on a freshly migrated database."""
    _fresh_db(tmp_path, monkeypatch)

    indexes = _indexes('cluster_history')
    assert 'ix_cluster_history_name' in indexes, sorted(indexes)
    assert indexes['ix_cluster_history_name'] == ['name']


def _rewind_to_023(engine):
    """Make the state DB look like one migrated before the index existed."""
    with engine.begin() as connection:
        connection.execute(
            sqlalchemy.text(
                'UPDATE alembic_version_state_db SET version_num = :version'),
            {'version': '023'})


def test_migration_adds_the_index_to_an_existing_db(tmp_path, monkeypatch):
    """A database created before 024 gets the index from the migration.

    A fresh database picks the index up from the table metadata (migration
    001 creates every table from it), so the migration itself is only
    reachable by taking the index away again.
    """
    _fresh_db(tmp_path, monkeypatch)
    engine = global_user_state._db_manager.get_engine()
    with engine.begin() as connection:
        connection.execute(
            sqlalchemy.text('DROP INDEX ix_cluster_history_name'))
    _rewind_to_023(engine)
    assert 'ix_cluster_history_name' not in _indexes('cluster_history')

    global_user_state.create_table(engine)

    indexes = _indexes('cluster_history')
    assert indexes['ix_cluster_history_name'] == ['name'], sorted(indexes)


def test_migration_skips_an_index_that_already_exists(tmp_path, monkeypatch):
    """The migration must tolerate a database that already has the index.

    Rewinding the alembic version makes the upgrade run a second time against
    a database where the index is already present, which is the case the
    guard in the migration exists for.
    """
    _fresh_db(tmp_path, monkeypatch)
    engine = global_user_state._db_manager.get_engine()
    _rewind_to_023(engine)

    # Must not raise (the index already exists).
    global_user_state.create_table(engine)

    indexes = _indexes('cluster_history')
    assert indexes['ix_cluster_history_name'] == ['name'], sorted(indexes)
