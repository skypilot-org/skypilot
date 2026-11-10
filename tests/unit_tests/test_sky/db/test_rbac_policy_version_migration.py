"""The rbac_policy_version migration, run against a real database.

On SQLite always, and on PostgreSQL when SKYPILOT_TEST_PG_URL is set.
"""
# pylint: disable=redefined-outer-name
import importlib.util
import os
import pathlib

from alembic import command as alembic_command
import pytest
import sqlalchemy

from sky.utils.db import migration_utils

_PG_URL = os.environ.get('SKYPILOT_TEST_PG_URL')
# Own schema, so other PostgreSQL tests resetting `public` do not interfere.
_PG_SCHEMA = 'rbac_policy_version_migration_test'
_REVISION = '027'  # 027_rbac_policy_version.py
_PG_TRIGGER = 'casbin_rule_bump_policy_version'


def _pg_url():
    # Pin the driver: the same URL also serves psycopg2.connect() elsewhere.
    return sqlalchemy.engine.make_url(_PG_URL).set(
        drivername='postgresql+psycopg2')


def _load_revision():
    path = next(
        pathlib.Path(migration_utils.__file__).parents[2].joinpath(
            'schemas', 'db', 'global_user_state').glob(f'{_REVISION}_*.py'))
    spec = importlib.util.spec_from_file_location('rbac_revision', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=[
    'sqlite',
    pytest.param('postgres',
                 marks=[
                     pytest.mark.skipif(
                         not _PG_URL, reason='SKYPILOT_TEST_PG_URL is not set'),
                     pytest.mark.xdist_group('rbac_policy_version_pg')
                 ])
])
def url(request, tmp_path):
    """A migrated global_user_state database."""
    if request.param == 'sqlite':
        url = f'sqlite:///{tmp_path}/state.db'
    else:
        admin = sqlalchemy.create_engine(_pg_url())
        with admin.begin() as conn:
            conn.execute(
                sqlalchemy.text(f'DROP SCHEMA IF EXISTS {_PG_SCHEMA} CASCADE'))
            conn.execute(sqlalchemy.text(f'CREATE SCHEMA {_PG_SCHEMA}'))
        admin.dispose()
        url = _pg_url().update_query_dict(
            {'options': f'-csearch_path={_PG_SCHEMA}'})
    engine = sqlalchemy.create_engine(url)
    migration_utils.safe_alembic_upgrade(
        engine, migration_utils.GLOBAL_USER_STATE_DB_NAME, _REVISION)
    engine.dispose()
    return url


def _query(url, statement):
    engine = sqlalchemy.create_engine(url)
    try:
        with engine.begin() as conn:
            result = conn.execute(sqlalchemy.text(statement))
            return result.fetchall() if result.returns_rows else None
    finally:
        engine.dispose()


def _triggers(url):
    if str(url).startswith('sqlite'):
        return _query(
            url, 'SELECT name FROM sqlite_master WHERE type = \'trigger\'')
    return _query(
        url, 'SELECT tgname FROM pg_trigger WHERE '
        'tgrelid = to_regclass(\'casbin_rule\') AND NOT tgisinternal')


def test_rerun_converges(url):
    revision = _load_revision()
    engine = sqlalchemy.create_engine(url)
    with engine.begin() as conn:
        revision.upgrade_on(conn)
    engine.dispose()
    assert _query(url,
                  'SELECT id, version FROM rbac_policy_version') == [(1, 0)]
    assert len(_triggers(url)) == (3 if str(url).startswith('sqlite') else 1)


@pytest.mark.skipif(not _PG_URL, reason='SKYPILOT_TEST_PG_URL is not set')
@pytest.mark.xdist_group('rbac_policy_version_pg')
def test_write_from_another_search_path_is_counted(url):
    if str(url).startswith('sqlite'):
        pytest.skip('Postgres only')
    # This session's search_path does not include the schema.
    other = sqlalchemy.create_engine(
        _pg_url(), connect_args={'options': '-csearch_path=public'})
    with other.begin() as conn:
        conn.execute(
            sqlalchemy.text(f'INSERT INTO {_PG_SCHEMA}.casbin_rule '
                            '(ptype, v0, v1) VALUES (\'g\', \'a\', \'user\')'))
    other.dispose()
    assert _query(url, 'SELECT version FROM rbac_policy_version') == [(1,)]


def test_downgrade_keeps_casbin_rule_writable(url):
    engine = sqlalchemy.create_engine(url)
    alembic_command.downgrade(
        migration_utils.get_alembic_config(
            engine, migration_utils.GLOBAL_USER_STATE_DB_NAME), '026')
    engine.dispose()
    _query(
        url, 'INSERT INTO casbin_rule (ptype, v0, v1) '
        'VALUES (\'g\', \'alice\', \'user\')')
    _query(url, 'DELETE FROM casbin_rule')
    assert _triggers(url) == []
    engine = sqlalchemy.create_engine(url)
    assert 'rbac_policy_version' not in sqlalchemy.inspect(
        engine).get_table_names()
    engine.dispose()
