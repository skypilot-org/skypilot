"""Persistent KV cache, backed by a sqlite or postgres database."""
import functools
import threading
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

_SQLALCHEMY_ENGINE: Optional[sqlalchemy.engine.Engine] = None
_SQLALCHEMY_ENGINE_LOCK = threading.Lock()

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


# We wrap the sqlalchemy engine initialization in a thread
# lock to ensure that multiple threads do not initialize the
# engine which could result in a rare race condition where
# a session has already been created with _SQLALCHEMY_ENGINE = e1,
# and then another thread overwrites _SQLALCHEMY_ENGINE = e2
# which could result in e1 being garbage collected unexpectedly.
def initialize_and_get_db() -> sqlalchemy.engine.Engine:
    global _SQLALCHEMY_ENGINE

    if _SQLALCHEMY_ENGINE is not None:
        return _SQLALCHEMY_ENGINE
    with _SQLALCHEMY_ENGINE_LOCK:
        if _SQLALCHEMY_ENGINE is not None:
            return _SQLALCHEMY_ENGINE
        # get an engine to the db
        engine = db_utils.get_engine('kv_cache')

        # run migrations if needed
        create_table(engine)
        _SQLALCHEMY_ENGINE = engine
        return _SQLALCHEMY_ENGINE


def _init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        initialize_and_get_db()
        return func(*args, **kwargs)

    return wrapper


initialize_and_get_db()


@_init_db
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
    assert _SQLALCHEMY_ENGINE is not None
    if (_SQLALCHEMY_ENGINE.dialect.name ==
            db_utils.SQLAlchemyDialect.SQLITE.value):
        insert_func = sqlite.insert
    elif (_SQLALCHEMY_ENGINE.dialect.name ==
          db_utils.SQLAlchemyDialect.POSTGRESQL.value):
        insert_func = postgresql.insert
    else:
        raise ValueError('Unsupported database dialect')

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
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


@_init_db
@metrics_lib.time_me
def get_cache_entry(key: str) -> Optional[str]:
    """Get the value of the cache entry.

    Args:
        key: The key of the cache entry.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(kv_cache_table.c.value).where(
                kv_cache_table.c.key == key).where(
                    kv_cache_table.c.expires_at > time.time()))
        return result.scalar()
