"""Add the launch timeline columns to spot.

- spot.created_at: when the job was accepted, as epoch seconds. T0 of the
  launch timeline. A separate column rather than reusing the PENDING
  job_events row, whose timestamp is a naive local datetime while every other
  timestamp on spot is time.time(); subtracting the two is wrong by the UTC
  offset on any non-UTC deployment and can come out negative.
- spot.t_*: the timeline denormalized once the job first reaches RUNNING, so
  the jobs list renders from one indexed row read rather than a per-job scan
  of launch_attempts.

Revision ID: 025
Revises: 024
Create Date: 2026-09-02

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '025'
down_revision: Union[str, Sequence[str], None] = '024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TIMELINE_COLUMNS = (
    'created_at',
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
    """Add the launch timeline columns."""
    with op.get_context().autocommit_block():
        for column in _TIMELINE_COLUMNS:
            db_utils.add_column_to_table_alembic('spot',
                                                 column,
                                                 sa.Float(),
                                                 server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
