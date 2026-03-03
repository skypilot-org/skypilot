"""Add batch_state table for batch coordinator persistence and HA recovery.

This migration adds the batch_state table to track per-batch progress:
- job_id: Managed job ID
- batch_idx: 0-based batch index
- start_idx / end_idx: Dataset item range
- status: PENDING / DISPATCHED / COMPLETED / FAILED
- worker_cluster: Cluster processing this batch
- retry_count: Number of retries
- updated_at: Last update timestamp

On controller crash, DISPATCHED batches are reset to PENDING for re-dispatch.

Revision ID: 015
Revises: 014
Create Date: 2026-02-27

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.jobs.state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '015'
down_revision: Union[str, Sequence[str], None] = '014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create batch_state table for batch coordinator persistence."""
    with op.get_context().autocommit_block():
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'batch_state')


def downgrade():
    """No downgrade logic."""
    pass
