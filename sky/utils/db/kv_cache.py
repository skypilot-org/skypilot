"""Persistent KV cache, backed by a sqlite or postgres database."""
import time
from typing import Optional

import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext import declarative

from sky import sky_logging
from sky.metrics import utils as metrics_lib
from sky.utils import common_utils
from sky.utils.db import db_utils
from sky.utils.db import migration_utils

logger = sky_logging.init_logger(__name__)

Base = declarative.declarative_base()

kv_cache_table = sqlalchemy.Table(
    'kv_cache',
    Base.metadata,
    sqlalchemy.Column('key', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('value', sqlalchemy.Text),
    sqlalchemy.Column('expires_at', sqlalchemy.Float),
)


def create_table(engine: sqlalchemy.engine.Engine):
    # Enable WAL mode to avoid locking issues.
    # See: issue #1441 and PR #1509
    # https://github.com/microsoft/WSL/issues/2395
    # TODO(romilb): We do not enable WAL for WSL because of known issue in WSL.
    #  This may cause the database locked problem from WSL issue #1441.
    if (engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value and
            not common_utils.is_wsl()):
        try:
            with orm.Session(engine) as session:
                session.execute(sqlalchemy.text('PRAGMA journal_mode=WAL'))
                session.commit()
        except sqlalchemy_exc.OperationalError as e:
            if 'database is locked' not in str(e):
                raise
            # If the database is locked, it is OK to continue, as the WAL mode
            # is not critical and is likely to be enabled by other processes.

    migration_utils.safe_alembic_upgrade(engine,
                                         migration_utils.KV_CACHE_DB_NAME,
                                         migration_utils.KV_CACHE_VERSION)


_db_manager = db_utils.DatabaseManager('kv_cache', create_table)


@metrics_lib.time_me
def add_or_update_cache_entry(
    key: str,
    value: str,
    expires_at: float,
) -> None:
    """Store the mapping from user hash to user name for display purposes.

    Args:
        key: The key of the cache entry.
        value: The value of the cache entry.
        expires_at: The timestamp when the cache entry expires.
    """
    engine = _db_manager.get_engine()
    if engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value:
        insert_func = sqlite.insert
    elif engine.dialect.name == db_utils.SQLAlchemyDialect.POSTGRESQL.value:
        insert_func = postgresql.insert
    else:
        raise ValueError('Unsupported database dialect')

    with orm.Session(engine) as session:
        insert_stmt = insert_func(kv_cache_table).values(key=key,
                                                         value=value,
                                                         expires_at=expires_at)
        do_update_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[kv_cache_table.c.key],
            set_={
                kv_cache_table.c.value: value,
                kv_cache_table.c.expires_at: expires_at
            })
        session.execute(do_update_stmt)

        session.commit()


@metrics_lib.time_me
def get_cache_entry(key: str) -> Optional[str]:
    """Get the value of the cache entry.

    Args:
        key: The key of the cache entry.
    """
    engine = _db_manager.get_engine()
    with orm.Session(engine) as session:
        result = session.execute(
            sqlalchemy.select(kv_cache_table.c.value).where(
                kv_cache_table.c.key == key).where(
                    kv_cache_table.c.expires_at > time.time()))
        return result.scalar()


_LIKE_ESCAPE_CHAR = '\\'


def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcard characters (%, _) in a literal value."""
    return (value.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2).replace(
        '%', f'{_LIKE_ESCAPE_CHAR}%').replace('_', f'{_LIKE_ESCAPE_CHAR}_'))


@metrics_lib.time_me
def delete_cache_entries_by_prefix(prefix: str) -> None:
    """Delete all cache entries whose key starts with the given prefix.

    Any SQL LIKE wildcards (%, _) in *prefix* are escaped so they are
    matched literally.

    Args:
        prefix: The literal prefix to match against cache keys.
    """
    escaped = _escape_like(prefix)
    engine = _db_manager.get_engine()
    with orm.Session(engine) as session:
        session.execute(
            sqlalchemy.delete(kv_cache_table).where(
                kv_cache_table.c.key.like(f'{escaped}%',
                                          escape=_LIKE_ESCAPE_CHAR)))
        session.commit()


@metrics_lib.time_me
def delete_cache_entries_by_prefix_suffix(prefix: str, suffix: str) -> None:
    """Delete all cache entries whose key starts with *prefix* and ends
    with *suffix*, with any content in between.

    Both *prefix* and *suffix* are treated as literal strings — any SQL
    LIKE wildcards (%, _) they contain are escaped automatically.

    Args:
        prefix: Literal prefix to match against cache keys.
        suffix: Literal suffix to match against cache keys.
    """
    escaped_prefix = _escape_like(prefix)
    escaped_suffix = _escape_like(suffix)
    pattern = f'{escaped_prefix}%{escaped_suffix}'
    engine = _db_manager.get_engine()
    with orm.Session(engine) as session:
        session.execute(
            sqlalchemy.delete(kv_cache_table).where(
                kv_cache_table.c.key.like(pattern, escape=_LIKE_ESCAPE_CHAR)))
        session.commit()
