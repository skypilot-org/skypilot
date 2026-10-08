"""Migrate an API server's state from SQLite to PostgreSQL.

One-off migration for an API server that was deployed without an external
database and should move to one. Run it with the same SkyPilot version as the
API server, while the API server is stopped, with the API server's ``~/.sky``
available and the target set in ``SKYPILOT_DB_CONNECTION_URI``:

.. code-block:: bash

    SKYPILOT_DB_CONNECTION_URI=postgresql://... \\
        python -m sky.utils.db.migrate_sqlite_to_postgres

The SQLite databases are only read, so rolling back is pointing the API server
back at them. Steps:

1. Refuse a target database that already holds SkyPilot data.
2. Take consistent copies of the SQLite databases with the SQLite backup API.
3. Create the schema in PostgreSQL with SkyPilot's own migrations, and remove
   the default recipes that this seeds into the empty database.
4. Copy every table in one transaction, check row counts and the largest value
   of each sequence-backed column, and move the sequences past those values.
5. Store the API server config (``--config``) in the database, where the API
   server reads it from when backed by PostgreSQL.
"""
import argparse
import contextlib
import os
import pathlib
import shutil
import sqlite3
import tempfile
import typing
from typing import Any, Dict, List
import urllib.parse

import psycopg2
from psycopg2 import extras
import sqlalchemy_adapter

from sky import global_user_state
from sky import skypilot_config
from sky.jobs import state as jobs_state
from sky.recipes import db as recipes_db
from sky.serve import serve_state
from sky.skylet import constants
from sky.utils import config_utils
from sky.utils import yaml_utils
from sky.utils.db import db_utils
from sky.utils.db import kv_cache

if typing.TYPE_CHECKING:
    import sqlalchemy

# SQLite databases of the API server, relative to ~/.sky.
STORES = ('state.db', 'spot_jobs.db', 'serve/services.db', 'recipes.db',
          'kv_cache.db')
_RECIPE_COLUMNS = ('name', 'description', 'content', 'recipe_type', 'pinned',
                   'user_id', 'user_name', 'is_editable', 'is_pinnable')
_BATCH_SIZE = 1000


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _readonly(path: pathlib.Path) -> sqlite3.Connection:
    return sqlite3.connect(f'file:{urllib.parse.quote(str(path))}?mode=ro',
                           uri=True)


def default_recipes() -> List[Dict[str, Any]]:
    """Returns the rows SkyPilot seeds into an empty recipes table."""
    # pylint: disable=protected-access
    return [
        dict(name=meta['name'],
             description=meta['description'],
             content=recipes_db._load_example_content(filename),
             recipe_type=meta['recipe_type'],
             pinned=1,
             user_id='system',
             user_name='SkyPilot',
             is_editable=0,
             is_pinnable=1)
        for filename, meta in recipes_db.DEFAULT_TEMPLATES.items()
    ]


def remove_default_recipes(connection: Any, placeholder: str,
                           expected: List[Dict[str, Any]]) -> List[str]:
    """Deletes the seeded default recipes, which the copy brings over again.

    Args:
        connection: A DB-API connection to the target database.
        placeholder: The paramstyle placeholder of the connection's driver.
        expected: The seeded rows, as returned by default_recipes().

    Returns:
        The names of the deleted recipes.

    Raises:
        ValueError: If the table holds anything other than the seeded rows.
    """
    cursor = connection.cursor()
    cursor.execute(f'SELECT {", ".join(_RECIPE_COLUMNS)} FROM recipes')
    # PostgreSQL returns the 0/1 flags as booleans.
    actual = [
        dict(
            zip(_RECIPE_COLUMNS, (int(v) if isinstance(v, bool) else v
                                  for v in row)))
        for row in cursor.fetchall()
    ]
    names = sorted(row['name'] for row in expected)
    if sorted(actual, key=lambda row: row['name']) != sorted(
            expected, key=lambda row: row['name']):
        raise ValueError('The recipes table in the target database differs '
                         'from the default recipes.')
    cursor.execute(
        f'DELETE FROM recipes WHERE name IN '
        f'({", ".join([placeholder] * len(names))})', names)
    connection.commit()
    return names


def require_postgres(engine: 'sqlalchemy.engine.Engine') -> None:
    """Raises ValueError unless the engine is backed by PostgreSQL."""
    if engine.dialect.name != db_utils.SQLAlchemyDialect.POSTGRESQL.value:
        raise ValueError(f'Schema initialization used {engine.dialect.name}, '
                         'not PostgreSQL.')


