"""SQLAlchemy schema/DB manager for managed jobs (server-only).

Imported by ``sky.jobs.state`` when SQLAlchemy is available. Tables stay at
column 0 so schema edits do not re-indent the parent module.
"""
# pylint: disable=line-too-long,unused-import
from __future__ import annotations

from typing import Any, List, Optional, Set

import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext import asyncio as sql_async
from sqlalchemy.ext import declarative

from sky.dag import DagExecution
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils.db import db_utils
from sky.utils.db import migration_utils
from sky.utils.db import sqlalchemy_deps

# Re-exported for sky.jobs.state (annotations / dialect helpers).
_ = (postgresql, sqlite, sql_async)

sqlalchemy_deps.require_sqlalchemy()

Base = declarative.declarative_base()

# === Database schema ===
# `spot` table contains all the finest-grained tasks, including all the
# tasks of a managed job (called spot for legacy reason, as it is generalized
# from the previous managed spot jobs). All tasks of the same job will have the
# same `spot_job_id`.
# The `job_name` column is now deprecated. It now holds the task's name, i.e.,
# the same content as the `task_name` column.
# The `job_id` is now not really a job id, but a only a unique
# identifier/primary key for all the tasks. We will use `spot_job_id`
# to identify the job.
# TODO(zhwu): schema migration may be needed.

# The daemon's two "not processed yet" predicates, used by both the index
# definitions and the migration so an index cannot drift from the query it
# exists to serve.
#
# Both are built from _MEASURABLE rather than spelled out, because the first
# version of this spelled them out and the second one lost a clause: without
# the origin columns the never-ran index kept every pre-upgrade row forever --
# 1078 unmatchable rows against 0 real candidates on the tenant it was measured
# on, which is the very thing partial indexes were introduced here to stop.
#
# Each predicate must stay implied by its query's WHERE or the planner cannot
# use the index. Every clause here is one the corresponding query already has.
_MEASURABLE = 'created_at IS NOT NULL AND eligible_at IS NOT NULL'

PENDING_TIMELINE_PREDICATE = f't_time_to_running IS NULL AND {_MEASURABLE}'
NEVER_RAN_PREDICATE = (f't_controller_queue IS NULL AND start_at IS NULL AND '
                       f'{_MEASURABLE}')

# A task the scheduler has claimed that has neither started nor finished. Used
# three ways -- the partial index below, its migration, and the WHERE of the
# stall scan in sky/jobs/stall.py -- so the query cannot drift from the index
# that serves it even by editing one of them. The two above are shared by two
# places each; this one closes the remaining gap.
#
# It holds in-flight claimed work only: a row leaves the moment it starts or
# ends, so the index tracks what is outstanding rather than job history.
CLAIMED_IN_FLIGHT_PREDICATE = ('submitted_at IS NOT NULL AND '
                               'start_at IS NULL AND end_at IS NULL')

# Claimed and not finished, whether or not it has started: what a controller
# process is holding. Deliberately one predicate wider than the one above --
# a running job still occupies its slot -- so it cannot share that index.
CLAIMED_LIVE_PREDICATE = 'submitted_at IS NOT NULL AND end_at IS NULL'

