"""Tests for sky.utils.db.migrate_sqlite_to_postgres."""
import contextlib
import os
import sqlite3
import stat
from unittest import mock

import pytest
import yaml

from sky.utils.db import migrate_sqlite_to_postgres as migrate

_RECIPES_DDL = ('CREATE TABLE recipes (name TEXT PRIMARY KEY, '
                'description TEXT, content TEXT, recipe_type TEXT, '
                'pinned INTEGER, user_id TEXT, user_name TEXT, '
                'created_at REAL, is_editable INTEGER, is_pinnable INTEGER)')


@pytest.fixture()
def recipes(tmp_path):
    """A recipes table holding exactly the seeded default recipes."""
    connection = sqlite3.connect(tmp_path / 'recipes.db')
    connection.execute(_RECIPES_DDL)
    expected = migrate.default_recipes()
    for row in expected:
        connection.execute(
            f'INSERT INTO recipes ({", ".join(row)}, created_at) '
            f'VALUES ({", ".join("?" * len(row))}, 1.0)', list(row.values()))
    connection.commit()
    yield connection, expected
    connection.close()


def _count(connection):
    return connection.execute('SELECT count(*) FROM recipes').fetchone()[0]


def test_remove_default_recipes(recipes):
    connection, expected = recipes
    removed = migrate.remove_default_recipes(connection, '?', expected)
    assert removed == sorted(row['name'] for row in expected)
    assert _count(connection) == 0


def test_remove_default_recipes_aborts_on_extra_row(recipes):
    connection, expected = recipes
    connection.execute('INSERT INTO recipes (name) VALUES (\'mine\')')
    connection.commit()
    with pytest.raises(ValueError, match='differs'):
        migrate.remove_default_recipes(connection, '?', expected)
    assert _count(connection) == len(expected) + 1


def test_remove_default_recipes_aborts_on_changed_row(recipes):
    connection, expected = recipes
    connection.execute('UPDATE recipes SET content = \'x\' WHERE name = ?',
                       (expected[0]['name'],))
    connection.commit()
    with pytest.raises(ValueError, match='differs'):
        migrate.remove_default_recipes(connection, '?', expected)
    assert _count(connection) == len(expected)


def test_remove_default_recipes_aborts_on_missing_row(recipes):
    connection, expected = recipes
    connection.execute('DELETE FROM recipes WHERE name = ?',
                       (expected[0]['name'],))
    connection.commit()
    with pytest.raises(ValueError, match='differs'):
        migrate.remove_default_recipes(connection, '?', expected)


@pytest.mark.parametrize('dialect', ['sqlite', 'mysql'])
def test_require_postgres_rejects_other_dialects(dialect):
    engine = mock.Mock()
    engine.dialect.name = dialect
    with pytest.raises(ValueError, match='not PostgreSQL'):
        migrate.require_postgres(engine)


def test_require_postgres_accepts_postgres():
    engine = mock.Mock()
    engine.dialect.name = 'postgresql'
    migrate.require_postgres(engine)


@pytest.fixture()
def sky_dir(tmp_path):
    """SQLite stores whose committed rows are still only in the WAL."""
    root = tmp_path / 'sky'
    connections = []
    for relative in migrate.STORES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)')
        connection.execute('INSERT INTO t (v) VALUES (?)', (relative,))
        connection.commit()
        connections.append(connection)
    yield root
    for connection in connections:
        connection.close()


def test_freeze(sky_dir, tmp_path):
    frozen = tmp_path / 'frozen'
    migrate.freeze(sky_dir, frozen)
    for relative in migrate.STORES:
        path = frozen / relative
        assert stat.S_IMODE(path.stat().st_mode) == 0o444
        assert not list(path.parent.glob(path.name + '-*'))
        with contextlib.closing(migrate._readonly(path)) as connection:
            assert connection.execute(
                'PRAGMA journal_mode').fetchone()[0] == 'delete'
            assert connection.execute('SELECT v FROM t').fetchall() == [
                (relative,)
            ]


def test_freeze_missing_store(sky_dir, tmp_path):
    (sky_dir / 'recipes.db').unlink()
    with pytest.raises(FileNotFoundError):
        migrate.freeze(sky_dir, tmp_path / 'frozen')


def test_source_tables_rejects_duplicate_table(sky_dir):
    # Every store in the fixture has table t.
    with pytest.raises(ValueError, match='two databases'):
        migrate.source_tables(sky_dir)


# --- Copy into a real PostgreSQL (opt-in via SKYPILOT_TEST_PG_URL) ----------
#
# The copy, the empty-target check and the sequence handling are PostgreSQL
# SQL, so they run against a throwaway server and are skipped otherwise. The
# URL's database is reset by these tests.

_PG_URL = os.environ.get('SKYPILOT_TEST_PG_URL')
pg_only = pytest.mark.skipif(not _PG_URL,
                             reason='SKYPILOT_TEST_PG_URL is not set')