def init_target() -> List[str]:
    """Creates the schema in the target and removes the seeded recipes."""
    # pylint: disable=protected-access
    for module in (global_user_state, jobs_state, serve_state, recipes_db,
                   kv_cache, skypilot_config):
        require_postgres(module._db_manager.get_engine())
    # The RBAC policy tables, created by the API server on its first start.
    engine = global_user_state._db_manager.get_engine()
    db_utils.add_all_tables_to_db_sqlalchemy(sqlalchemy_adapter.Base.metadata,
                                             engine)
    connection = recipes_db._db_manager.get_engine().raw_connection()
    try:
        return remove_default_recipes(connection, '%s', default_recipes())
    finally:
        connection.close()


def freeze(sky_dir: pathlib.Path, frozen_dir: pathlib.Path) -> None:
    """Copies each SQLite database through a read-only connection.

    The copies include changes still in the write-ahead log, have no journal
    and are made read-only.
    """
    for relative in STORES:
        source, destination = sky_dir / relative, frozen_dir / relative
        if not source.is_file():
            raise FileNotFoundError(f'SQLite database not found: {source}')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(_readonly(source)) as reader:
            with contextlib.closing(sqlite3.connect(destination)) as writer:
                reader.backup(writer)
                writer.execute('PRAGMA journal_mode=DELETE')
        destination.chmod(0o444)


def source_tables(sky_dir: pathlib.Path) -> Dict[str, pathlib.Path]:
    """Maps every table to copy to the SQLite database that holds it."""
    tables: Dict[str, pathlib.Path] = {}
    for relative in STORES:
        with contextlib.closing(_readonly(sky_dir / relative)) as source:
            for (name,) in source.execute(
                    'SELECT name FROM sqlite_master WHERE type = \'table\''):
                # Alembic version tables are written by init_target().
                if name.startswith(('sqlite_', 'alembic_version')):
                    continue
                if name in tables:
                    raise ValueError(f'Table {name} exists in two databases.')
                tables[name] = sky_dir / relative
    return tables


def require_empty_target(dsn: str, tables: List[str]) -> None:
    """Raises ValueError if any of the tables in the target has rows."""
    with contextlib.closing(psycopg2.connect(dsn)) as target:
        with target.cursor() as cursor:
            for table in ['config_yaml', *tables]:
                cursor.execute('SELECT to_regclass(%s) IS NOT NULL',
                               (_quote(table),))
                if not cursor.fetchone()[0]:
                    continue
                cursor.execute(f'SELECT 1 FROM {_quote(table)} LIMIT 1')
                if cursor.fetchone():
                    raise ValueError(
                        f'Table {table} in the target database is not empty. '
                        'Migrate into a new, empty database.')


def _target_columns(cursor: Any, table: str) -> Dict[str, str]:
    cursor.execute(
        'SELECT column_name, data_type FROM information_schema.columns '
        'WHERE table_schema = current_schema() AND table_name = %s', (table,))
    return dict(cursor.fetchall())


def _fk_order(cursor: Any, tables: List[str]) -> List[str]:
    """Orders tables parents first, so foreign keys hold after each insert."""
    cursor.execute('SELECT conrelid::regclass::text, confrelid::regclass::text '
                   'FROM pg_constraint WHERE contype = \'f\' '
                   'AND connamespace = current_schema()::regnamespace')
    parents: Dict[str, set] = {table: set() for table in tables}
    for child, parent in cursor.fetchall():
        child, parent = child.strip('"'), parent.strip('"')
        if child in parents and parent in parents and child != parent:
            parents[child].add(parent)
    ordered: List[str] = []
    while parents:
        ready = sorted(t for t, p in parents.items() if not p - set(ordered))
        if not ready:
            raise ValueError(f'Foreign-key cycle among {sorted(parents)}.')
        ordered += ready
        for table in ready:
            del parents[table]
    return ordered


def _convert(value: Any, data_type: str) -> Any:
    # SQLite stores booleans as 0/1.
    if value is not None and data_type == 'boolean':
        return bool(value)
    return value


def copy_tables(frozen_dir: pathlib.Path, dsn: str) -> Dict[str, int]:
    """Copies every table in one transaction and advances the sequences.

    Returns:
        The number of rows copied per table.
    """
    sources = source_tables(frozen_dir)
    rows: Dict[str, int] = {}
    with contextlib.closing(psycopg2.connect(dsn)) as target:
        with target, target.cursor() as cursor:
            # SkyPilot writes naive timestamps in UTC.
            cursor.execute('SET TIME ZONE \'UTC\'')
            for table in _fk_order(cursor, list(sources)):
                with contextlib.closing(_readonly(sources[table])) as source:
                    rows[table] = _copy_table(source, cursor, table)
    return rows


