"""The database for ssh node pool information."""
import functools
import os
import pathlib
import threading
import typing
from typing import Optional
import uuid

import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import orm

from sky import sky_logging
from sky.ssh_node_pools import constants
from sky.ssh_node_pools import models
from sky.utils import common_utils
from sky.utils import db_utils

# TODO(kyuds): we need to support Postgres in the future.
# currently, only using sqlite3

logger = sky_logging.init_logger(__name__)

_SQLALCHEMY_ENGINE: Optional[sqlalchemy.engine.Engine] = None
_DB_INIT_LOCK = threading.Lock()

_Metadata = sqlalchemy.MetaData()

ssh_pool_table = sqlalchemy.Table(
    'pools',
    _Metadata,
    sqlalchemy.Column('name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('alias', sqlalchemy.Text),
    sqlalchemy.Column('num_nodes', sqlalchemy.Integer, nullable=False),
    sqlalchemy.Column('head_node_name', sqlalchemy.Text, nullable=False),
    sqlalchemy.Column('head_node_id', sqlalchemy.String(36), nullable=False),
    sqlalchemy.Column('default_user', sqlalchemy.Text),
    sqlalchemy.Column('default_identity_file', sqlalchemy.Text),
    sqlalchemy.Column('default_password', sqlalchemy.Text),
)

ssh_node_table = sqlalchemy.Table(
    'nodes',
    _Metadata,
    sqlalchemy.Column('id', 
                      sqlalchemy.String(36), 
                      primary_key=True, 
                      default=lambda: str(uuid.uuid4())),
    sqlalchemy.Column('ip', sqlalchemy.Text, nullable=False),
    sqlalchemy.Column('user', sqlalchemy.Text),
    sqlalchemy.Column('identity_file', sqlalchemy.Text),
    sqlalchemy.Column('password', sqlalchemy.Text),
    sqlalchemy.Column('use_ssh_config', sqlalchemy.Boolean),
    sqlalchemy.Column('pool_name', 
                      sqlalchemy.Text, 
                      sqlalchemy.ForeignKey('pools.name'), 
                      nullable=False)
)

mapper_registry = orm.registry(metadata=_Metadata)

mapper_registry.map_imperatively(
    models.SSHPool,
    ssh_pool_table,
    properties={
        'nodes': orm.relationship(
            models.SSHNode,
            backref='pool',
            cascade="all, delete-orphan",
            order_by=ssh_node_table.c.id,
            foreign_keys=[ssh_node_table.c.pool_name])
    }
)

def _create_table():
    # refer to `global_user_state.py` create_table()
    if _SQLALCHEMY_ENGINE is None:
        raise ValueError('ssh node pool sqlalchemy engine is None')

    if (_SQLALCHEMY_ENGINE.dialect.name
            == db_utils.SQLAlchemyDialect.SQLITE.value and
            not common_utils.is_wsl()):
        try:
            with orm.Session(_SQLALCHEMY_ENGINE) as session:
                session.execute(sqlalchemy.text('PRAGMA journal_mode=WAL'))
                session.commit()
        except sqlalchemy_exc.OperationalError as e:
            if 'database is locked' not in str(e):
                raise

    # create table if they don't exist
    _Metadata.create_all(bind=_SQLALCHEMY_ENGINE)


def _initialize_and_get_db() -> sqlalchemy.engine.Engine:
    global _SQLALCHEMY_ENGINE
    if _SQLALCHEMY_ENGINE is not None:
        return _SQLALCHEMY_ENGINE

    with _DB_INIT_LOCK:
        if _SQLALCHEMY_ENGINE is None:
            db_path = os.path.expanduser(constants.SKYSSH_DB_PATH)
            pathlib.Path(db_path).parents[0].mkdir(parents=True, exist_ok=True)
            _SQLALCHEMY_ENGINE = sqlalchemy.create_engine('sqlite:///' + db_path)
            _create_table()

    return _SQLALCHEMY_ENGINE


def _init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _initialize_and_get_db()
        return func(*args, **kwargs)

    return wrapper
