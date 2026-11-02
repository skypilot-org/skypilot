"""SQLAlchemy schema and DB manager for global user state.

Server-only. Imported by ``sky.global_user_state`` when SQLAlchemy is available
(``skypilot[server]`` or a cloud extra). Keep table definitions here at column 0
so schema edits do not re-indent the parent module."""
# pylint: disable=line-too-long
from __future__ import annotations

import re

from sky.skylet import constants
from sky.utils import annotations as annotation_utils
from sky.utils import common_utils
from sky.utils.db import db_utils
from sky.utils.db import migration_utils

from sky.utils.db import sqlalchemy_deps

sqlalchemy_deps.require_sqlalchemy()

import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext import asyncio as sql_async
from sqlalchemy.ext import declarative

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
    sqlalchemy.Column('type', sqlalchemy.Text, server_default=None),
    # User-set default workspace; null when unset. Resolution and RBAC
    # validation are handled in sky/workspaces/; this column is the
    # persisted value only.
    sqlalchemy.Column('preferred_workspace',
                      sqlalchemy.Text,
                      server_default=None),
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
    sqlalchemy.Column('cluster_ever_up',
                      sqlalchemy.Integer,
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
    # Released versions keep their (port, pid) skylet tunnel tuple here and
    # share the database with this version during a rolling update.
    sqlalchemy.Column('skylet_ssh_tunnel_metadata',
                      sqlalchemy.LargeBinary,
                      server_default=None),
    # JSON {owner_id: [port, pid]} of the skylet tunnels opened by each API
    # server host.
    sqlalchemy.Column('skylet_ssh_tunnels',
                      sqlalchemy.JSON(none_as_null=True),
                      server_default=None),
    # Infrastructure columns for efficient filtering
    sqlalchemy.Column('cloud', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('region', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('zone', sqlalchemy.Text, server_default=None),
    # Node names for dashboard display (comma-separated)
    sqlalchemy.Column('node_names', sqlalchemy.Text, server_default=None),
    # External links for dashboard display, e.g. cloud-provider instance
    # console URLs generated at launch time. Same shape as the `links` field
    # on managed-job rows: a JSON object mapping {label: url}.
    sqlalchemy.Column('links', sqlalchemy.JSON, server_default=None),
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
    sqlalchemy.Column('is_ephemeral',
                      sqlalchemy.Integer,
                      server_default='0'),
    sqlalchemy.Column('error_message', sqlalchemy.Text,
                      server_default=None),
    # JSON-encoded lists of pods/clusters using the volume
    sqlalchemy.Column('usedby_pods', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('usedby_clusters',
                      sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('creation_yaml', sqlalchemy.Text,
                      server_default=None),
    # Set only while the volume is being resized to a size it does not have
    # yet; `handle`'s size stays the capacity that exists. See
    # models.VolumeResizeStatus.
    sqlalchemy.Column('resize_status', sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('resize_target_size',
                      sqlalchemy.Text,
                      server_default=None),
    # What the cloud said about the resize, in its own words. What is shown to
    # the user is built from this in volume_list, not stored.
    sqlalchemy.Column('resize_message',
                      sqlalchemy.Text,
                      server_default=None),
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
    sqlalchemy.Column('last_activity_time',
                      sqlalchemy.Integer,
                      server_default=None,
                      index=True),
    sqlalchemy.Column('launched_at',
                      sqlalchemy.Integer,
                      server_default=None,
                      index=True),
    # Infrastructure columns for efficient filtering
    sqlalchemy.Column('cloud', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('region', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('zone', sqlalchemy.Text, server_default=None),
    # Node names for dashboard display (comma-separated)
    sqlalchemy.Column('node_names', sqlalchemy.Text, server_default=None),
    # Whether the cluster was launched by a controller (managed job or
    # service). Mirrors the `is_managed` column on the clusters table so that
    # history queries (e.g. the dashboard's cost report) can filter out
    # controller-backed clusters even after they are terminated, since at that
    # point the clusters table row is gone and the join can no longer supply
    # the flag.
    sqlalchemy.Column('is_managed', sqlalchemy.Integer, server_default='0'),
)

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
    sqlalchemy.Column('transitioned_at',
                      sqlalchemy.Integer,
                      primary_key=True),
    sqlalchemy.Column('type', sqlalchemy.Text),
    sqlalchemy.Column('request_id', sqlalchemy.Text, server_default=None),
    # The primary key is (cluster_hash, reason, transitioned_at), but the two
    # readers that have to survive a cluster's teardown look events up by
    # `name` instead -- see get_latest_cluster_events and
    # get_cluster_events_by_name. Without this they scan the whole table.
    sqlalchemy.Index('ix_cluster_events_name_type', 'name', 'type',
                     'transitioned_at'),
)

# One row per provisioning attempt, recording the milestones a launch passes
# through so launch latency can be broken down after the fact.
#
# Why a table rather than in-memory timers: a launch that parks on an external
# condition raises ExecutionPausedError, which unwinds bulk_provision entirely
# and resumes as a fresh call in a possibly different executor worker.
# Milestones held in memory do not survive that.
#
# Every segment is a subtraction between two persisted timestamps, so whoever
# closes a segment can read the opening one back instead of carrying state.
#
# attempt_id is minted per real provisioning attempt (one per failover
# iteration). A pause/resume continues the existing row -- the resources are
# kept, not torn down, so it is one attempt.

# Attempts the metrics daemon still has to turn into observations: finished,
# and not yet claimed. Written once and used twice -- as the claim query's
# WHERE and as its index's predicate -- because PostgreSQL matches a partial
# index only when the two agree, and two hand-kept copies of a predicate are
# how they come to disagree.
UNOBSERVED_ATTEMPT_PREDICATE = ('outcome IS NOT NULL AND '
                                'metrics_observed_at IS NULL')

launch_attempt_table = sqlalchemy.Table(
    'launch_attempts',
    Base.metadata,
    sqlalchemy.Column('attempt_id', sqlalchemy.Text, primary_key=True),
    # Not a key: failover reuses the hash (the clusters row is kept), while a
    # teardown + relaunch mints a new one. Neither per-attempt nor per-job
    # stable -- see attempt_seq for ordering.
    sqlalchemy.Column('cluster_hash', sqlalchemy.Text),
    # Display only. A name outlives the cluster it named, so it must never be
    # used to group attempts or to find a row to resume.
    sqlalchemy.Column('cluster_name', sqlalchemy.Text),
    # Recorded so that provisioning code holding only the on-cloud name (the
    # one stamped on pods) can stamp a milestone without resolving it back.
    sqlalchemy.Column('cluster_name_on_cloud',
                      sqlalchemy.Text,
                      server_default=None),
    # The request whose execution opened this attempt. This is what makes
    # resuming exact rather than a guess: a pause re-queues the *same* request,
    # so an open row under the same request_id is this launch resuming, while a
    # row left behind by a crashed earlier launch carries a different one and
    # is never adopted.
    sqlalchemy.Column('request_id', sqlalchemy.Text, server_default=None),
    # Monotonic within cluster_name. See open_launch_attempt for why the name
    # rather than the hash.
    sqlalchemy.Column('attempt_seq', sqlalchemy.Integer),
    # Recorded on the row rather than joined from the clusters table, which is
    # deleted on teardown -- the attempt outlives the cluster it provisioned.
    sqlalchemy.Column('workspace', sqlalchemy.Text, server_default=None),
    # The queue name recorded by record_launch_queue_for_cluster, if any.
    # NULL otherwise. Used only to label the admission wait.
    sqlalchemy.Column('queue', sqlalchemy.Text, server_default=None),
    # Milestones, epoch seconds. Named cloud-agnostically: on Kubernetes
    # instances_requested is pod creation and instances_ready is all pods
    # running; on VM clouds they are the create call and the instances being
    # up. admitted stays NULL unless LaunchMilestone.ADMITTED is stamped.
    sqlalchemy.Column('provision_start', sqlalchemy.Float),
    sqlalchemy.Column('instances_requested',
                      sqlalchemy.Float,
                      server_default=None),
    sqlalchemy.Column('admitted', sqlalchemy.Float, server_default=None),
    sqlalchemy.Column('instances_ready',
                      sqlalchemy.Float,
                      server_default=None),
    # NULL while in flight. 'succeeded' | 'failed' | 'abandoned', where
    # abandoned means the writing process died and the startup sweep closed the
    # row -- only that case counts as a lost metric.
    sqlalchemy.Column('outcome', sqlalchemy.Text, server_default=None),
    # Set when the segments of this row have been turned into metric
    # observations, so a row is never observed twice.
    sqlalchemy.Column('metrics_observed_at',
                      sqlalchemy.Float,
                      server_default=None),
    # Resume lookup, attempt_seq allocation, the in-flight milestone lookup,
    # and the per-cluster timeline. cluster_hash is not indexed: a timeline
    # scoped to one incarnation filters these few rows by hash.
    sqlalchemy.Index('ix_launch_attempts_cluster', 'cluster_name',
                     'attempt_seq'),
    # Retention sweep. Without it the sweep full-scans on every tick.
    sqlalchemy.Index('ix_launch_attempts_provision_start',
                     'provision_start'),
    # The milestone writers hold the on-cloud name (it is what is stamped on
    # the pods) and look up the in-flight attempt by either name. Without this
    # that branch of the OR cannot be indexed, so the whole lookup degrades to
    # a table scan: 21ms against 4us over 200k rows, several times per launch,
    # growing with the retention window. The extra write cost is ~2us per
    # insert, and there is one insert per attempt against several lookups.
    sqlalchemy.Index('ix_launch_attempts_cluster_on_cloud',
                     'cluster_name_on_cloud'),
    # The abandoned sweep, which runs once a minute and in the steady state
    # finds nothing. On provision_start alone it still had to read every row
    # older than the bound -- in the steady state, nearly the table -- before
    # discovering that none were open. Leading with outcome makes it a seek
    # into the few in-flight rows: 120ms against nothing measurable over 200k
    # rows, once a minute, inside a write transaction that blocks other
    # writers for its duration.
    sqlalchemy.Index('ix_launch_attempts_open', 'outcome',
                     'provision_start'),
    # The metrics daemon's claim, also once a minute. Its predicate is
    # "closed but not yet observed", and `ix_launch_attempts_open` serves only
    # the first half: leading on `outcome IS NOT NULL` is nearly the whole
    # table in the steady state, every row of which is then filtered on
    # metrics_observed_at.
    #
    # Partial, and that is the point: rows leave this index as they are
    # observed, so in the steady state it holds only the last minute's work
    # rather than the job history. The predicate has to be repeated in the
    # index for PostgreSQL to match it to the query.
    sqlalchemy.Index(
        'ix_launch_attempts_unobserved',
        'provision_start',
        postgresql_where=sqlalchemy.text(UNOBSERVED_ATTEMPT_PREDICATE),
        sqlite_where=sqlalchemy.text(UNOBSERVED_ATTEMPT_PREDICATE)),
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
    # Indexed + unique: the auth middleware looks up rows by hash on every
    # request to enforce revocation/rotation/expiration.
    sqlalchemy.Column('token_hash',
                      sqlalchemy.Text,
                      index=True,
                      unique=True),
    sqlalchemy.Column('created_at', sqlalchemy.Integer),
    sqlalchemy.Column('last_used_at',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('expires_at', sqlalchemy.Integer,
                      server_default=None),
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

# The task YAML and entrypoint command of each job submitted to a cluster
# (`sky launch` / `sky exec`), for showing a job's spec on the dashboard.
#
# Kept on the API server rather than in the cluster's skylet job table so that
# reading one job's spec does not go through the cluster (the skylet's job
# queue returns every job's row at once), and so that adding it needs no change
# to the skylet's schema, which older skylet versions write to positionally.
#
# Keyed by cluster_hash rather than name: job ids restart at 1 when a cluster
# with the same name is relaunched after `sky down`. Rows are dropped when the
# cluster is terminated, and only the last MAX_JOB_SPECS_PER_CLUSTER job ids of
# a cluster are kept, so a long-lived cluster that runs many `sky exec`s does
# not grow the table without bound.
cluster_job_spec_table = sqlalchemy.Table(
    'cluster_job_specs',
    Base.metadata,
    sqlalchemy.Column('cluster_hash', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('job_id', sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column('task_yaml', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('entrypoint', sqlalchemy.Text, server_default=None),
)
# A task YAML is typically a few KB, so this is a few MB per cluster at most.
MAX_JOB_SPECS_PER_CLUSTER = 1000

# Renewable leases for leader election across server processes. Each row is
# one singleton role: ``holder`` is the current leader's identity, ``epoch`` is
# a monotonic fencing token bumped on every change of holder, and
# ``expires_at`` is the server-side deadline by which the holder must renew or
# be taken over. See ``sky.utils.leader_election.PgLeaseElector``.
leader_leases_table = sqlalchemy.Table(
    'leader_leases',
    Base.metadata,
    sqlalchemy.Column('lock_id', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('holder', sqlalchemy.Text),
    sqlalchemy.Column('epoch', sqlalchemy.BigInteger),
    sqlalchemy.Column('expires_at', sqlalchemy.DateTime(timezone=True)),
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

@annotation_utils.lru_cache(scope='global', maxsize=1)
def _sqlite_supports_returning() -> bool:
    """Check if SQLite (3.35.0+) and SQLAlchemy (2.0+) support RETURNING.

    See https://sqlite.org/lang_returning.html and
    https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#insert-update-delete-returning  # pylint: disable=line-too-long
    """
    sqlalchemy_version_parts = sqlalchemy.__version__.split('.')
    assert len(sqlalchemy_version_parts) >= 1, \
        f'Invalid SQLAlchemy version: {sqlalchemy.__version__}'
    sqlalchemy_major = int(sqlalchemy_version_parts[0])
    if sqlalchemy_major < 2:
        return False

    engine = _db_manager.get_engine()
    if engine.dialect.name != db_utils.SQLAlchemyDialect.SQLITE.value:
        return False
    with orm.Session(engine) as session:
        result = session.execute(sqlalchemy.text('SELECT sqlite_version()'))
        version_str = result.scalar()
        version_parts = version_str.split('.')
        assert len(version_parts) >= 2, \
            f'Invalid version string: {version_str}'
        major, minor = int(version_parts[0]), int(version_parts[1])
        return (major > 3) or (major == 3 and minor >= 35)

@annotation_utils.lru_cache(scope='global', maxsize=1)
def _supports_returning() -> bool:
    """Whether this backend can return the rows a statement just changed.

    Not the same question as `_sqlite_supports_returning`, which answers "is
    this SQLite, and new enough" and so returns False on PostgreSQL -- where
    RETURNING has existed since 8.2. Asking that one here would send every
    PostgreSQL deployment, the ones with the tables large enough for this to
    matter, down the fallback.

    Only SQLite needs asking, and only about its own version: RETURNING there
    arrived in 3.35, and SQLAlchemy exposes it from 2.0, which
    `dependencies.py` already requires.

    True for anything else because `SQLAlchemyDialect` models exactly two
    backends -- not because not-SQLite implies RETURNING. A third would fail
    loudly here, with a CompileError out of the claim, and would have to be
    taught the other dialect branches in this module first; this is one of the
    places to look when adding one.
    """
    engine = _db_manager.get_engine()
    if engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value:
        return _sqlite_supports_returning()
    return True

_db_manager = db_utils.DatabaseManager(
    'state',
    create_table,
    post_init_fn=lambda _: _sqlite_supports_returning())
initialize_and_get_db = _db_manager.get_engine
