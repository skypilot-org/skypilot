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
- ix_spot_pending_timeline: the metrics daemon asks once a minute for tasks
  that have started but have no timeline yet. Without the index that is a full
  scan of every task ever run, every minute.

Revision ID: 027
Revises: 026
Create Date: 2026-09-02

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '027'
down_revision: Union[str, Sequence[str], None] = '026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = 'ix_spot_pending_timeline'

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
    if _INDEX_NAME in {
            ix['name'] for ix in sa.inspect(bind).get_indexes('spot')
    }:
        return
    with op.get_context().autocommit_block():
        op.create_index(_INDEX_NAME, 'spot', ['t_time_to_running', 'start_at'])


def downgrade():
    """No-op for backward compatibility."""
    pass
