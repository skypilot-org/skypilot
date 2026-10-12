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
# The revision that creates the table, i.e. the state a database is in before
# the index revision runs. Kept as a name because these numbers move whenever
# another PR lands a migration first.
_TABLE_REVISION = '024'  # 024_add_launch_attempts.py
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


def test_creating_the_table_is_idempotent(tmp_path):
    """Running the upgrade again changes nothing and raises nothing.

    The table-creating revision is the only one for this feature, so it has to
    be safe to re-enter: an upgrade that raises fails the whole chain, which
    takes the server down over a metrics table.

    Note what having a single revision gives up. A database stamped at this
    revision but missing the table cannot be repaired by the migration chain --
    alembic never re-runs a stamped revision, and there is no later one to do
    it. That state was reachable while these migrations were being renumbered
    under a live dev database; it is not reachable from a fresh install, which
    runs this revision once.
    """
    url = _fresh(tmp_path)

    _upgrade(url)

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
