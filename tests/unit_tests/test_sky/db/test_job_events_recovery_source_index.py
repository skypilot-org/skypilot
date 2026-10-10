"""The job_events sourced-rows index (migration 030), run against a real DB.

Reflection goes through a connection opened after the migration ran, for
the reason test_launch_attempt_migrations.py gives: an engine connected
before it can answer from the schema it saw then.
"""
import sqlalchemy

from sky.utils.db import migration_utils

_TABLE = 'job_events'
_INDEX = 'ix_job_events_recovery_source_sourced'


def _upgrade(url):
    engine = sqlalchemy.create_engine(url)
    try:
        migration_utils.safe_alembic_upgrade(engine,
                                             migration_utils.SPOT_JOBS_DB_NAME,
                                             migration_utils.SPOT_JOBS_VERSION)
    finally:
        engine.dispose()


def _indexes(url):
    engine = sqlalchemy.create_engine(url)
    try:
        return {
            ix['name']: ix
            for ix in sqlalchemy.inspect(engine).get_indexes(_TABLE)
        }
    finally:
        engine.dispose()


def _fresh(tmp_path):
    url = f'sqlite:///{tmp_path}/spot_jobs.db'
    _upgrade(url)
    return url


def test_a_fresh_database_gets_the_sourced_rows_index(tmp_path):
    """The recovery-event metric counts every sourced row, whatever status
    it carries, and relies on this partial index to do so without scanning
    job_events."""
    indexes = _indexes(_fresh(tmp_path))

    assert _INDEX in indexes
    assert indexes[_INDEX]['column_names'] == ['recovery_source']
    where = indexes[_INDEX].get('dialect_options', {}).get('sqlite_where')
    assert where is not None and 'recovery_source IS NOT NULL' in str(where)
    # The status-and-source index the other consumers filter on stays.
    assert 'ix_job_events_new_status_recovery_source' in indexes


def test_upgrading_again_is_idempotent(tmp_path):
    url = _fresh(tmp_path)

    _upgrade(url)

    assert _INDEX in _indexes(url)
