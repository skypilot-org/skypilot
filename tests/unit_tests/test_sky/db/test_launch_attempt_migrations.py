"""The launch_attempts migrations, run against a real database.

Every reflection here goes through a connection opened after the migration
ran: an engine that was already connected before it can answer from the schema
it saw then, which made an earlier version of these tests report no indexes on
a database that had them.
"""
import sqlalchemy

from sky import global_user_state
from sky.utils.db import migration_utils

_TABLE = 'launch_attempts'
_WANTED = {
    'ix_launch_attempts_cluster',
    'ix_launch_attempts_provision_start',
    'ix_launch_attempts_cluster_on_cloud',
    'ix_launch_attempts_open',
}


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


def _indexes(url):
    return _inspect(url, lambda i: {ix['name'] for ix in i.get_indexes(_TABLE)})


def _tables(url):
    return _inspect(url, lambda i: set(i.get_table_names()))


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


def test_a_fresh_database_gets_every_index(tmp_path):
    """Creating the table brings its indexes with it.

    Worth pinning: the lookups this feature puts on the provision path scan
    the whole table without them, and nothing else would notice.
    """
    assert _WANTED <= _indexes(_fresh(tmp_path))


def test_a_database_already_at_023_still_gets_the_new_indexes(tmp_path):
    """The case the separate revision exists for.

    Alembic never re-runs a stamped revision, so indexes added to 023 after
    someone ran it would never reach their database.
    """
    url = _fresh(tmp_path)
    _execute(url, 'DROP INDEX ix_launch_attempts_cluster_on_cloud',
             'DROP INDEX ix_launch_attempts_open',
             "UPDATE alembic_version_state_db SET version_num = '023'")

    _upgrade(url)

    assert _WANTED <= _indexes(url)


def test_the_index_revision_survives_a_missing_table(tmp_path):
    """Inspecting a table that is not there raises.

    A migration that raises fails the whole upgrade, which takes the server
    down -- over a metrics table. Found on a real database stamped at 023
    without the table, so the state is reachable however it got there.
    """
    url = _fresh(tmp_path)
    _execute(url, f'DROP TABLE {_TABLE}',
             "UPDATE alembic_version_state_db SET version_num = '023'")

    _upgrade(url)

    # Recreated rather than merely skipped: skipping would leave such a
    # database without the feature for good.
    assert _TABLE in _tables(url)
    assert _WANTED <= _indexes(url)


def test_the_migrated_table_matches_what_the_code_queries(tmp_path):
    """The migration builds the table from the metadata the code reads."""
    url = _fresh(tmp_path)
    columns = _inspect(url,
                       lambda i: {c['name'] for c in i.get_columns(_TABLE)})

    assert columns == {
        c.name for c in global_user_state.launch_attempt_table.columns
    }