def _copy_table(source: sqlite3.Connection, cursor: Any, table: str) -> int:
    target_columns = _target_columns(cursor, table)
    if not target_columns:
        raise ValueError(f'Table {table} does not exist in PostgreSQL.')
    columns = [
        row[1] for row in source.execute(f'PRAGMA table_info({_quote(table)})')
    ]
    missing = set(columns) - set(target_columns)
    if missing:
        raise ValueError(
            f'Columns of {table} missing in PostgreSQL: {sorted(missing)}')
    types = [target_columns[column] for column in columns]
    column_list = ', '.join(map(_quote, columns))
    result = source.execute(f'SELECT {column_list} FROM {_quote(table)}')
    while True:
        batch = result.fetchmany(_BATCH_SIZE)
        if not batch:
            break
        extras.execute_values(
            cursor, f'INSERT INTO {_quote(table)} ({column_list}) VALUES %s',
            [tuple(map(_convert, row, types)) for row in batch])
    count = source.execute(
        f'SELECT count(*) FROM {_quote(table)}').fetchone()[0]
    cursor.execute(f'SELECT count(*) FROM {_quote(table)}')
    if cursor.fetchone()[0] != count:
        raise ValueError(f'Row count of {table} differs after the copy.')
    for column in columns:
        cursor.execute('SELECT pg_get_serial_sequence(%s, %s)',
                       (_quote(table), column))
        sequence = cursor.fetchone()[0]
        if sequence is None:
            continue
        max_query = (f'SELECT COALESCE(MAX({_quote(column)}), 0) '
                     f'FROM {_quote(table)}')
        expected = source.execute(max_query).fetchone()[0]
        cursor.execute(max_query)
        if cursor.fetchone()[0] != expected:
            raise ValueError(
                f'Largest {column} of {table} differs after the copy.')
        cursor.execute('SELECT setval(%s, %s, false)', (sequence, expected + 1))
    return count


def seed_config(dsn: str, config: Dict[str, Any]) -> None:
    """Stores the API server config in the target and verifies it."""
    skypilot_config.update_api_server_config_no_lock(
        config_utils.Config(config))
    with contextlib.closing(psycopg2.connect(dsn)) as target:
        with target.cursor() as cursor:
            cursor.execute('SELECT value FROM config_yaml WHERE key = %s',
                           (skypilot_config.API_SERVER_CONFIG_KEY,))
            row = cursor.fetchone()
    if row is None or yaml_utils.safe_load(row[0]) != config:
        raise ValueError('The stored API server config differs from '
                         'the config to migrate.')


def migrate(sky_dir: pathlib.Path, config: Dict[str, Any],
            dsn: str) -> Dict[str, Any]:
    """Migrates the API server state in sky_dir to the database at dsn."""
    require_empty_target(dsn, list(source_tables(sky_dir)))
    frozen_dir = pathlib.Path(tempfile.mkdtemp())
    try:
        freeze(sky_dir, frozen_dir)
        removed = init_target()
        rows = copy_tables(frozen_dir, dsn)
    finally:
        shutil.rmtree(frozen_dir)
    if config:
        seed_config(dsn, config)
    return {'removed_default_recipes': removed, 'rows': rows}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sky-dir',
                        type=pathlib.Path,
                        default=pathlib.Path('~/.sky').expanduser(),
                        help='The API server\'s SkyPilot state directory.')
    parser.add_argument('--config',
                        type=pathlib.Path,
                        default=None,
                        help='The API server config to store in the database. '
                        'Defaults to config.yaml in --sky-dir.')
    args = parser.parse_args()
    dsn = os.environ.get(constants.ENV_VAR_DB_CONNECTION_URI)
    if not dsn:
        parser.error(f'{constants.ENV_VAR_DB_CONNECTION_URI} is not set.')
    config_path = args.config or args.sky_dir / 'config.yaml'
    config = yaml_utils.safe_load(config_path.read_text(encoding='utf-8')) or {}
    # SkyPilot only uses the database URI when running as the API server.
    # The API server merges its config file into the config it stores, so
    # point it at an empty one rather than at the config being migrated.
    empty_config = tempfile.NamedTemporaryFile(suffix='.yaml', delete=False)
    empty_config.close()
    os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = '1'
    os.environ[skypilot_config.ENV_VAR_GLOBAL_CONFIG] = empty_config.name
    try:
        result = migrate(args.sky_dir, config, dsn)
    finally:
        os.unlink(empty_config.name)
    for table, count in sorted(result['rows'].items()):
        print(f'{table}: {count} rows')
    print(f'Removed default recipes before the copy: '
          f'{", ".join(result["removed_default_recipes"]) or "none"}')
    print('Migration complete.')


if __name__ == '__main__':
    main()