# Serial IDs, boolean/json/timestamptz columns and a cross-store foreign key.
_PG_DDL = """
CREATE TABLE config_yaml (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE recipes (name TEXT PRIMARY KEY, pinned BOOLEAN);
CREATE TABLE clusters (id SERIAL PRIMARY KEY, name TEXT, links JSON);
CREATE TABLE job_events (id SERIAL PRIMARY KEY,
                         cluster_id INTEGER REFERENCES clusters(id),
                         is_batch BOOLEAN, timestamp TIMESTAMPTZ, blob BYTEA);
CREATE TABLE services (name TEXT PRIMARY KEY, version INTEGER);
CREATE TABLE kv_cache (key TEXT PRIMARY KEY, value TEXT);
"""
_SQLITE_DDL = {
    # job_events comes before its foreign-key parent on purpose.
    'spot_jobs.db': ('CREATE TABLE job_events (id INTEGER PRIMARY KEY, '
                     'cluster_id INTEGER, is_batch INTEGER, timestamp TEXT, '
                     'blob BLOB)'),
    'state.db': ('CREATE TABLE clusters (id INTEGER PRIMARY KEY, name TEXT, '
                 'links TEXT)'),
    'serve/services.db': ('CREATE TABLE services (name TEXT PRIMARY KEY, '
                          'version INTEGER)'),
    'recipes.db': 'CREATE TABLE recipes (name TEXT PRIMARY KEY, pinned INT)',
    'kv_cache.db': 'CREATE TABLE kv_cache (key TEXT PRIMARY KEY, value TEXT)',
}
_CONFIG = {'allowed_clouds': ['kubernetes']}


def _sql(statement, *args):
    import psycopg2  # pylint: disable=import-outside-toplevel
    with contextlib.closing(psycopg2.connect(_PG_URL)) as target:
        with target, target.cursor() as cursor:
            cursor.execute(statement, args or None)
            return cursor.fetchall() if cursor.description else []


def _seed_config(dsn, config):
    del dsn
    _sql(
        'INSERT INTO config_yaml VALUES (\'api_server_config\', %s) '
        'ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value',
        yaml.safe_dump(config))


@pytest.fixture()
def pg_source(tmp_path):
    """SQLite stores with data, and a target with the matching schema."""
    root = tmp_path / 'sky'
    (root / 'serve').mkdir(parents=True)
    for relative, ddl in _SQLITE_DDL.items():
        with contextlib.closing(sqlite3.connect(root / relative)) as source:
            source.execute(ddl)
            source.execute('CREATE TABLE alembic_version_x (version_num TEXT)')
            source.commit()
    with contextlib.closing(sqlite3.connect(root / 'state.db')) as source:
        source.executemany('INSERT INTO clusters VALUES (?, ?, ?)',
                           [(3, 'a', '{"k": [1]}'), (8, 'b', None)])
        source.commit()
    with contextlib.closing(sqlite3.connect(root / 'spot_jobs.db')) as source:
        source.execute(
            'INSERT INTO job_events VALUES '
            '(5, 8, 1, \'2026-01-02 12:00:00\', ?)', (b'\x00\xff',))
        source.commit()
    _sql('DROP SCHEMA public CASCADE; CREATE SCHEMA public;' + _PG_DDL)
    with mock.patch.object(migrate, 'init_target', return_value=[]) as init, \
            mock.patch.object(migrate, 'seed_config', _seed_config):
        yield root, init
    _sql('DROP SCHEMA public CASCADE; CREATE SCHEMA public')


@pg_only
@pytest.mark.xdist_group('migrate_sqlite_to_postgres')
def test_migrate_copies_and_advances_sequences(pg_source):
    root, init = pg_source
    result = migrate.migrate(root, _CONFIG, _PG_URL)
    assert result['rows'] == {
        'clusters': 2,
        'job_events': 1,
        'kv_cache': 0,
        'recipes': 0,
        'services': 0,
    }
    (row,) = _sql('SELECT is_batch, timestamp AT TIME ZONE \'UTC\', blob '
                  'FROM job_events')
    assert row[0] is True
    assert str(row[1]) == '2026-01-02 12:00:00'
    assert bytes(row[2]) == b'\x00\xff'
    assert _sql('SELECT links FROM clusters WHERE id = 3') == [({'k': [1]},)]
    assert _sql('INSERT INTO clusters (name) VALUES (\'new\') '
                'RETURNING id') == [(9,)]
    assert yaml.safe_load(
        _sql('SELECT value FROM config_yaml')[0][0]) == (_CONFIG)
    init.assert_called_once()


@pg_only
@pytest.mark.xdist_group('migrate_sqlite_to_postgres')
def test_migrate_refuses_non_empty_target(pg_source):
    root, init = pg_source
    _sql('INSERT INTO services VALUES (\'left-over\', 1)')
    with pytest.raises(ValueError, match='not empty'):
        migrate.migrate(root, _CONFIG, _PG_URL)
    init.assert_not_called()


@pg_only
@pytest.mark.xdist_group('migrate_sqlite_to_postgres')
def test_migrate_refuses_rerun(pg_source):
    root, _ = pg_source
    migrate.migrate(root, _CONFIG, _PG_URL)
    with pytest.raises(ValueError, match='not empty'):
        migrate.migrate(root, _CONFIG, _PG_URL)


@pg_only
@pytest.mark.xdist_group('migrate_sqlite_to_postgres')
def test_failed_copy_rolls_back(pg_source):
    root, _ = pg_source
    with mock.patch.object(migrate, '_convert', side_effect=ValueError('boom')):
        with pytest.raises(ValueError, match='boom'):
            migrate.migrate(root, _CONFIG, _PG_URL)
    assert _sql('SELECT count(*) FROM clusters') == [(0,)]
    migrate.migrate(root, _CONFIG, _PG_URL)


@pg_only
@pytest.mark.xdist_group('migrate_sqlite_to_postgres')
def test_missing_target_table_fails(pg_source):
    root, _ = pg_source
    _sql('DROP TABLE kv_cache')
    with pytest.raises(ValueError, match='does not exist in PostgreSQL'):
        migrate.migrate(root, _CONFIG, _PG_URL)
