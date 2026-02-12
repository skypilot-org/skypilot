"""State migration tool for SkyPilot API server.

This module provides functions to export and import SkyPilot API server state,
enabling migration between different hosts or database backends
(SQLite <-> PostgreSQL).

The four supported migration paths are:
  SQLite -> SQLite, SQLite -> PostgreSQL,
  PostgreSQL -> SQLite, PostgreSQL -> PostgreSQL.

Exported tarball structure::

    skypilot-migration/
    ├── metadata.json
    ├── databases/
    │   ├── state_db.json
    │   ├── spot_jobs_db.json
    │   ├── serve_db.json
    │   ├── sky_config_db.json
    │   └── recipes_db.json
    ├── ssh_keys/
    └── ssh_node_pools_info/
"""
import base64
import datetime
import json
import os
import pathlib
import shutil
import tarfile
import tempfile
import typing
from typing import Any, Dict, List, Optional, Tuple

import sqlalchemy
from sqlalchemy import orm

import sky
from sky.utils.db import db_utils
from sky.utils.db import migration_utils

if typing.TYPE_CHECKING:
    pass

# Prefix for base64-encoded binary values in JSON.
_BASE64_PREFIX = 'b64:'

# Batch size for inserting rows.
_INSERT_BATCH_SIZE = 1000

# Top-level directory name inside the tarball.
_TARBALL_DIR = 'skypilot-migration'


# ---------------------------------------------------------------------------
# Database configuration
# ---------------------------------------------------------------------------
class _DbConfig(typing.NamedTuple):
    """Configuration for one logical database."""
    db_name: str
    # Passed to db_utils.get_engine(); None means PostgreSQL-only.
    engine_db_name: Optional[str]
    alembic_section: str
    alembic_version: str
    # Callable that returns the Base.metadata for this database.
    # We use a callable to avoid import-time side effects.
    get_metadata: Any  # Callable[[], sqlalchemy.MetaData]
    # If True, this database only exists when using PostgreSQL.
    postgresql_only: bool


def _get_db_configs() -> List[_DbConfig]:
    """Return database configurations, importing modules lazily."""
    # Imports are deferred to avoid heavy import-time costs and circular
    # dependencies when this module is loaded.
    # pylint: disable=import-outside-toplevel
    from sky import global_user_state
    from sky import skypilot_config
    from sky.jobs import state as jobs_state
    from sky.recipes import db as recipes_db
    from sky.serve import serve_state

    # pylint: enable=import-outside-toplevel

    return [
        _DbConfig(
            db_name='state_db',
            engine_db_name='state',
            alembic_section='state_db',
            alembic_version=migration_utils.GLOBAL_USER_STATE_VERSION,
            get_metadata=lambda: global_user_state.Base.metadata,
            postgresql_only=False,
        ),
        _DbConfig(
            db_name='spot_jobs_db',
            engine_db_name='spot_jobs',
            alembic_section='spot_jobs_db',
            alembic_version=migration_utils.SPOT_JOBS_VERSION,
            get_metadata=lambda: jobs_state.Base.metadata,
            postgresql_only=False,
        ),
        _DbConfig(
            db_name='serve_db',
            engine_db_name='serve/services',
            alembic_section='serve_db',
            alembic_version=migration_utils.SERVE_VERSION,
            get_metadata=lambda: serve_state.Base.metadata,
            postgresql_only=False,
        ),
        _DbConfig(
            db_name='sky_config_db',
            engine_db_name=None,
            alembic_section='sky_config_db',
            alembic_version=migration_utils.SKYPILOT_CONFIG_VERSION,
            get_metadata=lambda: skypilot_config.Base.metadata,
            postgresql_only=True,
        ),
        _DbConfig(
            db_name='recipes_db',
            engine_db_name='recipes',
            alembic_section='recipes_db',
            alembic_version=migration_utils.RECIPES_VERSION,
            get_metadata=lambda: recipes_db.Base.metadata,
            postgresql_only=False,
        ),
    ]


