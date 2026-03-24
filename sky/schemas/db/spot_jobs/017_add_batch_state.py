"""Add batch_state table and is_batch column for batch coordinator.

This migration adds:
1. batch_state table to track per-batch progress:
   - job_id: Managed job ID
   - batch_idx: 0-based batch index
   - start_idx / end_idx: Dataset item range
   - status: PENDING / DISPATCHED / COMPLETED / FAILED
   - worker_cluster: Cluster processing this batch
   - retry_count: Number of retries
   - updated_at: Last update timestamp

2. is_batch column to job_info for batch coordinator identification.
   Batch coordinator jobs (ds.map()) are serialized one-at-a-time per pool
   by the scheduler.

On controller crash, DISPATCHED batches are reset to PENDING for re-dispatch.

Revision ID: 017
Revises: 016
Create Date: 2026-02-27

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy

from sky.jobs.state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '017'
down_revision: Union[str, Sequence[str], None] = '016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create batch_state table and add is_batch column to job_info."""
    with op.get_context().autocommit_block():
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'batch_state')
        db_utils.add_column_to_table_alembic(
            'job_info',
            'is_batch',
            sqlalchemy.Boolean,
            server_default=sqlalchemy.sql.expression.false())


def downgrade():
    """No downgrade logic."""
    pass
