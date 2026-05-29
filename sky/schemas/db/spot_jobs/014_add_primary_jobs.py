"""Add is_primary_in_job_group column to spot table.

This migration adds support for primary/auxiliary task markers in job groups:
- is_primary_in_job_group: Boolean indicating whether this task is "primary"
  (True) or "auxiliary" (False) within a job group. NULL for non-job-group
  jobs (single jobs and pipelines). When all primary tasks complete, auxiliary
  tasks are automatically terminated.

Revision ID: 014
Revises: 013
Create Date: 2026-01-19

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '014'
down_revision: Union[str, Sequence[str], None] = '013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add is_primary_in_job_group column to spot table."""
    with op.get_context().autocommit_block():
        # Add is_primary_in_job_group column: Boolean indicating whether this
        # task is "primary" (True) or "auxiliary" (False) within a job group.
        # NULL for non-job-group jobs (single jobs and pipelines).
        db_utils.add_column_to_table_alembic('spot',
                                             'is_primary_in_job_group',
                                             sa.Boolean(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