# ---------------------------------------------------------------------------
# Helpers — database backend detection
# ---------------------------------------------------------------------------
def _is_postgresql() -> bool:
    """Return True when the current backend is PostgreSQL."""
    from sky.skylet import constants  # pylint: disable=import-outside-toplevel
    if os.environ.get(constants.ENV_VAR_IS_SKYPILOT_SERVER) is not None:
        conn_string = os.environ.get(constants.ENV_VAR_DB_CONNECTION_URI)
        if conn_string:
            return True
    return False


def _get_engine_for_db(
    engine_db_name: Optional[str],
    postgresql_only: bool,
) -> Optional[sqlalchemy.engine.Engine]:
    """Get engine for a database, or ``None`` if not applicable."""
    is_pg = _is_postgresql()
    if postgresql_only and not is_pg:
        return None
    if not is_pg and engine_db_name is None:
        return None
    return db_utils.get_engine(engine_db_name)


# ---------------------------------------------------------------------------
# Helpers — (de-)serialization
# ---------------------------------------------------------------------------
def _serialize_value(value: Any, col_type: sqlalchemy.types.TypeEngine) -> Any:
    """Serialize a single value based on its SQLAlchemy column type."""
    if value is None:
        return None

    if isinstance(col_type, sqlalchemy.LargeBinary):
        if isinstance(value, (bytes, bytearray)):
            return _BASE64_PREFIX + base64.b64encode(value).decode('ascii')
        return value

    if isinstance(col_type, sqlalchemy.DateTime):
        if isinstance(value, datetime.datetime):
            return value.isoformat()
        return str(value)

    if isinstance(col_type, sqlalchemy.JSON):
        # Keep as native JSON — don't double-encode.
        return value

    if isinstance(col_type, sqlalchemy.Boolean):
        return bool(value)

    # Text, Integer, Float — return as-is.
    return value


def _deserialize_value(value: Any,
                       col_type: sqlalchemy.types.TypeEngine) -> Any:
    """Deserialize a single value based on its SQLAlchemy column type."""
    if value is None:
        return None

    if isinstance(col_type, sqlalchemy.LargeBinary):
        if isinstance(value, str) and value.startswith(_BASE64_PREFIX):
            return base64.b64decode(value[len(_BASE64_PREFIX):])
        return value

    if isinstance(col_type, sqlalchemy.DateTime):
        if isinstance(value, str):
            return datetime.datetime.fromisoformat(value)
        return value

    if isinstance(col_type, sqlalchemy.JSON):
        return value

    if isinstance(col_type, sqlalchemy.Boolean):
        return bool(value)

    return value


def _serialize_row(row: Any, table: sqlalchemy.Table) -> Dict[str, Any]:
    """Serialize a database row to a JSON-compatible dict."""
    mapping = row._mapping  # pylint: disable=protected-access
    return {
        col.name: _serialize_value(mapping.get(col.name), col.type)
        for col in table.columns
    }


def _deserialize_row(data: Dict[str, Any],
                     table: sqlalchemy.Table) -> Dict[str, Any]:
    """Deserialize a JSON dict back to database-compatible values."""
    return {
        col.name: _deserialize_value(data[col.name], col.type)
        for col in table.columns
        if col.name in data
    }


def _get_column_info(table: sqlalchemy.Table) -> List[Dict[str, Any]]:
    """Return column metadata for a table."""
    columns: List[Dict[str, Any]] = []
    for col in table.columns:
        info: Dict[str, Any] = {
            'name': col.name,
            'type': type(col.type).__name__,
        }
        if col.primary_key:
            info['primary_key'] = True
        if col.autoincrement is True:
            info['autoincrement'] = True
        columns.append(info)
    return columns


# ---------------------------------------------------------------------------
# Helpers — Alembic version
# ---------------------------------------------------------------------------
def _get_alembic_version(engine: sqlalchemy.engine.Engine,
                         alembic_section: str) -> Optional[str]:
    """Read the current Alembic version from the database."""
    config = migration_utils.get_alembic_config(engine, alembic_section)
    version_table = config.get_section_option(config.config_ini_section,
                                              'version_table',
                                              'alembic_version')
    try:
        with engine.connect() as conn:
            result = conn.execute(
                sqlalchemy.text(f'SELECT version_num FROM {version_table}'))
            row = result.fetchone()
            if row:
                return row[0]
    except (sqlalchemy.exc.OperationalError, sqlalchemy.exc.ProgrammingError):
        pass
    return None