spot_table = sqlalchemy.Table(
    'spot',
    Base.metadata,
    sqlalchemy.Column('job_id',
                      sqlalchemy.Integer,
                      primary_key=True,
                      autoincrement=True),
    sqlalchemy.Column('job_name', sqlalchemy.Text),
    sqlalchemy.Column('resources', sqlalchemy.Text),
    sqlalchemy.Column('submitted_at', sqlalchemy.Float),
    # Indexed because non-terminal-status filtering on this column is on the
    # hot path for the pool dashboard (per-pool job listing) and skip_finished
    # queries; without it the filter is a full table scan over all (including
    # finished) tasks.
    sqlalchemy.Column('status', sqlalchemy.Text, index=True),
    sqlalchemy.Column('run_timestamp', sqlalchemy.Text),
    sqlalchemy.Column('start_at', sqlalchemy.Float, server_default=None),
    sqlalchemy.Column('end_at', sqlalchemy.Float, server_default=None),
    sqlalchemy.Column('last_recovered_at',
                      sqlalchemy.Float,
                      server_default='-1'),
    sqlalchemy.Column('recovery_count', sqlalchemy.Integer, server_default='0'),
    sqlalchemy.Column('job_duration', sqlalchemy.Float, server_default='0'),
    sqlalchemy.Column('failure_reason', sqlalchemy.Text),
    sqlalchemy.Column('spot_job_id', sqlalchemy.Integer, index=True),
    sqlalchemy.Column('task_id', sqlalchemy.Integer, server_default='0'),
    sqlalchemy.Column('task_name', sqlalchemy.Text),
    sqlalchemy.Column('specs', sqlalchemy.Text),
    sqlalchemy.Column('local_log_file', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('metadata', sqlalchemy.Text, server_default='{}'),
    sqlalchemy.Column('links', sqlalchemy.JSON, server_default=None),
    sqlalchemy.Column('logs_cleaned_at', sqlalchemy.Float, server_default=None),
    sqlalchemy.Column('full_resources', sqlalchemy.JSON, server_default=None),
    # Whether this task is a primary task (True) or auxiliary task (False)
    # within a job group. NULL for non-job-group jobs (single jobs/pipelines).
    # Auxiliary tasks are terminated when all primary tasks complete.
    sqlalchemy.Column('is_primary_in_job_group',
                      sqlalchemy.Boolean,
                      server_default=None),
    # For a RECOVERING task, whether the current recovery episode carries
    # "failure credit": TRUE iff a genuine preemption/failure (recovery
    # source FAILURE) is involved in this episode; FALSE for purely
    # system-driven episodes (EMERGENCY / RESTART). Deliberately a separate
    # fact from the per-occurrence causes in job_events.recovery_source: a
    # system-driven interruption of a failure recovery neither grants nor
    # erases the credit. Set when the episode opens, cleared (NULL) on every
    # exit from recovery, and read by set_recovered_async to decide whether
    # the completed episode increments recovery_count (NULL — rows written
    # before this column existed — is treated as credited for back-compat).
    sqlalchemy.Column('recovering_from_failure',
                      sqlalchemy.Boolean,
                      server_default=None),
    # Optional plugin-provided override for the user-facing status. The core
    # state machine never reads this column; it always uses `status`. Read
    # paths (status counts, status filter, returned status) may surface this
    # value instead of `status` via the optional `status_expr` seam, without
    # altering the underlying job lifecycle. NULL means "no override".
    sqlalchemy.Column('status_override', sqlalchemy.Text, server_default=None),
    # When the job was accepted, as epoch seconds. T0 of the launch timeline.
    #
    # A separate column rather than reusing the PENDING job_events row, whose
    # timestamp is written as a naive local datetime while every other
    # timestamp here is time.time(). Subtracting the two is wrong by the UTC
    # offset on any non-UTC deployment and can come out negative -- and a
    # negative observation lands silently in a histogram's lowest bucket, so
    # the metric would look healthy while being wrong.
    sqlalchemy.Column('created_at', sqlalchemy.Float, server_default=None),
    # When this task could first have started, as epoch seconds. The origin the
    # startup breakdown is measured from.
    #
    # Equal to created_at for a single task and for every task of a job group,
    # whose tasks all begin waiting together. A pipeline runs its tasks one
    # after another, so task N is not waiting on anything of ours until task
    # N-1 finishes -- measuring from submission would fold every upstream
    # task's runtime into t_controller_queue, which claims to mean "the
    # scheduler was saturated". Unbounded, and indistinguishable downstream:
    # the histogram is labelled by workspace only.
    #
    # Recording the moment rather than special-casing the formula keeps one
    # meaning for every task.
    #
    # Written on the consolidation path only: of the three set_pending callers,
    # just the one in jobs/server/core.py passes it -- the skylet service and
    # the generated remote-controller code do not. So on a remote controller
    # this is NULL for every task and the correction above does not apply
    # there. Deliberate rather than overlooked: that path keeps its own
    # database, the metrics daemon never reads these rows, and the breakdown is
    # scoped to consolidation. Readers require it NOT NULL, so a row without it
    # is skipped rather than measured from the wrong origin.
    sqlalchemy.Column('eligible_at', sqlalchemy.Float, server_default=None),
    # The launch timeline, denormalized once the job first reaches RUNNING, so
    # the jobs list renders from one indexed row read instead of a per-job scan
    # of launch_attempts.
    #
    # Only two of these are read today. t_time_to_running and
    # t_controller_queue are the conditional-UPDATE targets that make recording
    # exactly-once across processes, one for a task that ran and one for a task
    # that never did. The other six have no reader: the Prometheus series are
    # observed from the in-memory breakdown in the same call that computes it,
    # not from these columns. They are stored for the job-detail view, which
    # lands separately -- so if that view is dropped, these should go with it
    # rather than linger as a table that nothing consults.
    #
    # Write-once: they describe how long the job took to
    # start, which a preemption three hours later does not redefine.
    #
    # Together they partition the wall clock from created_at to RUNNING:
    #   controller_queue  accepted -> a controller claimed the job
    #   retry_overhead    launch attempts that were thrown away, plus backoff
    #   provision_setup   the final attempt's provision start -> instances asked
    #   queue_wait        asked for -> admitted by an external scheduler
    #   node_startup      admitted -> instances up
    #   runtime_setup     instances up -> the job is RUNNING
    sqlalchemy.Column('t_controller_queue',
                      sqlalchemy.Float,
                      server_default=None),
    sqlalchemy.Column('t_retry_overhead', sqlalchemy.Float,
                      server_default=None),
    # Time that belongs to no phase we can name: a job placed on a warm pool
    # never provisions, and one launched before these milestones has no
    # attempt to break down. Kept apart from retry_overhead so such a job does
    # not read as "99% retried launches", which is the misdiagnosis this
    # breakdown exists to prevent.
    sqlalchemy.Column('t_unattributed', sqlalchemy.Float, server_default=None),
    sqlalchemy.Column('t_provision_setup',
                      sqlalchemy.Float,
                      server_default=None),
    sqlalchemy.Column('t_queue_wait', sqlalchemy.Float, server_default=None),
    sqlalchemy.Column('t_node_startup', sqlalchemy.Float, server_default=None),
    sqlalchemy.Column('t_runtime_setup', sqlalchemy.Float, server_default=None),
    # The headline: accepted -> RUNNING. Measured directly rather than summed,
    # because histogram quantiles are not additive.
    sqlalchemy.Column('t_time_to_running',
                      sqlalchemy.Float,
                      server_default=None),
    # The metrics daemon's pending-timeline query, once a minute. Without it
    # that query full-scans spot and sorts the result to return the handful of
    # jobs that just started -- and in the steady state, to return nothing.
    # The predicate selects the rows without a timeline; start_at then serves
    # the ordering from the index rather than a temp b-tree.
    #
    # Partial, and that is the whole point rather than a refinement. A full
    # index stores one entry per task ever run and keeps it forever: a
    # pre-upgrade row has no created_at, so it can never satisfy the query,
    # is never given a timeline, and never leaves the unprocessed group it
    # sorts to the front of. Every tick then walks the entire history to reach
    # the few rows with work in them -- 9156 dead entries against 9 live ones
    # on the dev tenant this was measured on. Restricted to what the query can
    # return, the index holds the pending work instead, and a row drops out of
    # it the moment its timeline is recorded.
    #
    # Safe to restrict because the marker column is only ever asked IS NULL --
    # both selects and both conditional updates -- so no query wants the rows
    # this leaves out. The predicates must keep matching those queries or the
    # planner will quietly stop using the index, which is what the EXPLAIN
    # tests are for.
    sqlalchemy.Index(
        'ix_spot_pending_timeline',
        'start_at',
        postgresql_where=sqlalchemy.text(PENDING_TIMELINE_PREDICATE),
        sqlite_where=sqlalchemy.text(PENDING_TIMELINE_PREDICATE)),
    sqlalchemy.Index('ix_spot_never_ran',
                     'end_at',
                     postgresql_where=sqlalchemy.text(NEVER_RAN_PREDICATE),
                     sqlite_where=sqlalchemy.text(NEVER_RAN_PREDICATE)),
    # submitted_at is the indexed column so the stall scan's `<= cutoff` is a
    # range scan and its ORDER BY submitted_at needs no sort.
    sqlalchemy.Index(
        'ix_spot_unattended',
        'submitted_at',
        postgresql_where=sqlalchemy.text(CLAIMED_IN_FLIGHT_PREDICATE),
        sqlite_where=sqlalchemy.text(CLAIMED_IN_FLIGHT_PREDICATE)),
    # Occupancy: how many jobs the controllers are holding. Counted on every
    # refresh, and the predicate is wider than the one above, so without this
    # the count is a sequential scan of every task ever run.
    sqlalchemy.Index('ix_spot_claimed_live',
                     'spot_job_id',
                     postgresql_where=sqlalchemy.text(CLAIMED_LIVE_PREDICATE),
                     sqlite_where=sqlalchemy.text(CLAIMED_LIVE_PREDICATE)),
)

job_info_table = sqlalchemy.Table(
    'job_info',
    Base.metadata,
    sqlalchemy.Column('spot_job_id',
                      sqlalchemy.Integer,
                      primary_key=True,
                      autoincrement=True),
    sqlalchemy.Column('name', sqlalchemy.Text),
    # Indexed: every scheduling attempt filters on schedule_state (e.g.
    # get_waiting_job_async, get_num_launching_jobs, get_num_alive_jobs),
    # and active-state rows are a tiny fraction of the table.
    sqlalchemy.Column('schedule_state', sqlalchemy.Text, index=True),
    sqlalchemy.Column('controller_pid', sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('controller_pid_started_at',
                      sqlalchemy.Float,
                      server_default=None),
    sqlalchemy.Column('dag_yaml_path', sqlalchemy.Text),
    sqlalchemy.Column('env_file_path', sqlalchemy.Text),
    sqlalchemy.Column('dag_yaml_content', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('env_file_content', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('config_file_content',
                      sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('user_hash', sqlalchemy.Text),
    sqlalchemy.Column('workspace', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('priority',
                      sqlalchemy.Integer,
                      server_default=str(constants.DEFAULT_PRIORITY)),
    sqlalchemy.Column('priority_class', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('entrypoint', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('original_user_yaml_path',
                      sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('original_user_yaml_content',
                      sqlalchemy.Text,
                      server_default=None),
    # Indexed: every per-pool dashboard query and pool_status request filters
    # by this column. Without an index a job_info table with tens of thousands
    # of (mostly finished) rows turns each pool lookup into a full scan.
    sqlalchemy.Column('pool', sqlalchemy.Text, server_default=None, index=True),
    # Indexed: pool_status fetches per-replica used_by lists by filtering on
    # current_cluster_name; the index keeps that fast when many jobs share
    # the same pool.
    sqlalchemy.Column('current_cluster_name',
                      sqlalchemy.Text,
                      server_default=None,
                      index=True),
    sqlalchemy.Column('job_id_on_pool_cluster',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('pool_hash', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('controller_logs_cleaned_at',
                      sqlalchemy.Float,
                      server_default=None),
    # DAG execution mode: 'parallel' (job group) or 'serial' (pipeline/single)
    sqlalchemy.Column('execution',
                      sqlalchemy.Text,
                      server_default=DagExecution.SERIAL.value),
    # Infrastructure columns for efficient filtering/sorting
    sqlalchemy.Column('cloud', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('region', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('zone', sqlalchemy.Text, server_default=None),
    # Whether this job is a batch coordinator (ds.map()).  Batch jobs are
    # serialized one-at-a-time per pool by the scheduler.
    sqlalchemy.Column('is_batch',
                      sqlalchemy.Boolean,
                      server_default=sqlalchemy.sql.expression.false()),
    # Node names for dashboard display (comma-separated)
    sqlalchemy.Column('node_names', sqlalchemy.Text, server_default=None),
    # In consolidation mode, managed jobs shares the filemount blob managed
    # by API server. This id is a reference to the blob.
    sqlalchemy.Column('file_mounts_blob_id',
                      sqlalchemy.Text,
                      server_default=None),
    # Emergency recovery attempts used in the current episode (bounded retry
    # budget for unexpected controller errors). NULL means 0.
    sqlalchemy.Column('emergency_recovery_count',
                      sqlalchemy.Integer,
                      server_default=None),
    # Timestamp of the most recent emergency recovery attempt; used for
    # retry backoff and budget decay.
    sqlalchemy.Column('last_emergency_recovery_at',
                      sqlalchemy.Float,
                      server_default=None),
    # Where a job launched from inside another managed job came from (e.g.
    # an eval job launched by a job group's watcher task). All NULL for
    # top-level jobs. Written once on the child's row; parent rows are never
    # mutated.
    #   root_job_id: the top-level job of the tree. Load-bearing: the group
    #     the job is shown under and the lifecycle it shares (cancelled with
    #     the root, swept when the root's primary tasks finish).
    #   parent_job_id: the job that launched this one (== root for a direct
    #     member).
    #   parent_task_id: the task within the parent that launched this one.
    #     Display only.
    sqlalchemy.Column('root_job_id',
                      sqlalchemy.Integer,
                      server_default=None,
                      index=True),
    sqlalchemy.Column('parent_job_id',
                      sqlalchemy.Integer,
                      server_default=None,
                      index=True),
    sqlalchemy.Column('parent_task_id', sqlalchemy.Integer,
                      server_default=None),
    #   dynamic_task_index: a dynamic task's ordinal within its root's tree
    # (the root's declared tasks are 0..n-1, dynamic tasks number on from n in
    #     attach order); `<root>-<index>` names it on the CLI. NULL for
    #     top-level jobs.
    #   dynamic_task_count: on the root's row, how many dynamic tasks have
    #     attached; incremented atomically to hand out the next index.
    sqlalchemy.Column('dynamic_task_index',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('dynamic_task_count',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Index('ux_job_info_root_dynamic_task_index',
                     'root_job_id',
                     'dynamic_task_index',
                     unique=True),
)

# Separate table for API access token IDs associated with managed jobs.
# Maps job_id -> token_id for cleanup when the job completes.
api_access_token_table = sqlalchemy.Table(
    'api_access_tokens',
    Base.metadata,
    sqlalchemy.Column('job_id', sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column('token_id', sqlalchemy.Text, nullable=False),
)

# TODO(cooperc): drop the table in a migration
ha_recovery_script_table = sqlalchemy.Table(
    'ha_recovery_script',
    Base.metadata,
    sqlalchemy.Column('job_id', sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column('script', sqlalchemy.Text),
)

job_events_table = sqlalchemy.Table(
    'job_events',
    Base.metadata,
    sqlalchemy.Column('id',
                      sqlalchemy.Integer,
                      primary_key=True,
                      autoincrement=True),
    # See comment above for explanation of the legacy spot_job_id and
    # task_id columns.
    sqlalchemy.Column('spot_job_id', sqlalchemy.Integer, index=True),
    sqlalchemy.Column('task_id', sqlalchemy.Integer, index=True),
    sqlalchemy.Column('new_status', sqlalchemy.Text),
    sqlalchemy.Column('code', sqlalchemy.Text),
    sqlalchemy.Column('reason', sqlalchemy.Text),
    sqlalchemy.Column('timestamp',
                      sqlalchemy.DateTime(timezone=True),
                      index=True),
    # A RecoverySource value (FAILURE / EMERGENCY / RESTART) recording why
    # the job is recovering, so consumers can count only failure-driven
    # recoveries. Set on new_status='RECOVERING' events, and on the
    # RUNNING / WINDING_DOWN event an emergency recovery writes when it
    # keeps the task on its cluster instead of relaunching it
    # (record_emergency_kept_running_async). NULL on all other events and
    # on RECOVERING events written before this column existed (treated as
    # FAILURE for back-compat).
    sqlalchemy.Column('recovery_source', sqlalchemy.Text, server_default=None),
)

batch_state_table = sqlalchemy.Table(
    'batch_state',
    Base.metadata,
    sqlalchemy.Column('job_id', sqlalchemy.Integer, nullable=False),
    sqlalchemy.Column('batch_idx', sqlalchemy.Integer, nullable=False),
    sqlalchemy.Column('start_idx', sqlalchemy.Integer, nullable=False),
    sqlalchemy.Column('end_idx', sqlalchemy.Integer, nullable=False),
    sqlalchemy.Column('status',
                      sqlalchemy.Text,
                      nullable=False,
                      server_default='PENDING'),
    sqlalchemy.Column('worker_cluster', sqlalchemy.Text),
    sqlalchemy.Column('retry_count',
                      sqlalchemy.Integer,
                      nullable=False,
                      server_default='0'),
    sqlalchemy.Column('updated_at', sqlalchemy.Float),
    sqlalchemy.PrimaryKeyConstraint('job_id', 'batch_idx'),
)

# Jobs a managed job waits for: it is claimed by a controller only once all of
# them are DONE, and runs only if all of them succeeded.
job_dependencies_table = sqlalchemy.Table(
    'job_dependencies',
    Base.metadata,
    sqlalchemy.Column('spot_job_id', sqlalchemy.Integer, nullable=False),
    sqlalchemy.Column('depends_on_job_id', sqlalchemy.Integer, nullable=False),
    sqlalchemy.PrimaryKeyConstraint('spot_job_id', 'depends_on_job_id'),
)


def _job_status_enums():
    """Late-import enums from ``sky.jobs.state`` to avoid a load-time cycle."""
    # pylint: disable=import-outside-toplevel
    from sky.jobs import state as jobs_state
    return jobs_state.ManagedJobStatus, jobs_state.ManagedJobScheduleState


def _unfinished_dependency_exists() -> sqlalchemy.sql.elements.ColumnElement:
    """True for a job_info row with a dependency that is not DONE yet.

    Correlates to ``job_info_table`` in the enclosing query. A NULL
    schedule_state (a job from before schedule states existed) does not count
    as unfinished, and neither does a dependency whose row is gone; the
    controller then finds it did not succeed.
    """
    _, ManagedJobScheduleState = _job_status_enums()
    dependency_info = job_info_table.alias('dependency_info')
    return sqlalchemy.exists().where(
        sqlalchemy.and_(
            job_dependencies_table.c.spot_job_id ==
            job_info_table.c.spot_job_id,
            dependency_info.c.spot_job_id ==
            job_dependencies_table.c.depends_on_job_id,
            dependency_info.c.schedule_state !=
            ManagedJobScheduleState.DONE.value,
        ))


def _users_at_launch_limit(
        max_launching_jobs_per_user: int) -> sqlalchemy.Select:
    """Users with at least this many claimed jobs that are not running yet.

    Counted by task status rather than schedule state: a claimed job can be in
    any of the claimed schedule states while it waits (LAUNCHING while it
    launches, is in launch backoff or is parked waiting for resources; ALIVE
    during an emergency retry's backoff), and a job group stays LAUNCHING
    while it runs. So a claimed job counts while it has a task waiting to run
    (PENDING, STARTING or RECOVERING) and none running. Pool jobs never launch
    a cluster and are not counted.
    """
    ManagedJobStatus, ManagedJobScheduleState = _job_status_enums()
    launching_info = job_info_table.alias('launching_info')

    def _has_task_in(statuses: List[Any]):
        return sqlalchemy.exists().where(
            sqlalchemy.and_(
                spot_table.c.spot_job_id == launching_info.c.spot_job_id,
                spot_table.c.status.in_([status.value for status in statuses]),
            ))

    return sqlalchemy.select(launching_info.c.user_hash).where(
        sqlalchemy.and_(
            launching_info.c.schedule_state.in_([
                ManagedJobScheduleState.LAUNCHING.value,
                ManagedJobScheduleState.ALIVE_WAITING.value,
                ManagedJobScheduleState.ALIVE_BACKOFF.value,
                ManagedJobScheduleState.ALIVE.value,
            ]),
            launching_info.c.pool.is_(None),
            launching_info.c.user_hash.is_not(None),
            _has_task_in([
                ManagedJobStatus.PENDING,
                ManagedJobStatus.STARTING,
                ManagedJobStatus.RECOVERING,
            ]),
            ~_has_task_in(
                [ManagedJobStatus.RUNNING, ManagedJobStatus.WINDING_DOWN]),
        )).group_by(
            launching_info.c.user_hash).having(sqlalchemy.func.count() >=  # pylint: disable=not-callable
                                               max_launching_jobs_per_user)


def _has_live_task() -> sqlalchemy.sql.elements.ColumnElement:
    """True for a job_info row with a task launched and not yet finished.

    Correlates to ``job_info_table`` in the enclosing query. A WAITING job
    with such a task held a controller slot before a restart: recovery
    (reset_jobs_for_recovery_batch) sets a live job back to WAITING and keeps
    its tasks as they were, and a launch writes submitted_at once and keeps
    it while the task is parked back in PENDING. A job that has never been
    claimed has no submitted task. Matches CLAIMED_LIVE_PREDICATE, so it is
    served by ix_spot_claimed_live and costs what is claimed now, not the
    whole job history. Written as an uncorrelated IN, which the planner
    hashes once, rather than a correlated EXISTS, whose per-row estimate is
    large enough to trigger Postgres JIT on every call.
    """
    return job_info_table.c.spot_job_id.in_(
        sqlalchemy.select(spot_table.c.spot_job_id).where(
            sqlalchemy.and_(
                spot_table.c.submitted_at.is_not(None),
                spot_table.c.end_at.is_(None),
            )))


def _not_held_by_launch_limit(
    max_launching_jobs_per_user: Optional[int]
) -> sqlalchemy.sql.elements.ColumnElement:
    """True for a job_info row the per-user launch limit does not hold back.

    Correlates to ``job_info_table`` in the enclosing query. Only a WAITING
    non-pool job of a user at the limit is held back, and only if it has no
    live task: a job that recovery set back to WAITING after a restart may be
    running, and holding it would leave it with no controller until its user
    drops below the limit.
    """
    if max_launching_jobs_per_user is None:
        return sqlalchemy.true()
    _, ManagedJobScheduleState = _job_status_enums()
    return sqlalchemy.or_(
        job_info_table.c.schedule_state !=
        ManagedJobScheduleState.WAITING.value,
        job_info_table.c.pool.is_not(None),
        job_info_table.c.user_hash.is_(None),
        job_info_table.c.user_hash.not_in(
            _users_at_launch_limit(max_launching_jobs_per_user)),
        _has_live_task(),
    )


def get_held_job_ids(max_launching_jobs_per_user: int) -> Set[int]:
    """Ids of the WAITING jobs the per-user launch limit holds back.

    The scheduler's own rule (``_not_held_by_launch_limit``, as the claim in
    ``get_waiting_job_async`` applies it), so the jobs queue reports a job as
    held exactly when the claim skips it: a recovered job with a live task is
    never held, whatever its user's count.
    """
    _, ManagedJobScheduleState = _job_status_enums()
    with orm.Session(_db_manager.get_engine()) as session:
        rows = session.execute(
            sqlalchemy.select(job_info_table.c.spot_job_id).where(
                sqlalchemy.and_(
                    job_info_table.c.schedule_state ==
                    ManagedJobScheduleState.WAITING.value,
                    sqlalchemy.not_(
                        _not_held_by_launch_limit(max_launching_jobs_per_user)),
                ))).fetchall()
    return {row[0] for row in rows}


def users_at_launch_limit_sql(max_launching_jobs_per_user: int,
                              dialect: sqlalchemy.engine.Dialect) -> str:
    """The users-at-the-limit subquery as SQL text, for raw-SQL readers.

    Lets sky/jobs/stall.py apply the scheduler's own rule inside its query.
    Compiled with literal values, which is safe because its only values are
    the limit and status constants, never stored or user-provided text.
    """
    return str(
        _users_at_launch_limit(int(max_launching_jobs_per_user)).compile(
            dialect=dialect, compile_kwargs={'literal_binds': True}))


# Subquery that aggregates batch_state into per-job progress counts.
# Used by jobs-queue queries to supply batch_total_batches and
# batch_completed_batches without denormalized columns on job_info.
_batch_progress_subquery = sqlalchemy.select(
    batch_state_table.c.job_id,
    sqlalchemy.func.count().label(  # pylint: disable=not-callable
        'batch_total_batches'),
    sqlalchemy.func.count(  # pylint: disable=not-callable
        sqlalchemy.case((batch_state_table.c.status
                         == 'COMPLETED', 1),)).label('batch_completed_batches'),
).group_by(batch_state_table.c.job_id).subquery('batch_progress')


def create_table(engine: sqlalchemy.engine.Engine):
    # Enable WAL mode to avoid locking issues.
    # See: issue #3863, #1441 and PR #1509
    # https://github.com/microsoft/WSL/issues/2395
    # TODO(romilb): We do not enable WAL for WSL because of known issue in WSL.
    #  This may cause the database locked problem from WSL issue #1441.
    if (engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value and
            not common_utils.is_wsl()):
        try:
            with orm.Session(engine) as session:
                session.execute(sqlalchemy.text('PRAGMA journal_mode=WAL'))
                session.execute(sqlalchemy.text('PRAGMA synchronous=1'))
                session.commit()
        except sqlalchemy_exc.OperationalError as e:
            if 'database is locked' not in str(e):
                raise
            # If the database is locked, it is OK to continue, as the WAL mode
            # is not critical and is likely to be enabled by other processes.

    migration_utils.safe_alembic_upgrade(engine,
                                         migration_utils.SPOT_JOBS_DB_NAME,
                                         migration_utils.SPOT_JOBS_VERSION)


_db_manager = db_utils.DatabaseManager('spot_jobs', create_table)
