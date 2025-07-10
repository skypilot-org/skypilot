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
        return (session.query(
            models.SSHCluster).filter_by(name=cluster_name).first())


def get_one_or_all_clusters(
        cluster_name: Optional[str] = None) -> List[models.SSHCluster]:
    """Helper method. Get all clsuters or one cluster, depending
    on whether the cluster_name argument is passed."""
    if cluster_name is not None:
        found = get_cluster(cluster_name)
        return [found] if found is not None else []
    return get_all_clusters()


@_init_db
def get_cluster_by_status(
        status: models.SSHClusterStatus) -> List[models.SSHCluster]:
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


def update_cluster_status(cluster: models.SSHCluster,
                          status: models.SSHClusterStatus):
    """Update SSH Cluster Status"""
    if cluster.status != status:
        cluster.status = status
        add_or_update_cluster(cluster)


@_init_db
def remove_cluster(cluster_name: str):
    """Remove SSHCluster Configuration"""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(models.SSHCluster).filter_by(name=cluster_name).delete()
        session.commit()