# ---------------------------------------------------------------------------
# Helpers — PostgreSQL sequences
# ---------------------------------------------------------------------------
def _reset_postgres_sequences(engine: sqlalchemy.engine.Engine,
                              table: sqlalchemy.Table) -> None:
    """Reset PostgreSQL autoincrement sequences after data import."""
    if engine.dialect.name != db_utils.SQLAlchemyDialect.POSTGRESQL.value:
        return

    for col in table.columns:
        if (col.autoincrement is True and
                isinstance(col.type, sqlalchemy.Integer)):
            seq_name = f'{table.name}_{col.name}_seq'
            with engine.connect() as conn:
                # Check whether the sequence exists.
                result = conn.execute(
                    sqlalchemy.text('SELECT 1 FROM pg_sequences '
                                    'WHERE schemaname = \'public\' '
                                    'AND sequencename = :seq_name'),
                    {'seq_name': seq_name})
                if result.fetchone():
                    max_val = conn.execute(
                        sqlalchemy.text(f'SELECT COALESCE(MAX({col.name}), 0) '
                                        f'FROM {table.name}')).scalar()
                    conn.execute(
                        sqlalchemy.text(
                            f'SELECT setval(\'{seq_name}\', :max_val)'),
                        {'max_val': max(max_val, 1)})
                    conn.commit()


# ---------------------------------------------------------------------------
# Helpers — table import ordering
# ---------------------------------------------------------------------------
def _get_table_import_order(db_name: str, tables_data: Dict[str,
                                                            Any]) -> List[str]:
    """Return tables in a dependency-safe import order."""
    if db_name == 'spot_jobs_db':
        preferred = ['job_info', 'spot', 'ha_recovery_script', 'job_events']
        ordered = [t for t in preferred if t in tables_data]
        ordered += [t for t in tables_data if t not in preferred]
        return ordered

    if db_name == 'serve_db':
        preferred = [
            'services', 'replicas', 'version_specs', 'serve_ha_recovery_script'
        ]
        ordered = [t for t in preferred if t in tables_data]
        ordered += [t for t in tables_data if t not in preferred]
        return ordered

    return list(tables_data.keys())


