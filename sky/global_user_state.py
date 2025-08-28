"""Global user state, backed by a sqlite database.

Concepts:
- Cluster name: a user-supplied or auto-generated unique name to identify a
  cluster.
- Cluster handle: (non-user facing) an opaque backend handle for us to
  interact with a cluster.
"""
import asyncio
import enum
import functools
import json
import os
import pickle
import re
import threading
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

from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import context_utils
from sky.utils import registry
from sky.utils import status_lib
from sky.utils import yaml_utils
from sky.utils.db import db_utils
from sky.utils.db import migration_utils

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import clouds
    from sky.clouds import cloud
    from sky.data import Storage

logger = sky_logging.init_logger(__name__)

_ENABLED_CLOUDS_KEY_PREFIX = 'enabled_clouds_'
_ALLOWED_CLOUDS_KEY_PREFIX = 'allowed_clouds_'

_SQLALCHEMY_ENGINE: Optional[sqlalchemy.engine.Engine] = None
_SQLALCHEMY_ENGINE_LOCK = threading.Lock()

DEFAULT_CLUSTER_EVENT_RETENTION_HOURS = 24.0
DEBUG_CLUSTER_EVENT_RETENTION_HOURS = 30 * 24.0
MIN_CLUSTER_EVENT_DAEMON_INTERVAL_SECONDS = 3600

