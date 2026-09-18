"""Add the launch timeline columns to spot.

- spot.created_at: when the job was accepted, as epoch seconds. T0 of the
  launch timeline. A separate column rather than reusing the PENDING
  job_events row, whose timestamp is a naive local datetime while every other
  timestamp on spot is time.time(); subtracting the two is wrong by the UTC
  offset on any non-UTC deployment and can come out negative.
- spot.eligible_at: when a task could first have started. Equal to created_at
  for a single task and for every task of a job group, whose tasks all begin
  waiting together; for a pipeline's task N it is when task N-1 finished.
  Measuring a pipeline's later tasks from submission would fold every upstream
  task's runtime into t_controller_queue, which claims to mean "the scheduler
  was saturated" -- unbounded, and indistinguishable afterwards in a histogram
  labelled only by workspace.
- spot.t_*: the timeline denormalized once the job first reaches RUNNING, so
  the jobs list renders from one indexed row read rather than a per-job scan
  of launch_attempts.
- ix_spot_pending_timeline / ix_spot_never_ran: the metrics daemon asks once a
  minute for tasks that have started but have no timeline yet, and for tasks
  that ended without ever running. Without these, each is a full scan of every
  task ever run, every minute. Both are partial, so they hold the pending work
  rather than the job history -- see the note in sky/jobs/state.py.

Revision ID: 027
Revises: 026
Create Date: 2026-09-02

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.jobs import state
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '027'
down_revision: Union[str, Sequence[str], None] = '026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Imported rather than repeated: an index whose predicate has drifted from the
# query it serves is silently not used, and nothing fails.
_PENDING_INDEX = 'ix_spot_pending_timeline'
_NEVER_RAN_INDEX = 'ix_spot_never_ran'

_TIMELINE_COLUMNS = (
    'created_at',
    'eligible_at',
    't_controller_queue',
    't_retry_overhead',
    't_unattributed',
    't_provision_setup',
    't_queue_wait',
    't_node_startup',
    't_runtime_setup',
    't_time_to_running',
)


def upgrade():
    """Add the launch timeline columns and the daemon's lookup index."""
    with op.get_context().autocommit_block():
        for column in _TIMELINE_COLUMNS:
            db_utils.add_column_to_table_alembic('spot',
                                                 column,
                                                 sa.Float(),
                                                 server_default=None)
    # Separate inspection pass: the columns have to exist before an index can
    # be built over them, and create_index is not idempotent on its own.
    bind = op.get_bind()
    existing = {ix['name'] for ix in sa.inspect(bind).get_indexes('spot')}
    # Partial on purpose: the queries these serve ask for rows that have not
    # been processed, and a full index would also carry every row already
    # processed and every pre-upgrade row that can never match -- permanently,
    # since neither is ever given a timeline. See the model for the full note.
    for name, column, predicate in (
        (_PENDING_INDEX, 'start_at', state.PENDING_TIMELINE_PREDICATE),
        (_NEVER_RAN_INDEX, 'end_at', state.NEVER_RAN_PREDICATE),
    ):
        # Dropped first rather than skipped when present. Skipping is what
        # makes a migration idempotent, and it is also what stops it replacing
        # an index whose definition changed -- this revision is unreleased and
        # has already been deployed carrying a full index under this same name,
        # so a deployment that ran the earlier version would otherwise keep it
        # forever and never receive this fix. Re-creating is cheap here: the
        # index covers pending work, so it is small by construction.
        with op.get_context().autocommit_block():
            if name in existing:
                op.drop_index(name, table_name='spot')
            op.create_index(name,
                            'spot', [column],
                            postgresql_where=sa.text(predicate),
                            sqlite_where=sa.text(predicate))


def downgrade():
    """No-op for backward compatibility."""
    pass
