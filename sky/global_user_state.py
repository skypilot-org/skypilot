"""Global user state, backed by a sqlite database.

Concepts:
- Cluster name: a user-supplied or auto-generated unique name to identify a
  cluster.
- Cluster handle: (non-user facing) an opaque backend handle for us to
  interact with a cluster.
"""
import json
import os
import pathlib
import pickle
import re
import time
import typing
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext import declarative
import yaml

from sky import models
from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import context_utils
from sky.utils import db_utils
from sky.utils import registry
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import clouds
    from sky.clouds import cloud
    from sky.data import Storage

logger = sky_logging.init_logger(__name__)

_ENABLED_CLOUDS_KEY_PREFIX = 'enabled_clouds_'

_DB_PATH = os.path.expanduser('~/.sky/state.db')
pathlib.Path(_DB_PATH).parents[0].mkdir(parents=True, exist_ok=True)

if os.environ.get(constants.SKYPILOT_API_SERVER_DB_URL_ENV_VAR):
    # If SKYPILOT_API_SERVER_DB_URL_ENV_VAR is set, use it as the database URI.
    logger.debug(
        f'using db URI from {constants.SKYPILOT_API_SERVER_DB_URL_ENV_VAR}')
    _SQLALCHEMY_ENGINE = sqlalchemy.create_engine(
        os.environ.get(constants.SKYPILOT_API_SERVER_DB_URL_ENV_VAR))
else:
    _SQLALCHEMY_ENGINE = sqlalchemy.create_engine('sqlite:///' + _DB_PATH)

Base = declarative.declarative_base()