_UNIQUE_CONSTRAINT_FAILED_ERROR_MSGS = [
    # sqlite
    'UNIQUE constraint failed',
    # postgres
    'duplicate key value violates unique constraint',
]

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
    sqlalchemy.Column('password', sqlalchemy.Text),
    sqlalchemy.Column('created_at', sqlalchemy.Integer),
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
    sqlalchemy.Column('is_managed', sqlalchemy.Integer, server_default='0'),
    sqlalchemy.Column('provision_log_path',
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

volume_table = sqlalchemy.Table(
    'volumes',
    Base.metadata,
    sqlalchemy.Column('name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('launched_at', sqlalchemy.Integer),
    sqlalchemy.Column('handle', sqlalchemy.LargeBinary),
    sqlalchemy.Column('user_hash', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('workspace',
                      sqlalchemy.Text,
                      server_default=constants.SKYPILOT_DEFAULT_WORKSPACE),
    sqlalchemy.Column('last_attached_at',
                      sqlalchemy.Integer,
                      server_default=None),
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
    sqlalchemy.Column('last_creation_yaml',
                      sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('last_creation_command',
                      sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('workspace', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('provision_log_path',
                      sqlalchemy.Text,
                      server_default=None),
)


class ClusterEventType(enum.Enum):
    """Type of cluster event."""
    DEBUG = 'DEBUG'
    """Used to denote events that are not related to cluster status."""

    STATUS_CHANGE = 'STATUS_CHANGE'
    """Used to denote events that modify cluster status."""


# Table for cluster status change events.
# starting_status: Status of the cluster at the start of the event.
# ending_status: Status of the cluster at the end of the event.
# reason: Reason for the transition.
# transitioned_at: Timestamp of the transition.
cluster_event_table = sqlalchemy.Table(
    'cluster_events',
    Base.metadata,
    sqlalchemy.Column('cluster_hash', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('name', sqlalchemy.Text),
    sqlalchemy.Column('starting_status', sqlalchemy.Text),
    sqlalchemy.Column('ending_status', sqlalchemy.Text),
    sqlalchemy.Column('reason', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('transitioned_at', sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column('type', sqlalchemy.Text),
)

ssh_key_table = sqlalchemy.Table(
    'ssh_key',
    Base.metadata,
    sqlalchemy.Column('user_hash', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('ssh_public_key', sqlalchemy.Text),
    sqlalchemy.Column('ssh_private_key', sqlalchemy.Text),
)

service_account_token_table = sqlalchemy.Table(
    'service_account_tokens',
    Base.metadata,
    sqlalchemy.Column('token_id', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('token_name', sqlalchemy.Text),
    sqlalchemy.Column('token_hash', sqlalchemy.Text),
    sqlalchemy.Column('created_at', sqlalchemy.Integer),
    sqlalchemy.Column('last_used_at', sqlalchemy.Integer, server_default=None),
    sqlalchemy.Column('expires_at', sqlalchemy.Integer, server_default=None),
    sqlalchemy.Column('creator_user_hash',
                      sqlalchemy.Text),  # Who created this token
    sqlalchemy.Column('service_account_user_id',
                      sqlalchemy.Text),  # Service account's own user ID
)

cluster_yaml_table = sqlalchemy.Table(
    'cluster_yaml',
    Base.metadata,
    sqlalchemy.Column('cluster_name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('yaml', sqlalchemy.Text),
)

system_config_table = sqlalchemy.Table(
    'system_config',
    Base.metadata,
    sqlalchemy.Column('config_key', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('config_value', sqlalchemy.Text),
    sqlalchemy.Column('created_at', sqlalchemy.Integer),
    sqlalchemy.Column('updated_at', sqlalchemy.Integer),
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

    migration_utils.safe_alembic_upgrade(
        engine, migration_utils.GLOBAL_USER_STATE_DB_NAME,
        migration_utils.GLOBAL_USER_STATE_VERSION)


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
        engine = migration_utils.get_engine('state')

        # run migrations if needed
        create_table(engine)

        # return engine
        _SQLALCHEMY_ENGINE = engine
        return _SQLALCHEMY_ENGINE


def _init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        initialize_and_get_db()
        return func(*args, **kwargs)

    return wrapper


@_init_db
def add_or_update_user(user: models.User,
                       allow_duplicate_name: bool = True) -> bool:
    """Store the mapping from user hash to user name for display purposes.

    Returns:
        Boolean: whether the user is newly added
    """
    assert _SQLALCHEMY_ENGINE is not None

    if user.name is None:
        return False

    # Set created_at if not already set
    created_at = user.created_at
    if created_at is None:
        created_at = int(time.time())
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # Check for duplicate names if not allowed (within the same transaction)
        if not allow_duplicate_name:
            existing_user = session.query(user_table).filter(
                user_table.c.name == user.name).first()
            if existing_user is not None:
                return False

        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            # For SQLite, use INSERT OR IGNORE followed by UPDATE to detect new
            # vs existing
            insert_func = sqlite.insert

            # First try INSERT OR IGNORE - this won't fail if user exists
            insert_stmnt = insert_func(user_table).prefix_with(
                'OR IGNORE').values(id=user.id,
                                    name=user.name,
                                    password=user.password,
                                    created_at=created_at)
            result = session.execute(insert_stmnt)

            # Check if the INSERT actually inserted a row
            was_inserted = result.rowcount > 0

            if not was_inserted:
                # User existed, so update it (but don't update created_at)
                if user.password:
                    session.query(user_table).filter_by(id=user.id).update({
                        user_table.c.name: user.name,
                        user_table.c.password: user.password
                    })
                else:
                    session.query(user_table).filter_by(id=user.id).update(
                        {user_table.c.name: user.name})

            session.commit()
            return was_inserted

        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            # For PostgreSQL, use INSERT ... ON CONFLICT with RETURNING to
            # detect insert vs update
            insert_func = postgresql.insert

            insert_stmnt = insert_func(user_table).values(
                id=user.id,
                name=user.name,
                password=user.password,
                created_at=created_at)

            # Use a sentinel in the RETURNING clause to detect insert vs update
            if user.password:
                set_ = {
                    user_table.c.name: user.name,
                    user_table.c.password: user.password
                }
            else:
                set_ = {user_table.c.name: user.name}
            upsert_stmnt = insert_stmnt.on_conflict_do_update(
                index_elements=[user_table.c.id], set_=set_).returning(
                    user_table.c.id,
                    # This will be True for INSERT, False for UPDATE
                    sqlalchemy.literal_column('(xmax = 0)').label('was_inserted'
                                                                 ))

            result = session.execute(upsert_stmnt)
            row = result.fetchone()

            ret = bool(row.was_inserted) if row else False
            session.commit()

            return ret
        else:
            raise ValueError('Unsupported database dialect')


@_init_db
def get_user(user_id: str) -> Optional[models.User]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(user_table).filter_by(id=user_id).first()
    if row is None:
        return None
    return models.User(id=row.id,
                       name=row.name,
                       password=row.password,
                       created_at=row.created_at)


@_init_db
def _get_users(user_ids: Set[str]) -> Dict[str, models.User]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(user_table).filter(
            user_table.c.id.in_(user_ids)).all()
    return {
        row.id: models.User(id=row.id,
                            name=row.name,
                            password=row.password,
                            created_at=row.created_at) for row in rows
    }


@_init_db
def get_user_by_name(username: str) -> List[models.User]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(user_table).filter_by(name=username).all()
    if len(rows) == 0:
        return []
    return [
        models.User(id=row.id,
                    name=row.name,
                    password=row.password,
                    created_at=row.created_at) for row in rows
    ]


@_init_db
def get_user_by_name_match(username_match: str) -> List[models.User]:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(user_table).filter(
            user_table.c.name.like(f'%{username_match}%')).all()
    return [
        models.User(id=row.id, name=row.name, created_at=row.created_at)
        for row in rows
    ]


@_init_db
def delete_user(user_id: str) -> None:
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(user_table).filter_by(id=user_id).delete()
        session.commit()


@_init_db
def get_all_users() -> List[models.User]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(user_table).all()
    return [
        models.User(id=row.id,
                    name=row.name,
                    password=row.password,
                    created_at=row.created_at) for row in rows
    ]


@_init_db
def add_or_update_cluster(cluster_name: str,
                          cluster_handle: 'backends.ResourceHandle',
                          requested_resources: Optional[Set[Any]],
                          ready: bool,
                          is_launch: bool = True,
                          config_hash: Optional[str] = None,
                          task_config: Optional[Dict[str, Any]] = None,
                          is_managed: bool = False,
                          provision_log_path: Optional[str] = None):
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
        is_managed: Whether the cluster is launched by the
            controller.
        provision_log_path: Absolute path to provision.log, if available.
    """
    assert _SQLALCHEMY_ENGINE is not None
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

    user_hash = common_utils.get_current_user().id
    active_workspace = skypilot_config.get_active_workspace()
    history_workspace = active_workspace
    history_hash = user_hash

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
                'last_creation_yaml': yaml_utils.dump_yaml_str(task_config)
                                      if task_config else None,
                'last_creation_command': last_use,
            })
        if provision_log_path is not None:
            conditional_values.update({
                'provision_log_path': provision_log_path,
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
            is_managed=int(is_managed),
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
        if cluster_row and cluster_row.workspace:
            history_workspace = cluster_row.workspace
        if cluster_row and cluster_row.user_hash:
            history_hash = cluster_row.user_hash
        creation_info = {}
        if conditional_values.get('last_creation_yaml') is not None:
            creation_info = {
                'last_creation_yaml':
                    conditional_values.get('last_creation_yaml'),
                'last_creation_command':
                    conditional_values.get('last_creation_command'),
            }

        insert_stmnt = insert_func(cluster_history_table).values(
            cluster_hash=cluster_hash,
            name=cluster_name,
            num_nodes=launched_nodes,
            requested_resources=pickle.dumps(requested_resources),
            launched_resources=pickle.dumps(launched_resources),
            usage_intervals=pickle.dumps(usage_intervals),
            user_hash=user_hash,
            workspace=history_workspace,
            provision_log_path=provision_log_path,
            **creation_info,
        )
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
                cluster_history_table.c.user_hash: history_hash,
                cluster_history_table.c.workspace: history_workspace,
                cluster_history_table.c.provision_log_path: provision_log_path,
                **creation_info,
            })
        session.execute(do_update_stmt)

        session.commit()


@_init_db
def add_cluster_event(cluster_name: str,
                      new_status: Optional[status_lib.ClusterStatus],
                      reason: str,
                      event_type: ClusterEventType,
                      nop_if_duplicate: bool = False,
                      duplicate_regex: Optional[str] = None,
                      expose_duplicate_error: bool = False,
                      transitioned_at: Optional[int] = None) -> None:
    """Add a cluster event.

    Args:
        cluster_name: Name of the cluster.
        new_status: New status of the cluster.
        reason: Reason for the event.
        event_type: Type of the event.
        nop_if_duplicate: If True, do not add the event if it is a duplicate.
        duplicate_regex: If provided, do not add the event if it matches the
            regex. Only used if nop_if_duplicate is True.
        expose_duplicate_error: If True, raise an error if the event is a
            duplicate. Only used if nop_if_duplicate is True.
        transitioned_at: If provided, use this timestamp for the event.
    """
    assert _SQLALCHEMY_ENGINE is not None
    cluster_hash = _get_hash_for_existing_cluster(cluster_name)
    if cluster_hash is None:
        logger.debug(f'Hash for cluster {cluster_name} not found. '
                     'Skipping event.')
        return
    if transitioned_at is None:
        transitioned_at = int(time.time())
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            session.rollback()
            raise ValueError('Unsupported database dialect')

        cluster_row = session.query(cluster_table).filter_by(name=cluster_name)
        last_status = cluster_row.first(
        ).status if cluster_row and cluster_row.first() is not None else None
        if nop_if_duplicate:
            last_event = get_last_cluster_event(cluster_hash,
                                                event_type=event_type)
            if duplicate_regex is not None and last_event is not None:
                if re.search(duplicate_regex, last_event):
                    return
            elif last_event == reason:
                return
        try:
            session.execute(
                insert_func(cluster_event_table).values(
                    cluster_hash=cluster_hash,
                    name=cluster_name,
                    starting_status=last_status,
                    ending_status=new_status.value if new_status else None,
                    reason=reason,
                    transitioned_at=transitioned_at,
                    type=event_type.value,
                ))
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            for msg in _UNIQUE_CONSTRAINT_FAILED_ERROR_MSGS:
                if msg in str(e):
                    # This can happen if the cluster event is added twice.
                    # We can ignore this error unless the caller requests
                    # to expose the error.
                    if expose_duplicate_error:
                        raise db_utils.UniqueConstraintViolationError(
                            value=reason, message=str(e))
                    else:
                        return
            raise e


def get_last_cluster_event(cluster_hash: str,
                           event_type: ClusterEventType) -> Optional[str]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_event_table).filter_by(
            cluster_hash=cluster_hash, type=event_type.value).order_by(
                cluster_event_table.c.transitioned_at.desc()).first()
    if row is None:
        return None
    return row.reason


def _get_last_cluster_event_multiple(
        cluster_hashes: Set[str],
        event_type: ClusterEventType) -> Dict[str, str]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # Use a subquery to get the latest event for each cluster_hash
        latest_events = session.query(
            cluster_event_table.c.cluster_hash,
            sqlalchemy.func.max(cluster_event_table.c.transitioned_at).label(
                'max_time')).filter(
                    cluster_event_table.c.cluster_hash.in_(cluster_hashes),
                    cluster_event_table.c.type == event_type.value).group_by(
                        cluster_event_table.c.cluster_hash).subquery()

        # Join with original table to get the full event details
        rows = session.query(cluster_event_table).join(
            latest_events,
            sqlalchemy.and_(
                cluster_event_table.c.cluster_hash ==
                latest_events.c.cluster_hash,
                cluster_event_table.c.transitioned_at ==
                latest_events.c.max_time)).all()

    return {row.cluster_hash: row.reason for row in rows}


def cleanup_cluster_events_with_retention(retention_hours: float,
                                          event_type: ClusterEventType) -> None:
    assert _SQLALCHEMY_ENGINE is not None
    # Once for events with type STATUS_CHANGE.
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        query = session.query(cluster_event_table).filter(
            cluster_event_table.c.transitioned_at <
            time.time() - retention_hours * 3600,
            cluster_event_table.c.type == event_type.value)
        logger.debug(f'Deleting {query.count()} cluster events.')
        query.delete()
        session.commit()


async def cluster_event_retention_daemon():
    """Garbage collect cluster events periodically."""
    while True:
        logger.info('Running cluster event retention daemon...')
        # Use the latest config.
        skypilot_config.reload_config()
        retention_hours = skypilot_config.get_nested(
            ('api_server', 'cluster_event_retention_hours'),
            DEFAULT_CLUSTER_EVENT_RETENTION_HOURS)
        debug_retention_hours = skypilot_config.get_nested(
            ('api_server', 'cluster_debug_event_retention_hours'),
            DEBUG_CLUSTER_EVENT_RETENTION_HOURS)
        try:
            if retention_hours >= 0:
                logger.debug('Cleaning up cluster events with retention '
                             f'{retention_hours} hours.')
                cleanup_cluster_events_with_retention(
                    retention_hours, ClusterEventType.STATUS_CHANGE)
            if debug_retention_hours >= 0:
                logger.debug('Cleaning up debug cluster events with retention '
                             f'{debug_retention_hours} hours.')
                cleanup_cluster_events_with_retention(debug_retention_hours,
                                                      ClusterEventType.DEBUG)
        except asyncio.CancelledError:
            logger.info('Cluster event retention daemon cancelled')
            break
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error running cluster event retention daemon: {e}')

        # Run daemon at most once every hour to avoid too frequent cleanup.
        sleep_amount = max(
            min(retention_hours * 3600, debug_retention_hours * 3600),
            MIN_CLUSTER_EVENT_DAEMON_INTERVAL_SECONDS)
        await asyncio.sleep(sleep_amount)


def get_cluster_events(cluster_name: Optional[str], cluster_hash: Optional[str],
                       event_type: ClusterEventType) -> List[str]:
    """Returns the cluster events for the cluster.

    Args:
        cluster_name: Name of the cluster. Cannot be specified if cluster_hash
            is specified.
        cluster_hash: Hash of the cluster. Cannot be specified if cluster_name
            is specified.
        event_type: Type of the event.
    """
    assert _SQLALCHEMY_ENGINE is not None

    if cluster_name is not None and cluster_hash is not None:
        raise ValueError('Cannot specify both cluster_name and cluster_hash')
    if cluster_name is None and cluster_hash is None:
        raise ValueError('Must specify either cluster_name or cluster_hash')
    if cluster_name is not None:
        cluster_hash = _get_hash_for_existing_cluster(cluster_name)
        if cluster_hash is None:
            raise ValueError(f'Hash for cluster {cluster_name} not found.')

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(cluster_event_table).filter_by(
            cluster_hash=cluster_hash, type=event_type.value).order_by(
                cluster_event_table.c.transitioned_at.asc()).all()
    return [row.reason for row in rows]


def _get_user_hash_or_current_user(user_hash: Optional[str]) -> str:
    """Returns the user hash or the current user hash, if user_hash is None.

    This is to ensure that the clusters created before the client-server
    architecture (no user hash info previously) are associated with the current
    user.
    """
    if user_hash is not None:
        return user_hash
    return common_utils.get_user_hash()


@_init_db
def update_cluster_handle(cluster_name: str,
                          cluster_handle: 'backends.ResourceHandle'):
    assert _SQLALCHEMY_ENGINE is not None
    handle = pickle.dumps(cluster_handle)
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(cluster_table).filter_by(name=cluster_name).update(
            {cluster_table.c.handle: handle})
        session.commit()


@_init_db
def update_last_use(cluster_name: str):
    """Updates the last used command for the cluster."""
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(cluster_table).filter_by(name=cluster_name).update(
            {cluster_table.c.last_use: common_utils.get_current_command()})
        session.commit()


@_init_db
def remove_cluster(cluster_name: str, terminate: bool) -> None:
    """Removes cluster_name mapping."""
    assert _SQLALCHEMY_ENGINE is not None
    cluster_hash = _get_hash_for_existing_cluster(cluster_name)
    usage_intervals = _get_cluster_usage_intervals(cluster_hash)
    provision_log_path = get_cluster_provision_log_path(cluster_name)

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # usage_intervals is not None and not empty
        if usage_intervals:
            assert cluster_hash is not None, cluster_name
            start_time = usage_intervals.pop()[0]
            end_time = int(time.time())
            usage_intervals.append((start_time, end_time))
            _set_cluster_usage_intervals(cluster_hash, usage_intervals)

        if provision_log_path:
            assert cluster_hash is not None, cluster_name
            session.query(cluster_history_table).filter_by(
                cluster_hash=cluster_hash
            ).filter(
                cluster_history_table.c.provision_log_path.is_(None)
            ).update({
                cluster_history_table.c.provision_log_path: provision_log_path
            })

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


@_init_db
def get_handle_from_cluster_name(
        cluster_name: str) -> Optional['backends.ResourceHandle']:
    assert _SQLALCHEMY_ENGINE is not None
    assert cluster_name is not None, 'cluster_name cannot be None'
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None:
        return None
    return pickle.loads(row.handle)


@_init_db
def get_glob_cluster_names(cluster_name: str) -> List[str]:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def set_cluster_status(cluster_name: str,
                       status: status_lib.ClusterStatus) -> None:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def set_cluster_autostop_value(cluster_name: str, idle_minutes: int,
                               to_down: bool) -> None:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def get_cluster_launch_time(cluster_name: str) -> Optional[int]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None or row.launched_at is None:
        return None
    return int(row.launched_at)


@_init_db
def get_cluster_info(cluster_name: str) -> Optional[Dict[str, Any]]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None or row.metadata is None:
        return None
    return json.loads(row.metadata)


@_init_db
def get_cluster_provision_log_path(cluster_name: str) -> Optional[str]:
    """Returns provision_log_path from clusters table, if recorded."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None:
        return None
    return getattr(row, 'provision_log_path', None)


@_init_db
def get_cluster_history_provision_log_path(cluster_name: str) -> Optional[str]:
    """Returns provision_log_path from cluster_history for this name.

    If the cluster currently exists, we use its hash. Otherwise, we look up
    historical rows by name and choose the most recent one based on
    usage_intervals.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # Try current cluster first (fast path)
        cluster_hash = _get_hash_for_existing_cluster(cluster_name)
        if cluster_hash is not None:
            row = session.query(cluster_history_table).filter_by(
                cluster_hash=cluster_hash).first()
            if row is not None:
                return getattr(row, 'provision_log_path', None)

        # Fallback: search history by name and pick the latest by
        # usage_intervals
        rows = session.query(cluster_history_table).filter_by(
            name=cluster_name).all()
        if not rows:
            return None

        def latest_timestamp(usages_bin) -> int:
            try:
                intervals = pickle.loads(usages_bin)
                # intervals: List[Tuple[int, Optional[int]]]
                if not intervals:
                    return -1
                _, end = intervals[-1]
                return end if end is not None else int(time.time())
            except Exception:  # pylint: disable=broad-except
                return -1

        latest_row = max(rows,
                         key=lambda r: latest_timestamp(r.usage_intervals))
        return getattr(latest_row, 'provision_log_path', None)


@_init_db
def set_cluster_info(cluster_name: str, metadata: Dict[str, Any]) -> None:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(cluster_table).filter_by(
            name=cluster_name).update(
                {cluster_table.c.metadata: json.dumps(metadata)})
        session.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


@_init_db
def get_cluster_storage_mounts_metadata(
        cluster_name: str) -> Optional[Dict[str, Any]]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None or row.storage_mounts_metadata is None:
        return None
    return pickle.loads(row.storage_mounts_metadata)


@_init_db
def set_cluster_storage_mounts_metadata(
        cluster_name: str, storage_mounts_metadata: Dict[str, Any]) -> None:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def _get_cluster_usage_intervals(
        cluster_hash: Optional[str]
) -> Optional[List[Tuple[int, Optional[int]]]]:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def _set_cluster_usage_intervals(
        cluster_hash: str, usage_intervals: List[Tuple[int,
                                                       Optional[int]]]) -> None:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def set_owner_identity_for_cluster(cluster_name: str,
                                   owner_identity: Optional[List[str]]) -> None:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def _get_hash_for_existing_cluster(cluster_name: str) -> Optional[str]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None or row.cluster_hash is None:
        return None
    return row.cluster_hash


@_init_db
def get_launched_resources_from_cluster_hash(
        cluster_hash: str) -> Optional[Tuple[int, Any]]:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
@context_utils.cancellation_guard
def get_cluster_from_name(
        cluster_name: Optional[str]) -> Optional[Dict[str, Any]]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(cluster_table).filter_by(name=cluster_name).first()
    if row is None:
        return None
    user_hash = _get_user_hash_or_current_user(row.user_hash)
    user = get_user(user_hash)
    user_name = user.name if user is not None else None
    last_event = get_last_cluster_event(
        row.cluster_hash, event_type=ClusterEventType.STATUS_CHANGE)
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
        'user_name': user_name,
        'config_hash': row.config_hash,
        'workspace': row.workspace,
        'last_creation_yaml': row.last_creation_yaml,
        'last_creation_command': row.last_creation_command,
        'is_managed': bool(row.is_managed),
        'last_event': last_event,
    }

    return record


@_init_db
def get_clusters(
    *,  # keyword only separator
    exclude_managed_clusters: bool = False,
    workspaces_filter: Optional[Set[str]] = None,
    user_hashes_filter: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Get clusters from the database.

    Args:
        exclude_managed_clusters: If True, exclude clusters that have
            is_managed field set to True.
        workspaces_filter: If specified, only include clusters
            that has workspace field set to one of the values.
        user_hashes_filter: If specified, only include clusters
            that has user_hash field set to one of the values.
    """
    # is a cluster has a null user_hash,
    # we treat it as belonging to the current user.
    current_user_hash = common_utils.get_user_hash()
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        query = session.query(cluster_table)
        if exclude_managed_clusters:
            query = query.filter(cluster_table.c.is_managed == int(False))
        if workspaces_filter is not None:
            query = query.filter(
                cluster_table.c.workspace.in_(workspaces_filter))
        if user_hashes_filter is not None:
            if current_user_hash in user_hashes_filter:
                # backwards compatibility for old clusters.
                # If current_user_hash is in user_hashes_filter, we include
                # clusters that have a null user_hash.
                query = query.filter(
                    cluster_table.c.user_hash.in_(user_hashes_filter) |
                    (cluster_table.c.user_hash is None))
            else:
                query = query.filter(
                    cluster_table.c.user_hash.in_(user_hashes_filter))
        query = query.order_by(sqlalchemy.desc(cluster_table.c.launched_at))
        rows = query.all()
    records = []

    # get user hash for each row
    row_to_user_hash = {}
    for row in rows:
        user_hash = (row.user_hash
                     if row.user_hash is not None else current_user_hash)
        row_to_user_hash[row.cluster_hash] = user_hash

    # get all users needed for the rows at once
    user_hashes = set(row_to_user_hash.values())
    user_hash_to_user = _get_users(user_hashes)

    # get last cluster event for each row
    cluster_hashes = set(row_to_user_hash.keys())
    last_cluster_event_dict = _get_last_cluster_event_multiple(
        cluster_hashes, ClusterEventType.STATUS_CHANGE)

    # get user for each row
    for row in rows:
        user_hash = row_to_user_hash[row.cluster_hash]
        user = user_hash_to_user.get(user_hash, None)
        user_name = user.name if user is not None else None
        last_event = last_cluster_event_dict.get(row.cluster_hash, None)
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
            'user_name': user_name,
            'config_hash': row.config_hash,
            'workspace': row.workspace,
            'last_creation_yaml': row.last_creation_yaml,
            'last_creation_command': row.last_creation_command,
            'is_managed': bool(row.is_managed),
            'last_event': last_event,
        }

        records.append(record)
    return records


@_init_db
def get_clusters_from_history(
        days: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get cluster reports from history.

    Args:
        days: If specified, only include historical clusters (those not
              currently active) that were last used within the past 'days'
              days. Active clusters are always included regardless of this
              parameter.

    Returns:
        List of cluster records with history information.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # Explicitly select columns from both tables to avoid ambiguity
        query = session.query(
            cluster_history_table.c.cluster_hash, cluster_history_table.c.name,
            cluster_history_table.c.num_nodes,
            cluster_history_table.c.requested_resources,
            cluster_history_table.c.launched_resources,
            cluster_history_table.c.usage_intervals,
            cluster_history_table.c.user_hash,
            cluster_history_table.c.last_creation_yaml,
            cluster_history_table.c.last_creation_command,
            cluster_history_table.c.workspace.label('history_workspace'),
            cluster_table.c.status, cluster_table.c.workspace,
            cluster_table.c.status_updated_at).select_from(
                cluster_history_table.join(cluster_table,
                                           cluster_history_table.c.cluster_hash
                                           == cluster_table.c.cluster_hash,
                                           isouter=True))

        rows = query.all()

    # Prepare filtering parameters
    cutoff_time = None
    if days is not None:
        cutoff_time = int(time.time()) - (days * 24 * 60 * 60)

    records = []
    for row in rows:
        user_hash = _get_user_hash_or_current_user(row.user_hash)
        launched_at = _get_cluster_launch_time(row.cluster_hash)
        duration = _get_cluster_duration(row.cluster_hash)

        # Parse status
        status = None
        if row.status:
            status = status_lib.ClusterStatus[row.status]

        # Apply filtering: always include active clusters, filter historical
        # ones by time
        if cutoff_time is not None and status is None:  # Historical cluster
            # For historical clusters, check if they were used recently
            # Use the most recent activity from usage_intervals to determine
            # last use
            usage_intervals = []
            if row.usage_intervals:
                try:
                    usage_intervals = pickle.loads(row.usage_intervals)
                except (pickle.PickleError, AttributeError):
                    usage_intervals = []

            # Find the most recent activity time from usage_intervals
            last_activity_time = None
            if usage_intervals:
                # Get the end time of the last interval (or start time if
                # still running)
                last_interval = usage_intervals[-1]
                last_activity_time = (last_interval[1] if last_interval[1]
                                      is not None else last_interval[0])

            # Skip historical clusters that haven't been used recently
            if last_activity_time is None or last_activity_time < cutoff_time:
                continue

        # Parse launched resources safely
        launched_resources = None
        if row.launched_resources:
            try:
                launched_resources = pickle.loads(row.launched_resources)
            except (pickle.PickleError, AttributeError):
                launched_resources = None

        # Parse usage intervals safely
        usage_intervals = []
        if row.usage_intervals:
            try:
                usage_intervals = pickle.loads(row.usage_intervals)
            except (pickle.PickleError, AttributeError):
                usage_intervals = []

        # Get user name from user hash
        user = get_user(user_hash)
        user_name = user.name if user is not None else None
        workspace = (row.history_workspace
                     if row.history_workspace else row.workspace)

        record = {
            'name': row.name,
            'launched_at': launched_at,
            'duration': duration,
            'num_nodes': row.num_nodes,
            'resources': launched_resources,
            'cluster_hash': row.cluster_hash,
            'usage_intervals': usage_intervals,
            'status': status,
            'user_hash': user_hash,
            'user_name': user_name,
            'workspace': workspace,
            'last_creation_yaml': row.last_creation_yaml,
            'last_creation_command': row.last_creation_command,
            'last_event': get_last_cluster_event(
                row.cluster_hash, event_type=ClusterEventType.STATUS_CHANGE),
        }

        records.append(record)

    # sort by launch time, descending in recency
    records = sorted(records, key=lambda record: -(record['launched_at'] or 0))
    return records


@_init_db
def get_cluster_names_start_with(starts_with: str) -> List[str]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(cluster_table.c.name).filter(
            cluster_table.c.name.like(f'{starts_with}%')).all()
    return [row[0] for row in rows]


@_init_db
def get_cached_enabled_clouds(cloud_capability: 'cloud.CloudCapability',
                              workspace: str) -> List['clouds.Cloud']:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def set_enabled_clouds(enabled_clouds: List[str],
                       cloud_capability: 'cloud.CloudCapability',
                       workspace: str) -> None:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def get_allowed_clouds(workspace: str) -> List[str]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(config_table).filter_by(
            key=_get_allowed_clouds_key(workspace)).first()
    if row:
        return json.loads(row.value)
    return []


@_init_db
def set_allowed_clouds(allowed_clouds: List[str], workspace: str) -> None:
    assert _SQLALCHEMY_ENGINE is not None
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
            key=_get_allowed_clouds_key(workspace),
            value=json.dumps(allowed_clouds))
        do_update_stmt = insert_stmnt.on_conflict_do_update(
            index_elements=[config_table.c.key],
            set_={config_table.c.value: json.dumps(allowed_clouds)})
        session.execute(do_update_stmt)
        session.commit()


def _get_allowed_clouds_key(workspace: str) -> str:
    return _ALLOWED_CLOUDS_KEY_PREFIX + workspace


@_init_db
def add_or_update_storage(storage_name: str,
                          storage_handle: 'Storage.StorageMetadata',
                          storage_status: status_lib.StorageStatus):
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def remove_storage(storage_name: str):
    """Removes Storage from Database"""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(storage_table).filter_by(name=storage_name).delete()
        session.commit()


@_init_db
def set_storage_status(storage_name: str,
                       status: status_lib.StorageStatus) -> None:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(storage_table).filter_by(
            name=storage_name).update({storage_table.c.status: status.value})
        session.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Storage {storage_name} not found.')


@_init_db
def get_storage_status(storage_name: str) -> Optional[status_lib.StorageStatus]:
    assert _SQLALCHEMY_ENGINE is not None
    assert storage_name is not None, 'storage_name cannot be None'
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(storage_table).filter_by(name=storage_name).first()
    if row:
        return status_lib.StorageStatus[row.status]
    return None


@_init_db
def set_storage_handle(storage_name: str,
                       handle: 'Storage.StorageMetadata') -> None:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(storage_table).filter_by(
            name=storage_name).update(
                {storage_table.c.handle: pickle.dumps(handle)})
        session.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Storage{storage_name} not found.')


@_init_db
def get_handle_from_storage_name(
        storage_name: Optional[str]) -> Optional['Storage.StorageMetadata']:
    assert _SQLALCHEMY_ENGINE is not None
    if storage_name is None:
        return None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(storage_table).filter_by(name=storage_name).first()
    if row:
        return pickle.loads(row.handle)
    return None


@_init_db
def get_glob_storage_name(storage_name: str) -> List[str]:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def get_storage_names_start_with(starts_with: str) -> List[str]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(storage_table).filter(
            storage_table.c.name.like(f'{starts_with}%')).all()
    return [row.name for row in rows]


@_init_db
def get_storage() -> List[Dict[str, Any]]:
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def get_volume_names_start_with(starts_with: str) -> List[str]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(volume_table).filter(
            volume_table.c.name.like(f'{starts_with}%')).all()
    return [row.name for row in rows]


@_init_db
def get_volumes() -> List[Dict[str, Any]]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(volume_table).all()
    records = []
    for row in rows:
        records.append({
            'name': row.name,
            'launched_at': row.launched_at,
            'handle': pickle.loads(row.handle),
            'user_hash': row.user_hash,
            'workspace': row.workspace,
            'last_attached_at': row.last_attached_at,
            'last_use': row.last_use,
            'status': status_lib.VolumeStatus[row.status],
        })
    return records


@_init_db
def get_volume_by_name(name: str) -> Optional[Dict[str, Any]]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(volume_table).filter_by(name=name).first()
    if row:
        return {
            'name': row.name,
            'launched_at': row.launched_at,
            'handle': pickle.loads(row.handle),
            'user_hash': row.user_hash,
            'workspace': row.workspace,
            'last_attached_at': row.last_attached_at,
            'last_use': row.last_use,
            'status': status_lib.VolumeStatus[row.status],
        }
    return None


@_init_db
def add_volume(name: str, config: models.VolumeConfig,
               status: status_lib.VolumeStatus) -> None:
    assert _SQLALCHEMY_ENGINE is not None
    volume_launched_at = int(time.time())
    handle = pickle.dumps(config)
    last_use = common_utils.get_current_command()
    user_hash = common_utils.get_current_user().id
    active_workspace = skypilot_config.get_active_workspace()

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')
        insert_stmnt = insert_func(volume_table).values(
            name=name,
            launched_at=volume_launched_at,
            handle=handle,
            user_hash=user_hash,
            workspace=active_workspace,
            last_attached_at=None,
            last_use=last_use,
            status=status.value,
        )
        do_update_stmt = insert_stmnt.on_conflict_do_nothing()
        session.execute(do_update_stmt)
        session.commit()


@_init_db
def update_volume(name: str, last_attached_at: int,
                  status: status_lib.VolumeStatus) -> None:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(volume_table).filter_by(name=name).update({
            volume_table.c.last_attached_at: last_attached_at,
            volume_table.c.status: status.value,
        })
        session.commit()


@_init_db
def update_volume_status(name: str, status: status_lib.VolumeStatus) -> None:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(volume_table).filter_by(name=name).update({
            volume_table.c.status: status.value,
        })
        session.commit()


@_init_db
def delete_volume(name: str) -> None:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(volume_table).filter_by(name=name).delete()
        session.commit()


@_init_db
def get_ssh_keys(user_hash: str) -> Tuple[str, str, bool]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(ssh_key_table).filter_by(
            user_hash=user_hash).first()
    if row:
        return row.ssh_public_key, row.ssh_private_key, True
    return '', '', False


@_init_db
def set_ssh_keys(user_hash: str, ssh_public_key: str, ssh_private_key: str):
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def add_service_account_token(token_id: str,
                              token_name: str,
                              token_hash: str,
                              creator_user_hash: str,
                              service_account_user_id: str,
                              expires_at: Optional[int] = None) -> None:
    """Add a service account token to the database."""
    assert _SQLALCHEMY_ENGINE is not None
    created_at = int(time.time())

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')

        insert_stmnt = insert_func(service_account_token_table).values(
            token_id=token_id,
            token_name=token_name,
            token_hash=token_hash,
            created_at=created_at,
            expires_at=expires_at,
            creator_user_hash=creator_user_hash,
            service_account_user_id=service_account_user_id)
        session.execute(insert_stmnt)
        session.commit()


@_init_db
def get_service_account_token(token_id: str) -> Optional[Dict[str, Any]]:
    """Get a service account token by token_id."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(service_account_token_table).filter_by(
            token_id=token_id).first()
    if row is None:
        return None
    return {
        'token_id': row.token_id,
        'token_name': row.token_name,
        'token_hash': row.token_hash,
        'created_at': row.created_at,
        'last_used_at': row.last_used_at,
        'expires_at': row.expires_at,
        'creator_user_hash': row.creator_user_hash,
        'service_account_user_id': row.service_account_user_id,
    }


@_init_db
def get_user_service_account_tokens(user_hash: str) -> List[Dict[str, Any]]:
    """Get all service account tokens for a user (as creator)."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(service_account_token_table).filter_by(
            creator_user_hash=user_hash).all()
    return [{
        'token_id': row.token_id,
        'token_name': row.token_name,
        'token_hash': row.token_hash,
        'created_at': row.created_at,
        'last_used_at': row.last_used_at,
        'expires_at': row.expires_at,
        'creator_user_hash': row.creator_user_hash,
        'service_account_user_id': row.service_account_user_id,
    } for row in rows]


@_init_db
def update_service_account_token_last_used(token_id: str) -> None:
    """Update the last_used_at timestamp for a service account token."""
    assert _SQLALCHEMY_ENGINE is not None
    last_used_at = int(time.time())

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(service_account_token_table).filter_by(
            token_id=token_id).update(
                {service_account_token_table.c.last_used_at: last_used_at})
        session.commit()


@_init_db
def delete_service_account_token(token_id: str) -> bool:
    """Delete a service account token.

    Returns:
        True if token was found and deleted.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.query(service_account_token_table).filter_by(
            token_id=token_id).delete()
        session.commit()
    return result > 0


@_init_db
def rotate_service_account_token(token_id: str,
                                 new_token_hash: str,
                                 new_expires_at: Optional[int] = None) -> None:
    """Rotate a service account token by updating its hash and expiration.

    Args:
        token_id: The token ID to rotate.
        new_token_hash: The new hashed token value.
        new_expires_at: New expiration timestamp, or None for no expiration.
    """
    assert _SQLALCHEMY_ENGINE is not None
    current_time = int(time.time())

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        count = session.query(service_account_token_table).filter_by(
            token_id=token_id
        ).update({
            service_account_token_table.c.token_hash: new_token_hash,
            service_account_token_table.c.expires_at: new_expires_at,
            service_account_token_table.c.last_used_at: None,  # Reset last used
            # Update creation time
            service_account_token_table.c.created_at: current_time,
        })
        session.commit()

    if count == 0:
        raise ValueError(f'Service account token {token_id} not found.')


@_init_db
def get_cluster_yaml_str(cluster_yaml_path: Optional[str]) -> Optional[str]:
    """Get the cluster yaml from the database or the local file system.
    If the cluster yaml is not in the database, check if it exists on the
    local file system and migrate it to the database.

    It is assumed that the cluster yaml file is named as <cluster_name>.yml.
    """
    assert _SQLALCHEMY_ENGINE is not None
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
    return yaml_utils.safe_load(yaml_str)


@_init_db
def set_cluster_yaml(cluster_name: str, yaml_str: str) -> None:
    """Set the cluster yaml in the database."""
    assert _SQLALCHEMY_ENGINE is not None
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


@_init_db
def remove_cluster_yaml(cluster_name: str):
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(cluster_yaml_table).filter_by(
            cluster_name=cluster_name).delete()
        session.commit()


@_init_db
def get_all_service_account_tokens() -> List[Dict[str, Any]]:
    """Get all service account tokens across all users (for admin access)."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.query(service_account_token_table).all()
    return [{
        'token_id': row.token_id,
        'token_name': row.token_name,
        'token_hash': row.token_hash,
        'created_at': row.created_at,
        'last_used_at': row.last_used_at,
        'expires_at': row.expires_at,
        'creator_user_hash': row.creator_user_hash,
        'service_account_user_id': row.service_account_user_id,
    } for row in rows]


@_init_db
def get_system_config(config_key: str) -> Optional[str]:
    """Get a system configuration value by key."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        row = session.query(system_config_table).filter_by(
            config_key=config_key).first()
    if row is None:
        return None
    return row.config_value


@_init_db
def set_system_config(config_key: str, config_value: str) -> None:
    """Set a system configuration value."""
    assert _SQLALCHEMY_ENGINE is not None
    current_time = int(time.time())

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')

        insert_stmnt = insert_func(system_config_table).values(
            config_key=config_key,
            config_value=config_value,
            created_at=current_time,
            updated_at=current_time)

        upsert_stmnt = insert_stmnt.on_conflict_do_update(
            index_elements=[system_config_table.c.config_key],
            set_={
                system_config_table.c.config_value: config_value,
                system_config_table.c.updated_at: current_time,
            })
        session.execute(upsert_stmnt)
        session.commit()
