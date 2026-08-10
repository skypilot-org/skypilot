"""Add emergency recovery + recovery source columns.

These columns support automatic recovery from unexpected controller errors
(emergency recovery) and let consumers classify why a job is recovering:
- job_info.emergency_recovery_count: recovery attempts used in the current
  episode (bounded retry budget).
- job_info.last_emergency_recovery_at: timestamp of the most recent attempt,
  used for backoff and budget decay.
- spot.recovering_from_failure: for a RECOVERING task, whether the current
  recovery episode carries failure credit (a genuine preemption/failure is
  involved, as opposed to a purely system-driven EMERGENCY/RESTART episode);
  cleared when the task leaves RECOVERING. Decides whether completing the
  episode increments recovery_count (NULL — rows written before this column
  existed — is treated as credited).
- job_events.recovery_source: for RECOVERING events, why the job is
  recovering (FAILURE / EMERGENCY / RESTART). NULL on other events and on
  RECOVERING events written before this column existed (treated as FAILURE).

Revision ID: 022
Revises: 021
Create Date: 2026-06-12

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '022'
down_revision: Union[str, Sequence[str], None] = '021'
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
                                             'recovering_from_failure',
                                             sa.Boolean(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('job_events',
                                             'recovery_source',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
