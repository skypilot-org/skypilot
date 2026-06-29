"""Add emergency recovery + recovery source columns.

These columns support automatic recovery from unexpected controller errors
(emergency recovery) and let consumers classify why a job is recovering:
- job_info.emergency_recovery_count: recovery attempts used in the current
  episode (bounded retry budget).
- job_info.last_emergency_recovery_at: timestamp of the most recent attempt,
  used for backoff and budget decay.
- spot.status_before_emergency: the task status immediately before an
  emergency recovery; non-NULL marks a RECOVERING task as emergency-origin so
  the resume logic can re-attach to a healthy cluster instead of tearing it
  down.
- job_events.recovery_source: for RECOVERING events, why the job is
  recovering (FAILURE / EMERGENCY / HA). NULL on other events and on
  RECOVERING events written before this column existed (treated as FAILURE).

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
        db_utils.add_column_to_table_alembic('job_events',
                                             'recovery_source',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