# ---------------------------------------------------------------------------
# Helpers — tarball safety
# ---------------------------------------------------------------------------
def _safe_extract(tar: tarfile.TarFile, dest: str) -> None:
    """Extract *tar* into *dest*, rejecting path-traversal attempts."""
    dest = os.path.realpath(dest)
    for member in tar.getmembers():
        member_path = os.path.realpath(os.path.join(dest, member.name))
        if not member_path.startswith(dest + os.sep) and member_path != dest:
            raise ValueError(
                f'Path traversal detected in tarball: {member.name}')
    tar.extractall(dest)  # pylint: disable=consider-using-with


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def export_state(output_path: str) -> str:
    """Export all SkyPilot API server state to a tarball.

    Args:
        output_path: Desired path for the output ``.tar.gz`` file.

    Returns:
        The absolute path to the created tarball.
    """
    db_configs = _get_db_configs()
    is_pg = _is_postgresql()
    source_type = 'postgresql' if is_pg else 'sqlite'
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with tempfile.TemporaryDirectory() as tmpdir:
        migration_dir = os.path.join(tmpdir, _TARBALL_DIR)
        for subdir in ('databases', 'ssh_keys', 'ssh_node_pools_info'):
            os.makedirs(os.path.join(migration_dir, subdir))

        alembic_versions: Dict[str, Optional[str]] = {}
        tables_exported: Dict[str, Tuple[int, int]] = {}

        # ---- databases ----
        for cfg in db_configs:
            engine = _get_engine_for_db(cfg.engine_db_name, cfg.postgresql_only)
            if engine is None:
                print(f'  Skipping {cfg.db_name} '
                      f'(not applicable for {source_type})')
                continue

            print(f'  Exporting {cfg.db_name}...')
            base_metadata = cfg.get_metadata()

            current_version = _get_alembic_version(engine, cfg.alembic_section)
            alembic_versions[cfg.db_name] = current_version

            db_export: Dict[str, Any] = {
                'db_name': cfg.db_name,
                'alembic_version': current_version,
                'exported_at': timestamp,
                'tables': {},
            }

            table_count = 0
            row_count = 0

            for table_name, table in base_metadata.tables.items():
                try:
                    with orm.Session(engine) as session:
                        rows = session.execute(
                            sqlalchemy.select(table)).fetchall()

                    serialized_rows = [
                        _serialize_row(row, table) for row in rows
                    ]
                    db_export['tables'][table_name] = {
                        'columns': _get_column_info(table),
                        'rows': serialized_rows,
                    }
                    table_count += 1
                    row_count += len(serialized_rows)
                except (sqlalchemy.exc.OperationalError,
                        sqlalchemy.exc.ProgrammingError) as e:
                    print(f'    Warning: Could not export table '
                          f'{table_name}: {e}')

            db_json_path = os.path.join(migration_dir, 'databases',
                                        f'{cfg.db_name}.json')
            with open(db_json_path, 'w', encoding='utf-8') as f:
                json.dump(db_export, f, indent=2, default=str)

            tables_exported[cfg.db_name] = (table_count, row_count)
            print(f'    Exported {table_count} tables, {row_count} rows')

        # ---- SSH keys ----
        sky_dir = os.path.expanduser('~/.sky')
        ssh_keys_dir = os.path.join(sky_dir, 'ssh_keys')
        if os.path.isdir(ssh_keys_dir):
            for item in os.listdir(ssh_keys_dir):
                src = os.path.join(ssh_keys_dir, item)
                if os.path.isfile(src):
                    shutil.copy2(src,
                                 os.path.join(migration_dir, 'ssh_keys', item))
            print(f'  Copied SSH keys from {ssh_keys_dir}')

        # ---- SSH node pool info ----
        ssh_pools_info_dir = os.path.join(sky_dir, 'ssh_node_pools_info')
        if os.path.isdir(ssh_pools_info_dir):
            shutil.copytree(ssh_pools_info_dir,
                            os.path.join(migration_dir, 'ssh_node_pools_info'),
                            dirs_exist_ok=True)
            print('  Copied SSH node pool info')

        # ---- metadata.json ----
        metadata = {
            'skypilot_version': sky.__version__,
            'source_db_type': source_type,
            'exported_at': timestamp,
            'alembic_versions': alembic_versions,
            'tables_exported': {
                name: {
                    'tables': t,
                    'rows': r
                } for name, (t, r) in tables_exported.items()
            },
        }
        with open(os.path.join(migration_dir, 'metadata.json'),
                  'w',
                  encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)

        # ---- create tarball ----
        output_path = os.path.abspath(output_path)
        pathlib.Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output_path, 'w:gz') as tar:
            tar.add(migration_dir, arcname=_TARBALL_DIR)

    return output_path


