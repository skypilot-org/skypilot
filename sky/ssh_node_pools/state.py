"""The database for ssh node pool information."""
import functools
import os
from pathlib import Path
import threading
from typing import List, Optional

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


class _IpRegistry(models.SSHBase):  # type: ignore[valid-type, misc]
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
    models.SSHBase.metadata.create_all(bind=_SQLALCHEMY_ENGINE)


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
@_init_db
def get_all_clusters() -> List[models.SSHCluster]:
    """Get all SSHCluster Configurations"""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        return session.query(models.SSHCluster).all()


@_init_db
def get_cluster(cluster_name: str) -> Optional[models.SSHCluster]:
    """Get a SSHCluster Configuration by Cluster Name"""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        return (session.query(models.SSHCluster)
                       .filter_by(name=cluster_name).first())


@_init_db
def get_cluster_by_status(status: models.SSHClusterStatus) -> List[models.SSHCluster]:
    """Get SSHCluster Configurations by Status"""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        return session.query(models.SSHCluster).filter_by(status=status).all()


@_init_db
def add_or_update_cluster(cluster: models.SSHCluster):
    """Add or Update an Existing SSHCluster Configuration"""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.merge(cluster)
        session.commit()


@_init_db
def remove_cluster(cluster_name: str):
    """Remove SSHCluster Configuration"""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(models.SSHCluster).filter_by(name=cluster_name).delete()
        session.commit()


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
