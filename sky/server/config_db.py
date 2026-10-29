"""API server config YAML persistence in the shared DB.

Server / migration only. Client and CLI paths must not import this module at
import time -- ``skypilot_config`` lazy-imports it only when a DB URL is set.

Requires SQLAlchemy via ``skypilot[server]`` (or a cloud extra that includes
``server_dependencies``).
"""
from __future__ import annotations

import typing
from typing import Any, Optional

from sky.adaptors import common as adaptors_common
from sky.utils import yaml_utils
from sky.utils.db import db_utils
from sky.utils.db import migration_utils
from sky.utils.db import sqlalchemy_deps

_SA_MSG = sqlalchemy_deps.SQLALCHEMY_INSTALL_MSG
if typing.TYPE_CHECKING:
    import sqlalchemy
    from sqlalchemy import orm
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.engine import Engine
    from sqlalchemy.ext import declarative
else:
    sqlalchemy = adaptors_common.LazyImport('sqlalchemy',
                                            import_error_message=_SA_MSG)
    orm = adaptors_common.LazyImport('sqlalchemy.orm',
                                     import_error_message=_SA_MSG)
    postgresql = adaptors_common.LazyImport('sqlalchemy.dialects.postgresql',
                                            import_error_message=_SA_MSG)
    sqlite = adaptors_common.LazyImport('sqlalchemy.dialects.sqlite',
                                        import_error_message=_SA_MSG)
    declarative = adaptors_common.LazyImport('sqlalchemy.ext.declarative',
                                             import_error_message=_SA_MSG)

Base: Any = None
config_yaml_table: Any = None
_db_manager: Optional[db_utils.DatabaseManager] = None


def ensure_config_db_schema() -> None:
    """Initialize config ORM metadata and the DatabaseManager (idempotent).

    Safe to call from Alembic migrations and from API server config load/save.
    """
    global Base, config_yaml_table, _db_manager
    if _db_manager is not None:
        return
    sqlalchemy_deps.require_sqlalchemy()

    Base = declarative.declarative_base()
    config_yaml_table = sqlalchemy.Table(
        'config_yaml',
        Base.metadata,
        sqlalchemy.Column('key', sqlalchemy.Text, primary_key=True),
        sqlalchemy.Column('value', sqlalchemy.Text),
    )

    def _create_table(engine: 'Engine') -> None:
        """Initialize the config database with migrations."""
        migration_utils.safe_alembic_upgrade(
            engine, migration_utils.SKYPILOT_CONFIG_DB_NAME,
            migration_utils.SKYPILOT_CONFIG_VERSION)

    # Config YAML is only stored when using Postgres; no sqlite db_name.
    _db_manager = db_utils.DatabaseManager(db_name='config',
                                           create_table_fn=_create_table)


def get_engine() -> 'Engine':
    """Return the config DB engine after ensuring schema."""
    ensure_config_db_schema()
    assert _db_manager is not None
    return _db_manager.get_engine()


def get_config_yaml(key: str) -> Optional[dict]:
    """Load a YAML config document by key from the config DB."""
    ensure_config_db_schema()
    assert config_yaml_table is not None
    with orm.Session(get_engine()) as session:
        row = session.query(config_yaml_table).filter_by(key=key).first()
    if row:
        return yaml_utils.safe_load(row.value)
    return None


def set_config_yaml(key: str, config: dict) -> None:
    """Upsert a YAML config document by key into the config DB."""
    ensure_config_db_schema()
    assert config_yaml_table is not None
    engine = get_engine()
    # Persisting, not logging: this value is read back as the server config,
    # so it must be the real one. Do not route through dump_redacted_yaml().
    config_str = yaml_utils.dump_yaml_str(dict(config))
    with orm.Session(engine) as session:
        if engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value:
            insert_func = sqlite.insert
        elif engine.dialect.name == db_utils.SQLAlchemyDialect.POSTGRESQL.value:
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')
        insert_stmnt = insert_func(config_yaml_table).values(key=key,
                                                             value=config_str)
        do_update_stmt = insert_stmnt.on_conflict_do_update(
            index_elements=[config_yaml_table.c.key],
            set_={config_yaml_table.c.value: config_str})
        session.execute(do_update_stmt)
        session.commit()