config_table = sqlalchemy.Table(
    'config',
    Base.metadata,
    sqlalchemy.Column('key', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('value', sqlalchemy.Text),
)

user_table = sqlalchemy.Table(
    'users',
    Base.metadata,
    sqlalchemy.Column('id', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('name', sqlalchemy.Text),
)

cluster_table = sqlalchemy.Table(
    'clusters',
    Base.metadata,
    sqlalchemy.Column('name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('launched_at', sqlalchemy.Integer),
    sqlalchemy.Column('handle', sqlalchemy.LargeBinary),
    sqlalchemy.Column('last_use', sqlalchemy.Text),
    sqlalchemy.Column('status', sqlalchemy.Text),
    sqlalchemy.Column('autostop', sqlalchemy.Integer, server_default='-1'),
    sqlalchemy.Column('to_down', sqlalchemy.Integer, server_default='0'),
    sqlalchemy.Column('metadata', sqlalchemy.Text, server_default='{}'),
    sqlalchemy.Column('owner', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('cluster_hash', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('storage_mounts_metadata',
                      sqlalchemy.LargeBinary,
                      server_default=None),
    sqlalchemy.Column('cluster_ever_up', sqlalchemy.Integer,
                      server_default='0'),
    sqlalchemy.Column('status_updated_at',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('config_hash', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('user_hash', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('workspace',
                      sqlalchemy.Text,
                      server_default=constants.SKYPILOT_DEFAULT_WORKSPACE),
    sqlalchemy.Column('last_creation_yaml',
                      sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('last_creation_command',
                      sqlalchemy.Text,
                      server_default=None),
)

storage_table = sqlalchemy.Table(
    'storage',
    Base.metadata,
    sqlalchemy.Column('name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('launched_at', sqlalchemy.Integer),
    sqlalchemy.Column('handle', sqlalchemy.LargeBinary),
    sqlalchemy.Column('last_use', sqlalchemy.Text),
    sqlalchemy.Column('status', sqlalchemy.Text),
)

# Table for Cluster History
# usage_intervals: List[Tuple[int, int]]
#  Specifies start and end timestamps of cluster.
#  When the last end time is None, the cluster is still UP.
#  Example: [(start1, end1), (start2, end2), (start3, None)]

# requested_resources: Set[resource_lib.Resource]
#  Requested resources fetched from task that user specifies.

# launched_resources: Optional[resources_lib.Resources]
#  Actual launched resources fetched from handle for cluster.

# num_nodes: Optional[int] number of nodes launched.
cluster_history_table = sqlalchemy.Table(
    'cluster_history',
    Base.metadata,
    sqlalchemy.Column('cluster_hash', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('name', sqlalchemy.Text),
    sqlalchemy.Column('num_nodes', sqlalchemy.Integer),
    sqlalchemy.Column('requested_resources', sqlalchemy.LargeBinary),
    sqlalchemy.Column('launched_resources', sqlalchemy.LargeBinary),
    sqlalchemy.Column('usage_intervals', sqlalchemy.LargeBinary),
    sqlalchemy.Column('user_hash', sqlalchemy.Text),
)

ssh_key_table = sqlalchemy.Table(
    'ssh_key',
    Base.metadata,
    sqlalchemy.Column('user_hash', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('ssh_public_key', sqlalchemy.Text),
    sqlalchemy.Column('ssh_private_key', sqlalchemy.Text),
)

cluster_yaml_table = sqlalchemy.Table(
    'cluster_yaml',
    Base.metadata,
    sqlalchemy.Column('cluster_name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('yaml', sqlalchemy.Text),
)


def _glob_to_similar(glob_pattern):
    """Converts a glob pattern to a PostgreSQL LIKE pattern."""

    # Escape special LIKE characters that are not special in glob
    glob_pattern = glob_pattern.replace('%', '\\%').replace('_', '\\_')

    # Convert glob wildcards to LIKE wildcards
    like_pattern = glob_pattern.replace('*', '%').replace('?', '_')

    # Handle character classes, including negation
    def replace_char_class(match):
        group = match.group(0)
        if group.startswith('[!'):
            return '[^' + group[2:-1] + ']'
        return group

    like_pattern = re.sub(r'\[(!)?.*?\]', replace_char_class, like_pattern)
    return like_pattern


def create_table():
    # Enable WAL mode to avoid locking issues.
    # See: issue #1441 and PR #1509
    # https://github.com/microsoft/WSL/issues/2395
    # TODO(romilb): We do not enable WAL for WSL because of known issue in WSL.
    #  This may cause the database locked problem from WSL issue #1441.
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
            # If the database is locked, it is OK to continue, as the WAL mode
            # is not critical and is likely to be enabled by other processes.

    # Create tables if they don't exist
    Base.metadata.create_all(bind=_SQLALCHEMY_ENGINE)

    # For backward compatibility.
    # TODO(zhwu): Remove this function after all users have migrated to
    # the latest version of SkyPilot.
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # Add autostop column to clusters table
        db_utils.add_column_to_table_sqlalchemy(session,
                                                'clusters',
                                                'autostop',
                                                sqlalchemy.Integer(),
                                                default_statement='DEFAULT -1')

        db_utils.add_column_to_table_sqlalchemy(
            session,
            'clusters',
            'metadata',
            sqlalchemy.Text(),
            default_statement='DEFAULT \'{}\'')

        db_utils.add_column_to_table_sqlalchemy(session,
                                                'clusters',
                                                'to_down',
                                                sqlalchemy.Integer(),
                                                default_statement='DEFAULT 0')

        # The cloud identity that created the cluster.
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'clusters',
            'owner',
            sqlalchemy.Text(),
            default_statement='DEFAULT NULL')

        db_utils.add_column_to_table_sqlalchemy(
            session,
            'clusters',
            'cluster_hash',
            sqlalchemy.Text(),
            default_statement='DEFAULT NULL')

        db_utils.add_column_to_table_sqlalchemy(
            session,
            'clusters',
            'storage_mounts_metadata',
            sqlalchemy.LargeBinary(),
            default_statement='DEFAULT NULL')
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'clusters',
            'cluster_ever_up',
            sqlalchemy.Integer(),
            default_statement='DEFAULT 0',
            # Set the value to 1 so that all the existing clusters before #2977
            # are considered as ever up, i.e:
            #   existing cluster's default (null) -> 1;
            #   new cluster's default -> 0;
            # This is conservative for the existing clusters: even if some INIT
            # clusters were never really UP, setting it to 1 means they won't be
            # auto-deleted during any failover.
            value_to_replace_existing_entries=1)
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'clusters',
            'status_updated_at',
            sqlalchemy.Integer(),
            default_statement='DEFAULT NULL')
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'clusters',
            'user_hasha',
            sqlalchemy.Text(),
            default_statement='DEFAULT NULL',
            value_to_replace_existing_entries=common_utils.get_user_hash())
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'clusters',
            'config_hash',
            sqlalchemy.Text(),
            default_statement='DEFAULT NULL')

        db_utils.add_column_to_table_sqlalchemy(
            session,
            'cluster_history',
            'user_hash',
            sqlalchemy.Text(),
            default_statement='DEFAULT NULL')

        db_utils.add_column_to_table_sqlalchemy(
            session,
            'clusters',
            'workspace',
            sqlalchemy.Text(),
            default_statement='DEFAULT \'default\'',
            value_to_replace_existing_entries=constants.
            SKYPILOT_DEFAULT_WORKSPACE)
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'clusters',
            'last_creation_yaml',
            sqlalchemy.Text(),
            default_statement='DEFAULT NULL',
        )
        db_utils.add_column_to_table_sqlalchemy(
            session,
            'clusters',
            'last_creation_command',
            sqlalchemy.Text(),
            default_statement='DEFAULT NULL')
        session.commit()


create_table()


def add_or_update_user(user: models.User):
    """Store the mapping from user hash to user name for display purposes."""
    if user.name is None:
        return

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')
        insert_stmnt = insert_func(user_table).values(id=user.id,
                                                      name=user.name)
        do_update_stmt = insert_stmnt.on_conflict_do_update(
            index_elements=[user_table.c.id],
            set_={user_table.c.name: user.name})
        session.execute(do_update_stmt)
        session.commit()


def get_user(user_id: str) -> models.User:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(user_table).filter_by(id=user_id).first()
    if row is None:
        return models.User(id=user_id)
    return models.User(id=row.id, name=row.name)


def get_all_users() -> List[models.User]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(user_table).all()
    return [models.User(id=row.id, name=row.name) for row in rows]


def add_or_update_cluster(cluster_name: str,
                          cluster_handle: 'backends.ResourceHandle',
                          requested_resources: Optional[Set[Any]],
                          ready: bool,
                          is_launch: bool = True,
                          config_hash: Optional[str] = None,
                          task_config: Optional[Dict[str, Any]] = None):
    """Adds or updates cluster_name -> cluster_handle mapping.

    Args:
        cluster_name: Name of the cluster.
        cluster_handle: backends.ResourceHandle of the cluster.
        requested_resources: Resources requested for cluster.
        ready: Whether the cluster is ready to use. If False, the cluster will
            be marked as INIT, otherwise it will be marked as UP.
        is_launch: if the cluster is firstly launched. If True, the launched_at
            and last_use will be updated. Otherwise, use the old value.
        config_hash: Configuration hash for the cluster.
        task_config: The config of the task being launched.
    """
    # TODO(zhwu): have to be imported here to avoid circular import.
    from sky import skypilot_config  # pylint: disable=import-outside-toplevel

    # FIXME: launched_at will be changed when `sky launch -c` is called.
    handle = pickle.dumps(cluster_handle)
    cluster_launched_at = int(time.time()) if is_launch else None
    last_use = common_utils.get_current_command() if is_launch else None
    status = status_lib.ClusterStatus.INIT
    if ready:
        status = status_lib.ClusterStatus.UP
    status_updated_at = int(time.time())

    # TODO (sumanth): Cluster history table will have multiple entries
    # when the cluster failover through multiple regions (one entry per region).
    # It can be more inaccurate for the multi-node cluster
    # as the failover can have the nodes partially UP.
    cluster_hash = _get_hash_for_existing_cluster(cluster_name) or str(
        uuid.uuid4())
    usage_intervals = _get_cluster_usage_intervals(cluster_hash)

    # first time a cluster is being launched
    if not usage_intervals:
        usage_intervals = []

    # if this is the cluster init or we are starting after a stop
    if not usage_intervals or usage_intervals[-1][-1] is not None:
        if cluster_launched_at is None:
            # This could happen when the cluster is restarted manually on the
            # cloud console. In this case, we will use the current time as the
            # cluster launched time.
            # TODO(zhwu): We should use the time when the cluster is restarted
            # to be more accurate.
            cluster_launched_at = int(time.time())
        usage_intervals.append((cluster_launched_at, None))

    user_hash = common_utils.get_user_hash()
    active_workspace = skypilot_config.get_active_workspace()

    conditional_values = {}
    if is_launch:
        conditional_values.update({
            'launched_at': cluster_launched_at,
            'last_use': last_use
        })

    if int(ready) == 1:
        conditional_values.update({
            'cluster_ever_up': 1,
        })

    if config_hash is not None:
        conditional_values.update({
            'config_hash': config_hash,
        })

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # with_for_update() locks the row until commit() or rollback()
        # is called, or until the code escapes the with block.
        cluster_row = session.query(cluster_table).filter_by(
            name=cluster_name).with_for_update().first()
        if (not cluster_row or
                cluster_row.status == status_lib.ClusterStatus.STOPPED.value):
            conditional_values.update({
                'autostop': -1,
                'to_down': 0,
            })
        if not cluster_row or not cluster_row.user_hash:
            conditional_values.update({
                'user_hash': user_hash,
            })
        if not cluster_row or not cluster_row.workspace:
            conditional_values.update({
                'workspace': active_workspace,
            })
        if (is_launch and not cluster_row or
                cluster_row.status != status_lib.ClusterStatus.UP.value):
            conditional_values.update({
                'last_creation_yaml': common_utils.dump_yaml_str(task_config)
                                      if task_config else None,
                'last_creation_command': last_use,
            })

        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            session.rollback()
            raise ValueError('Unsupported database dialect')

        insert_stmnt = insert_func(cluster_table).values(
            name=cluster_name,
            **conditional_values,
            handle=handle,
            status=status.value,
            # set metadata to server default ('{}')
            # set owner to server default (null)
            cluster_hash=cluster_hash,
            # set storage_mounts_metadata to server default (null)
            status_updated_at=status_updated_at,
        )
        do_update_stmt = insert_stmnt.on_conflict_do_update(
            index_elements=[cluster_table.c.name],
            set_={
                **conditional_values,
                cluster_table.c.handle: handle,
                cluster_table.c.status: status.value,
                # do not update metadata value
                # do not update owner value
                cluster_table.c.cluster_hash: cluster_hash,
                # do not update storage_mounts_metadata
                cluster_table.c.status_updated_at: status_updated_at,
                # do not update user_hash
            })
        session.execute(do_update_stmt)

        # Modify cluster history table
        launched_nodes = getattr(cluster_handle, 'launched_nodes', None)
        launched_resources = getattr(cluster_handle, 'launched_resources', None)

        insert_stmnt = insert_func(cluster_history_table).values(
            cluster_hash=cluster_hash,
            name=cluster_name,
            num_nodes=launched_nodes,
            requested_resources=pickle.dumps(requested_resources),
            launched_resources=pickle.dumps(launched_resources),
            usage_intervals=pickle.dumps(usage_intervals),
            user_hash=user_hash)
        do_update_stmt = insert_stmnt.on_conflict_do_update(
            index_elements=[cluster_history_table.c.cluster_hash],
            set_={
                cluster_history_table.c.name: cluster_name,
                cluster_history_table.c.num_nodes: launched_nodes,
                cluster_history_table.c.requested_resources:
                    pickle.dumps(requested_resources),
                cluster_history_table.c.launched_resources:
                    pickle.dumps(launched_resources),
                cluster_history_table.c.usage_intervals:
                    pickle.dumps(usage_intervals),
                cluster_history_table.c.user_hash: user_hash
            })
        session.execute(do_update_stmt)

        session.commit()


def _get_user_hash_or_current_user(user_hash: Optional[str]) -> str:
    """Returns the user hash or the current user hash, if user_hash is None.

    This is to ensure that the clusters created before the client-server
    architecture (no user hash info previously) are associated with the current
    user.
    """
    if user_hash is not None:
        return user_hash
    return common_utils.get_user_hash()


def update_cluster_handle(cluster_name: str,
                          cluster_handle: 'backends.ResourceHandle'):
    handle = pickle.dumps(cluster_handle)
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(cluster_table).filter_by(name=cluster_name).update(
            {cluster_table.c.handle: handle})
        session.commit()


def update_last_use(cluster_name: str):
    """Updates the last used command for the cluster."""
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(cluster_table).filter_by(name=cluster_name).update(
            {cluster_table.c.last_use: common_utils.get_current_command()})
        session.commit()


def remove_cluster(cluster_name: str, terminate: bool) -> None:
    """Removes cluster_name mapping."""
    cluster_hash = _get_hash_for_existing_cluster(cluster_name)
    usage_intervals = _get_cluster_usage_intervals(cluster_hash)

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # usage_intervals is not None and not empty
        if usage_intervals:
            assert cluster_hash is not None, cluster_name
            start_time = usage_intervals.pop()[0]
            end_time = int(time.time())
            usage_intervals.append((start_time, end_time))
            _set_cluster_usage_intervals(cluster_hash, usage_intervals)

        if terminate:
            session.query(cluster_table).filter_by(name=cluster_name).delete()
        else:
            handle = get_handle_from_cluster_name(cluster_name)
            if handle is None:
                return
            # Must invalidate IP list to avoid directly trying to ssh into a
            # stopped VM, which leads to timeout.
            if hasattr(handle, 'stable_internal_external_ips'):
                handle = typing.cast('backends.CloudVmRayResourceHandle',
                                     handle)
                handle.stable_internal_external_ips = None
            current_time = int(time.time())
            session.query(cluster_table).filter_by(name=cluster_name).update({
                cluster_table.c.handle: pickle.dumps(handle),
                cluster_table.c.status: status_lib.ClusterStatus.STOPPED.value,
                cluster_table.c.status_updated_at: current_time
            })
        session.commit()


def get_handle_from_cluster_name(
        cluster_name: str) -> Optional['backends.ResourceHandle']:
    assert cluster_name is not None, 'cluster_name cannot be None'
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None:
        return None
    return pickle.loads(row.handle)


def get_glob_cluster_names(cluster_name: str) -> List[str]:
    assert cluster_name is not None, 'cluster_name cannot be None'
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            rows = session.query(cluster_table).filter(
                cluster_table.c.name.op('GLOB')(cluster_name)).all()
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            rows = session.query(cluster_table).filter(
                cluster_table.c.name.op('SIMILAR TO')(
                    _glob_to_similar(cluster_name))).all()
        else:
            raise ValueError('Unsupported database dialect')
    return [row.name for row in rows]


def set_cluster_status(cluster_name: str,
                       status: status_lib.ClusterStatus) -> None:
    current_time = int(time.time())
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(cluster_table).filter_by(
            name=cluster_name).update({
                cluster_table.c.status: status.value,
                cluster_table.c.status_updated_at: current_time
            })
        session.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


def set_cluster_autostop_value(cluster_name: str, idle_minutes: int,
                               to_down: bool) -> None:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(cluster_table).filter_by(
            name=cluster_name).update({
                cluster_table.c.autostop: idle_minutes,
                cluster_table.c.to_down: int(to_down)
            })
        session.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


def get_cluster_launch_time(cluster_name: str) -> Optional[int]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None or row.launched_at is None:
        return None
    return int(row.launched_at)


def get_cluster_info(cluster_name: str) -> Optional[Dict[str, Any]]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None or row.metadata is None:
        return None
    return json.loads(row.metadata)


def set_cluster_info(cluster_name: str, metadata: Dict[str, Any]) -> None:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(cluster_table).filter_by(
            name=cluster_name).update(
                {cluster_table.c.metadata: json.dumps(metadata)})
        session.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


def get_cluster_storage_mounts_metadata(
        cluster_name: str) -> Optional[Dict[str, Any]]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None or row.storage_mounts_metadata is None:
        return None
    return pickle.loads(row.storage_mounts_metadata)


def set_cluster_storage_mounts_metadata(
        cluster_name: str, storage_mounts_metadata: Dict[str, Any]) -> None:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(cluster_table).filter_by(
            name=cluster_name).update({
                cluster_table.c.storage_mounts_metadata:
                    pickle.dumps(storage_mounts_metadata)
            })
        session.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


def _get_cluster_usage_intervals(
        cluster_hash: Optional[str]
) -> Optional[List[Tuple[int, Optional[int]]]]:
    if cluster_hash is None:
        return None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_history_table).filter_by(
            cluster_hash=cluster_hash).first()
    if row is None or row.usage_intervals is None:
        return None
    return pickle.loads(row.usage_intervals)


def _get_cluster_launch_time(cluster_hash: str) -> Optional[int]:
    usage_intervals = _get_cluster_usage_intervals(cluster_hash)
    if usage_intervals is None:
        return None
    return usage_intervals[0][0]


def _get_cluster_duration(cluster_hash: str) -> int:
    total_duration = 0
    usage_intervals = _get_cluster_usage_intervals(cluster_hash)

    if usage_intervals is None:
        return total_duration

    for i, (start_time, end_time) in enumerate(usage_intervals):
        # duration from latest start time to time of query
        if start_time is None:
            continue
        if end_time is None:
            assert i == len(usage_intervals) - 1, i
            end_time = int(time.time())
        start_time, end_time = int(start_time), int(end_time)
        total_duration += end_time - start_time
    return total_duration


def _set_cluster_usage_intervals(
        cluster_hash: str, usage_intervals: List[Tuple[int,
                                                       Optional[int]]]) -> None:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(cluster_history_table).filter_by(
            cluster_hash=cluster_hash).update({
                cluster_history_table.c.usage_intervals:
                    pickle.dumps(usage_intervals)
            })
        session.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster hash {cluster_hash} not found.')


def set_owner_identity_for_cluster(cluster_name: str,
                                   owner_identity: Optional[List[str]]) -> None:
    if owner_identity is None:
        return
    owner_identity_str = json.dumps(owner_identity)
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(cluster_table).filter_by(
            name=cluster_name).update(
                {cluster_table.c.owner: owner_identity_str})
        session.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


def _get_hash_for_existing_cluster(cluster_name: str) -> Optional[str]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None or row.cluster_hash is None:
        return None
    return row.cluster_hash


def get_launched_resources_from_cluster_hash(
        cluster_hash: str) -> Optional[Tuple[int, Any]]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_history_table).filter_by(
            cluster_hash=cluster_hash).first()
    if row is None:
        return None
    num_nodes = row.num_nodes
    launched_resources = row.launched_resources

    if num_nodes is None or launched_resources is None:
        return None
    launched_resources = pickle.loads(launched_resources)
    return num_nodes, launched_resources


def _load_owner(record_owner: Optional[str]) -> Optional[List[str]]:
    if record_owner is None:
        return None
    try:
        result = json.loads(record_owner)
        if result is not None and not isinstance(result, list):
            # Backwards compatibility for old records, which were stored as
            # a string instead of a list. It is possible that json.loads
            # will parse the string with all numbers as an int or escape
            # some characters, such as \n, so we need to use the original
            # record_owner.
            return [record_owner]
        return result
    except json.JSONDecodeError:
        # Backwards compatibility for old records, which were stored as
        # a string instead of a list. This will happen when the previous
        # UserId is a string instead of an int.
        return [record_owner]


def _load_storage_mounts_metadata(
    record_storage_mounts_metadata: Optional[bytes]
) -> Optional[Dict[str, 'Storage.StorageMetadata']]:
    if not record_storage_mounts_metadata:
        return None
    return pickle.loads(record_storage_mounts_metadata)


@context_utils.cancellation_guard
def get_cluster_from_name(
        cluster_name: Optional[str]) -> Optional[Dict[str, Any]]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None:
        return None
    user_hash = _get_user_hash_or_current_user(row.user_hash)
    # TODO: use namedtuple instead of dict
    record = {
        'name': row.name,
        'launched_at': row.launched_at,
        'handle': pickle.loads(row.handle),
        'last_use': row.last_use,
        'status': status_lib.ClusterStatus[row.status],
        'autostop': row.autostop,
        'to_down': bool(row.to_down),
        'owner': _load_owner(row.owner),
        'metadata': json.loads(row.metadata),
        'cluster_hash': row.cluster_hash,
        'storage_mounts_metadata': _load_storage_mounts_metadata(
            row.storage_mounts_metadata),
        'cluster_ever_up': bool(row.cluster_ever_up),
        'status_updated_at': row.status_updated_at,
        'user_hash': user_hash,
        'user_name': get_user(user_hash).name,
        'config_hash': row.config_hash,
        'workspace': row.workspace,
        'last_creation_yaml': row.last_creation_yaml,
        'last_creation_command': row.last_creation_command,
    }

    return record


def get_clusters() -> List[Dict[str, Any]]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(cluster_table).order_by(
            sqlalchemy.desc(cluster_table.c.launched_at)).all()
    records = []
    for row in rows:
        user_hash = _get_user_hash_or_current_user(row.user_hash)
        # TODO: use namedtuple instead of dict
        record = {
            'name': row.name,
            'launched_at': row.launched_at,
            'handle': pickle.loads(row.handle),
            'last_use': row.last_use,
            'status': status_lib.ClusterStatus[row.status],
            'autostop': row.autostop,
            'to_down': bool(row.to_down),
            'owner': _load_owner(row.owner),
            'metadata': json.loads(row.metadata),
            'cluster_hash': row.cluster_hash,
            'storage_mounts_metadata': _load_storage_mounts_metadata(
                row.storage_mounts_metadata),
            'cluster_ever_up': bool(row.cluster_ever_up),
            'status_updated_at': row.status_updated_at,
            'user_hash': user_hash,
            'user_name': get_user(user_hash).name,
            'config_hash': row.config_hash,
            'workspace': row.workspace,
            'last_creation_yaml': row.last_creation_yaml,
            'last_creation_command': row.last_creation_command,
        }

        records.append(record)
    return records


def get_clusters_from_history() -> List[Dict[str, Any]]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(
            cluster_history_table.join(cluster_table,
                                       cluster_history_table.c.cluster_hash ==
                                       cluster_table.c.cluster_hash,
                                       isouter=True)).all()

    # '(cluster_hash, name, num_nodes, requested_resources, '
    #         'launched_resources, usage_intervals) '
    records = []
    for row in rows:
        # TODO: use namedtuple instead of dict
        user_hash = _get_user_hash_or_current_user(row.user_hash)
        status = row.status
        if status is not None:
            status = status_lib.ClusterStatus[status]
        record = {
            'name': row.name,
            'launched_at': _get_cluster_launch_time(row.cluster_hash),
            'duration': _get_cluster_duration(row.cluster_hash),
            'num_nodes': row.num_nodes,
            'resources': pickle.loads(row.launched_resources),
            'cluster_hash': row.cluster_hash,
            'usage_intervals': pickle.loads(row.usage_intervals),
            'status': status,
            'user_hash': user_hash,
        }

        records.append(record)

    # sort by launch time, descending in recency
    records = sorted(records, key=lambda record: -record['launched_at'])
    return records


def get_cluster_names_start_with(starts_with: str) -> List[str]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(cluster_table).filter(
            cluster_table.c.name.like(f'{starts_with}%')).all()
    return [row.name for row in rows]


def get_cached_enabled_clouds(cloud_capability: 'cloud.CloudCapability',
                              workspace: str) -> List['clouds.Cloud']:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(config_table).filter_by(
            key=_get_enabled_clouds_key(cloud_capability, workspace)).first()
    ret = []
    if row:
        ret = json.loads(row.value)
    enabled_clouds: List['clouds.Cloud'] = []
    for c in ret:
        try:
            cloud = registry.CLOUD_REGISTRY.from_str(c)
        except ValueError:
            # Handle the case for the clouds whose support has been
            # removed from SkyPilot, e.g., 'local' was a cloud in the past
            # and may be stored in the database for users before #3037.
            # We should ignore removed clouds and continue.
            continue
        if cloud is not None:
            enabled_clouds.append(cloud)
    return enabled_clouds


def set_enabled_clouds(enabled_clouds: List[str],
                       cloud_capability: 'cloud.CloudCapability',
                       workspace: str) -> None:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')
        insert_stmnt = insert_func(config_table).values(
            key=_get_enabled_clouds_key(cloud_capability, workspace),
            value=json.dumps(enabled_clouds))
        do_update_stmt = insert_stmnt.on_conflict_do_update(
            index_elements=[config_table.c.key],
            set_={config_table.c.value: json.dumps(enabled_clouds)})
        session.execute(do_update_stmt)
        session.commit()


def _get_enabled_clouds_key(cloud_capability: 'cloud.CloudCapability',
                            workspace: str) -> str:
    return _ENABLED_CLOUDS_KEY_PREFIX + workspace + '_' + cloud_capability.value


def add_or_update_storage(storage_name: str,
                          storage_handle: 'Storage.StorageMetadata',
                          storage_status: status_lib.StorageStatus):
    storage_launched_at = int(time.time())
    handle = pickle.dumps(storage_handle)
    last_use = common_utils.get_current_command()

    def status_check(status):
        return status in status_lib.StorageStatus

    if not status_check(storage_status):
        raise ValueError(f'Error in updating global state. Storage Status '
                         f'{storage_status} is passed in incorrectly')
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')
        insert_stmnt = insert_func(storage_table).values(
            name=storage_name,
            handle=handle,
            last_use=last_use,
            launched_at=storage_launched_at,
            status=storage_status.value)
        do_update_stmt = insert_stmnt.on_conflict_do_update(
            index_elements=[storage_table.c.name],
            set_={
                storage_table.c.handle: handle,
                storage_table.c.last_use: last_use,
                storage_table.c.launched_at: storage_launched_at,
                storage_table.c.status: storage_status.value
            })
        session.execute(do_update_stmt)
        session.commit()


def remove_storage(storage_name: str):
    """Removes Storage from Database"""
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(storage_table).filter_by(name=storage_name).delete()
        session.commit()


def set_storage_status(storage_name: str,
                       status: status_lib.StorageStatus) -> None:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(storage_table).filter_by(
            name=storage_name).update({storage_table.c.status: status.value})
        session.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Storage {storage_name} not found.')


def get_storage_status(storage_name: str) -> Optional[status_lib.StorageStatus]:
    assert storage_name is not None, 'storage_name cannot be None'
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(storage_table).filter_by(name=storage_name).first()
    if row:
        return status_lib.StorageStatus[row.status]
    return None


def set_storage_handle(storage_name: str,
                       handle: 'Storage.StorageMetadata') -> None:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(storage_table).filter_by(
            name=storage_name).update(
                {storage_table.c.handle: pickle.dumps(handle)})
        session.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Storage{storage_name} not found.')


def get_handle_from_storage_name(
        storage_name: Optional[str]) -> Optional['Storage.StorageMetadata']:
    if storage_name is None:
        return None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(storage_table).filter_by(name=storage_name).first()
    if row:
        return pickle.loads(row.handle)
    return None


def get_glob_storage_name(storage_name: str) -> List[str]:
    assert storage_name is not None, 'storage_name cannot be None'
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            rows = session.query(storage_table).filter(
                storage_table.c.name.op('GLOB')(storage_name)).all()
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            rows = session.query(storage_table).filter(
                storage_table.c.name.op('SIMILAR TO')(
                    _glob_to_similar(storage_name))).all()
        else:
            raise ValueError('Unsupported database dialect')
    return [row.name for row in rows]


def get_storage_names_start_with(starts_with: str) -> List[str]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(storage_table).filter(
            storage_table.c.name.like(f'{starts_with}%')).all()
    return [row.name for row in rows]


def get_storage() -> List[Dict[str, Any]]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(storage_table).all()
    records = []
    for row in rows:
        # TODO: use namedtuple instead of dict
        records.append({
            'name': row.name,
            'launched_at': row.launched_at,
            'handle': pickle.loads(row.handle),
            'last_use': row.last_use,
            'status': status_lib.StorageStatus[row.status],
        })
    return records


def get_ssh_keys(user_hash: str) -> Tuple[str, str, bool]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(ssh_key_table).filter_by(
            user_hash=user_hash).first()
    if row:
        return row.ssh_public_key, row.ssh_private_key, True
    return '', '', False


def set_ssh_keys(user_hash: str, ssh_public_key: str, ssh_private_key: str):
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')
        insert_stmnt = insert_func(ssh_key_table).values(
            user_hash=user_hash,
            ssh_public_key=ssh_public_key,
            ssh_private_key=ssh_private_key)
        do_update_stmt = insert_stmnt.on_conflict_do_update(
            index_elements=[ssh_key_table.c.user_hash],
            set_={
                ssh_key_table.c.ssh_public_key: ssh_public_key,
                ssh_key_table.c.ssh_private_key: ssh_private_key
            })
        session.execute(do_update_stmt)
        session.commit()


def get_cluster_yaml_str(cluster_yaml_path: Optional[str]) -> Optional[str]:
    """Get the cluster yaml from the database or the local file system.
    If the cluster yaml is not in the database, check if it exists on the
    local file system and migrate it to the database.

    It is assumed that the cluster yaml file is named as <cluster_name>.yml.
    """
    if cluster_yaml_path is None:
        raise ValueError('Attempted to read a None YAML.')
    cluster_file_name = os.path.basename(cluster_yaml_path)
    cluster_name, _ = os.path.splitext(cluster_file_name)
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_yaml_table).filter_by(
            cluster_name=cluster_name).first()
    if row is None:
        # If the cluster yaml is not in the database, check if it exists
        # on the local file system and migrate it to the database.
        # TODO(syang): remove this check once we have a way to migrate the
        # cluster from file to database. Remove on v0.12.0.
        if cluster_yaml_path is not None and os.path.exists(cluster_yaml_path):
            with open(cluster_yaml_path, 'r', encoding='utf-8') as f:
                yaml_str = f.read()
            set_cluster_yaml(cluster_name, yaml_str)
            return yaml_str
        return None
    return row.yaml


def get_cluster_yaml_dict(cluster_yaml_path: Optional[str]) -> Dict[str, Any]:
    """Get the cluster yaml as a dictionary from the database.

    It is assumed that the cluster yaml file is named as <cluster_name>.yml.
    """
    yaml_str = get_cluster_yaml_str(cluster_yaml_path)
    if yaml_str is None:
        raise ValueError(f'Cluster yaml {cluster_yaml_path} not found.')
    return yaml.safe_load(yaml_str)


def set_cluster_yaml(cluster_name: str, yaml_str: str) -> None:
    """Set the cluster yaml in the database."""
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')
        insert_stmnt = insert_func(cluster_yaml_table).values(
            cluster_name=cluster_name, yaml=yaml_str)
        do_update_stmt = insert_stmnt.on_conflict_do_update(
            index_elements=[cluster_yaml_table.c.cluster_name],
            set_={cluster_yaml_table.c.yaml: yaml_str})
        session.execute(do_update_stmt)
        session.commit()


def remove_cluster_yaml(cluster_name: str):
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(cluster_yaml_table).filter_by(
            cluster_name=cluster_name).delete()
        session.commit()
