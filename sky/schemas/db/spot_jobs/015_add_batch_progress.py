"""Add batch progress columns to job_info table.

This migration adds support for batch job progress tracking in the dashboard:
- batch_total_batches: Total number of batches in a batch job.
- batch_completed_batches: Number of completed batches.

These columns are NULL for non-batch jobs.

Revision ID: 015
Revises: 014
Create Date: 2026-02-19

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '015'
down_revision: Union[str, Sequence[str], None] = '014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add batch progress columns to job_info table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'batch_total_batches',
                                             sa.Integer(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('job_info',
                                             'batch_completed_batches',
                                             sa.Integer(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
