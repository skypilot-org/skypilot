"""Add primary_tasks and termination_delay columns to job_info table.

This migration adds support for primary/auxiliary job markers in job groups:
- primary_tasks: JSON list of job names that are "primary". When all primary
  jobs complete, auxiliary jobs (all other jobs) are automatically terminated.
  NULL means all jobs are primary (traditional behavior).
- termination_delay: JSON (string like "30s" or dict with job-specific delays
  like {"default": "30s", "replay-buffer": "1m"}). Defines the grace period
  before auxiliary jobs are terminated after primary jobs complete.

Revision ID: 013
Revises: 012
Create Date: 2026-01-19

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '013'
down_revision: Union[str, Sequence[str], None] = '012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add primary_tasks and termination_delay columns to job_info table."""
    with op.get_context().autocommit_block():
        # Add primary_tasks column: JSON list of job names that are "primary".
        # NULL means all jobs are primary (traditional behavior).
        db_utils.add_column_to_table_alembic('job_info',
                                             'primary_tasks',
                                             sa.JSON(),
                                             server_default=None)
        # Add termination_delay column: JSON (string or dict with delays).
        # NULL means immediate termination (0s delay).
        db_utils.add_column_to_table_alembic('job_info',
                                             'termination_delay',
                                             sa.JSON(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
