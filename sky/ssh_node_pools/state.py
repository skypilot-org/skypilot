"""The database for ssh node pool information."""
from abc import ABC
from abc import ABCMeta
from abc import abstractmethod
import functools
import os
from pathlib import Path
import threading
from typing import List, Optional, Type, TypeVar

import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import orm
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.declarative import DeclarativeMeta
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


class _DeclarativeABCMeta(DeclarativeMeta, ABCMeta):
    pass


_Base = declarative_base(metaclass=_DeclarativeABCMeta)
_T = TypeVar('_T', bound='_BaseOrm')


class _BaseOrm(ABC):
    """Base class for Cluster ORM
    Used for type hinting.
    """

    @abstractmethod
    def to_model(self) -> models.SSHCluster:
        pass

    @classmethod
    @abstractmethod
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
    """ORM for SSH Cluster History

    Stores information of SSH Node Pools that are currently active."""
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


class _IpRegistry(_Base):  # type: ignore[valid-type, misc]
    """Ips currently being used by SSH Clusters"""
    __tablename__ = 'ip_registry'

    ip = sqlalchemy.Column(sqlalchemy.Text, primary_key=True)
    cluster_name = sqlalchemy.Column(sqlalchemy.Text, nullable=False)


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
            Path(db_path).parents[0].mkdir(parents=True, exist_ok=True)
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


## ip registry ##

# IpRegistry is meant to be a quicker way of figuring out whether
# a specific IP is being used in an SSH cluster or not. This is only
# for actual SSH nodes that have been deployed using `sky ssh up`.
# Since a single node cannot be used for two ssh clusters, these methods
# help to quickly find out whether this is the case. Therefore, the
# underlying model class is transparent.

# TODO(kyuds): currently, we use unresolved IPs. We should use resolved IPs.

@_init_db
def add_ips_to_registry(cluster_name: str, ips: List[str]):
    """Add ips to cluster on IpRegistry"""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        existing_map = {
            row.ip: row.cluster_name 
            for row in (
                session.query(_IpRegistry)
                .filter(_IpRegistry.ip.in_(ips))
                .all())
        }

        conflicting_ips = [
            ip for ip, existing_cluster in existing_map.items()
            if existing_cluster != cluster_name
        ]
        if conflicting_ips:
            raise ValueError('The following ips are already '
                             f'in use: {conflicting_ips}')

        for ip in ips:
            if ip not in existing_map:
                session.add(_IpRegistry(ip=ip, cluster_name=cluster_name))
        session.commit()


@_init_db
def remove_ips_from_registry(cluster_name: str, ips: List[str]):
    """Remove ips from cluster on IpRegistry"""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(_IpRegistry).filter(
            _IpRegistry.cluster_name == cluster_name,
            _IpRegistry.ip.in_(ips)
        ).delete(synchronize_session=False)
        session.commit()


@_init_db
def query_ip_from_registry(ip: str) -> Optional[str]:
    """Return cluster name of which this ip is registered"""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(_IpRegistry).filter_by(ip=ip).first()
        if row is None:
            return None
        return row.cluster_name


@_init_db
def query_ips_from_registry(ips: List[str]) -> List[Optional[str]]:
    """Return cluster names of which ips are registered
    
    Ips not registered will return None.
    len(ips) == len(return_value)"""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(_IpRegistry).filter(_IpRegistry.ip.in_(ips)).all()
        ips_to_cluster = {row.ip : row.cluster_name for row in rows}
        return [ips_to_cluster.get(ip) for ip in ips]
