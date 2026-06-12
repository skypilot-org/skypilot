"""Add emergency recovery columns to job_info and spot tables.

These columns support automatic recovery from unexpected controller errors
(EMERGENCY_RECOVERING status):
- job_info.emergency_recovery_count: recovery attempts used in the current
  episode (bounded retry budget).
- job_info.last_emergency_recovery_at: timestamp of the most recent attempt,
  used for backoff and budget decay.
- spot.status_before_emergency: the task status at the moment it entered
  EMERGENCY_RECOVERING, so the resume logic can re-attach to a healthy
  RUNNING cluster instead of tearing it down.

Revision ID: 021
Revises: 020
Create Date: 2026-06-12

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '021'
down_revision: Union[str, Sequence[str], None] = '020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add emergency recovery columns."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'emergency_recovery_count',
                                             sa.Integer(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('job_info',
                                             'last_emergency_recovery_at',
                                             sa.Float(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('spot',
                                             'status_before_emergency',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