def import_state(tarball_path: str, force: bool = False) -> None:
    """Import SkyPilot API server state from a tarball.

    Args:
        tarball_path: Path to the ``.tar.gz`` tarball produced by
            :func:`export_state`.
        force: If ``True``, truncate existing data in the target tables
            before importing.  If ``False`` (the default), the import is
            aborted when any target table already contains data.

    Raises:
        FileNotFoundError: If *tarball_path* does not exist.
        ValueError: If the tarball has an invalid format.
        RuntimeError: If the target contains data and *force* is ``False``.
    """
    if not os.path.exists(tarball_path):
        raise FileNotFoundError(f'Tarball not found: {tarball_path}')

    db_configs = _get_db_configs()
    is_pg = _is_postgresql()
    target_type = 'postgresql' if is_pg else 'sqlite'

    with tempfile.TemporaryDirectory() as tmpdir:
        # Extract tarball.
        print(f'Extracting {tarball_path}...')
        with tarfile.open(tarball_path, 'r:gz') as tar:
            _safe_extract(tar, tmpdir)

        migration_dir = os.path.join(tmpdir, _TARBALL_DIR)
        if not os.path.isdir(migration_dir):
            raise ValueError(
                f'Invalid tarball: missing {_TARBALL_DIR} directory')

        # Read metadata.
        metadata_path = os.path.join(migration_dir, 'metadata.json')
        if not os.path.exists(metadata_path):
            raise ValueError('Invalid tarball: missing metadata.json')

        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)

        source_version = metadata.get('skypilot_version', 'unknown')
        source_type = metadata.get('source_db_type', 'unknown')

        print(f'  Source: SkyPilot {source_version} ({source_type})')
        print(f'  Target: SkyPilot {sky.__version__} ({target_type})')
        print(f'  Exported at: {metadata.get("exported_at", "unknown")}')

        # ---- databases ----
        total_tables = 0
        total_rows = 0

        for cfg in db_configs:
            db_json_path = os.path.join(migration_dir, 'databases',
                                        f'{cfg.db_name}.json')
            if not os.path.exists(db_json_path):
                print(f'  Skipping {cfg.db_name} (not in tarball)')
                continue

            engine = _get_engine_for_db(cfg.engine_db_name, cfg.postgresql_only)
            if engine is None:
                print(f'  Skipping {cfg.db_name} '
                      f'(not applicable for {target_type})')
                continue

            print(f'  Importing {cfg.db_name}...')

            # Ensure schema is at the correct version.
            migration_utils.safe_alembic_upgrade(engine, cfg.alembic_section,
                                                 cfg.alembic_version)

            base_metadata = cfg.get_metadata()

            with open(db_json_path, 'r', encoding='utf-8') as f:
                db_export = json.load(f)

            tables_data = db_export.get('tables', {})

            # Non-empty check.
            if not force:
                non_empty: List[str] = []
                for table_name in tables_data:
                    if table_name not in base_metadata.tables:
                        continue
                    table = base_metadata.tables[table_name]
                    with orm.Session(engine) as session:
                        # pylint: disable=not-callable
                        count = session.execute(
                            sqlalchemy.select(
                                sqlalchemy.func.count()).select_from(
                                    table)).scalar()
                        if count and count > 0:
                            non_empty.append(f'{table_name} ({count} rows)')
                if non_empty:
                    raise RuntimeError(
                        f'Target database {cfg.db_name} has non-empty '
                        f'tables: {", ".join(non_empty)}. '
                        f'Use --force to truncate before import.')

            # Import tables in dependency order.
            table_order = _get_table_import_order(cfg.db_name, tables_data)

            for table_name in table_order:
                if table_name not in base_metadata.tables:
                    print(f'    Warning: Table {table_name} not found in '
                          f'current schema, skipping')
                    continue

                table = base_metadata.tables[table_name]
                table_data = tables_data[table_name]
                rows = table_data.get('rows', [])

                if not rows:
                    continue

                with orm.Session(engine) as session:
                    if force:
                        session.execute(table.delete())
                        session.commit()

                    deserialized = [_deserialize_row(r, table) for r in rows]
                    for i in range(0, len(deserialized), _INSERT_BATCH_SIZE):
                        batch = deserialized[i:i + _INSERT_BATCH_SIZE]
                        session.execute(table.insert(), batch)
                    session.commit()

                _reset_postgres_sequences(engine, table)

                total_tables += 1
                total_rows += len(rows)
                print(f'    Imported {table_name}: {len(rows)} rows')

        # ---- SSH keys ----
        sky_dir = os.path.expanduser('~/.sky')
        ssh_keys_src = os.path.join(migration_dir, 'ssh_keys')
        ssh_keys_dst = os.path.join(sky_dir, 'ssh_keys')
        if os.path.isdir(ssh_keys_src) and os.listdir(ssh_keys_src):
            os.makedirs(ssh_keys_dst, exist_ok=True)
            for item in os.listdir(ssh_keys_src):
                src = os.path.join(ssh_keys_src, item)
                dst = os.path.join(ssh_keys_dst, item)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                    os.chmod(dst, 0o600)
            print('    Restored SSH keys')

        # ---- SSH node pool info ----
        ssh_pools_src = os.path.join(migration_dir, 'ssh_node_pools_info')
        ssh_pools_dst = os.path.join(sky_dir, 'ssh_node_pools_info')
        if os.path.isdir(ssh_pools_src) and os.listdir(ssh_pools_src):
            os.makedirs(ssh_pools_dst, exist_ok=True)
            shutil.copytree(ssh_pools_src, ssh_pools_dst, dirs_exist_ok=True)
            print('    Restored SSH node pool info')

        print(f'\nImport complete: {total_tables} tables, {total_rows} rows')
