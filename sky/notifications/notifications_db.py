"""Persistent notifications, backed by a sqlite or postgres database."""
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

notifications_table = sqlalchemy.Table(
    'notifications',
    Base.metadata,
    sqlalchemy.Column('id',
                      sqlalchemy.Integer,
                      primary_key=True,
                      autoincrement=True),
    sqlalchemy.Column('timestamp', sqlalchemy.Float, nullable=False),
    sqlalchemy.Column('user_hash', sqlalchemy.String, nullable=True),
    sqlalchemy.Column('type', sqlalchemy.String, nullable=False),
    sqlalchemy.Column('title', sqlalchemy.String, nullable=False),
    sqlalchemy.Column('message', sqlalchemy.Text, nullable=False),
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
                                         migration_utils.NOTIFICATIONS_DB_NAME,
                                         migration_utils.NOTIFICATIONS_VERSION)


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
        engine = db_utils.get_engine('notifications')

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
def add_notification(
    user_hash: Optional[str],
    notification_type: str,
    title: str,
    message: str,
) -> None:
    """Insert a notification row with timestamp set to current time."""
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
        insert_stmt = insert_func(notifications_table).values(
            timestamp=time.time(),
            user_hash=user_hash,
            type=notification_type,
            title=title,
            message=message,
        )
        session.execute(insert_stmt)
        session.commit()
