"""The database for ssh node pool information."""
from abc import ABC
from abc import abstractmethod
import functools
import os
import pathlib
import threading
from typing import List, Optional, Type, TypeVar

import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import orm
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy_json import mutable_json_type

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

_Base = declarative_base()
_T = TypeVar('_T', bound='_BaseOrm')


class _BaseOrm(ABC):
    """Base class for Cluster ORM
    Used for type hinting.
    """

    @abstractmethod
    def to_model(self) -> models.SSHCluster:
        pass

    @abstractmethod
    @classmethod
    def from_model(cls: Type[_T], model: models.SSHCluster) -> _T:
        pass


# We have one table for staging (pools being "upped")
# and one pool for history management. Schema is identical.
class _SSHStagingOrm(_Base, _BaseOrm):  # type: ignore[valid-type, misc]
    """ORM for SSH Cluster in Staging"""
    __tablename__ = 'cluster_staging'

    name = sqlalchemy.Column(sqlalchemy.Text, primary_key=True)
    alias = sqlalchemy.Column(sqlalchemy.Text, nullable=True)
    head_node_ip = sqlalchemy.Column(sqlalchemy.Text, nullable=False)
    node_json = sqlalchemy.Column(
        mutable_json_type(dbtype=sqlalchemy.JSON, nested=True))

    def to_model(self) -> models.SSHCluster:
        return models.SSHCluster(name=self.name,
                                 head_node_ip=self.head_node_ip,
                                 alias=self.alias,
                                 _node_json=self.node_json)

    @classmethod
    def from_model(cls, model: models.SSHCluster) -> '_SSHStagingOrm':
        return cls(name=model.name,
                   head_node_ip=model.head_node_ip,
                   alias=model.alias,
                   node_json=model.node_json)


class _SSHHistoryOrm(_Base, _BaseOrm):  # type: ignore[valid-type, misc]
    """ORM for SSH Cluster History"""
    __tablename__ = 'cluster_history'

    name = sqlalchemy.Column(sqlalchemy.Text, primary_key=True)
    alias = sqlalchemy.Column(sqlalchemy.Text, nullable=True)
    head_node_ip = sqlalchemy.Column(sqlalchemy.Text, nullable=False)
    node_json = sqlalchemy.Column(
        mutable_json_type(dbtype=sqlalchemy.JSON, nested=True))

    def to_model(self) -> models.SSHCluster:
        return models.SSHCluster(name=self.name,
                                 head_node_ip=self.head_node_ip,
                                 alias=self.alias,
                                 _node_json=self.node_json)

    @classmethod
    def from_model(cls, model: models.SSHCluster) -> '_SSHHistoryOrm':
        return cls(name=model.name,
                   head_node_ip=model.head_node_ip,
                   alias=model.alias,
                   node_json=model.node_json)


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
    _Base.metadata.create_all(bind=_SQLALCHEMY_ENGINE)


def _initialize_and_get_db() -> sqlalchemy.engine.Engine:
    global _SQLALCHEMY_ENGINE
    if _SQLALCHEMY_ENGINE is not None:
        return _SQLALCHEMY_ENGINE

    with _DB_INIT_LOCK:
        if _SQLALCHEMY_ENGINE is None:
            db_path = os.path.expanduser(constants.SKYSSH_DB_PATH)
            pathlib.Path(db_path).parents[0].mkdir(parents=True, exist_ok=True)
            _SQLALCHEMY_ENGINE = sqlalchemy.create_engine('sqlite:///' +
                                                          db_path)
            _create_table()

    return _SQLALCHEMY_ENGINE


def _init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _initialize_and_get_db()
        return func(*args, **kwargs)

    return wrapper


## cluster db ##


# generics
@_init_db
def _get_all_clusters_generic(orm_class: Type[_T]) -> List[models.SSHCluster]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(orm_class).all()
        return [row.to_model() for row in rows]


@_init_db
def _get_cluster_generic(name: str,
                         orm_class: Type[_T]) -> Optional[models.SSHCluster]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(orm_class).filter_by(name=name).first()
        if row is None:
            return None
        return row.to_model()


@_init_db
def _add_or_update_cluster_generic(cluster: models.SSHCluster,
                                   orm_class: Type[_T]):
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        orm_cluster = orm_class.from_model(cluster)
        session.merge(orm_cluster)
        session.commit()


@_init_db
def _remove_cluster_generic(name: str, orm_class: Type[_T]):
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(orm_class).filter_by(name=name).delete()
        session.commit()


# staging / history
def get_all_clusters_staging() -> List[models.SSHCluster]:
    """Get all staged ssh clusters"""
    return _get_all_clusters_generic(_SSHStagingOrm)


def get_all_clusters_history() -> List[models.SSHCluster]:
    """Get all deployed ssh clusters"""
    return _get_all_clusters_generic(_SSHHistoryOrm)


def get_cluster_staging(name: str) -> Optional[models.SSHCluster]:
    """Get specific staged ssh cluster"""
    return _get_cluster_generic(name, _SSHStagingOrm)


def get_cluster_history(name: str) -> Optional[models.SSHCluster]:
    """Get specific ssh cluster history"""
    return _get_cluster_generic(name, _SSHHistoryOrm)


def add_or_update_cluster_staging(cluster: models.SSHCluster):
    """Upsert ssh cluster for staging"""
    _add_or_update_cluster_generic(cluster, _SSHStagingOrm)


def add_or_update_cluster_history(cluster: models.SSHCluster):
    """Upsert ssh cluster history"""
    _add_or_update_cluster_generic(cluster, _SSHHistoryOrm)


def remove_cluster_staging(name: str):
    """Remove ssh cluster from staging"""
    _remove_cluster_generic(name, _SSHStagingOrm)


def remove_cluster_history(name: str):
    """Remove ssh cluster history"""
    _remove_cluster_generic(name, _SSHHistoryOrm)
