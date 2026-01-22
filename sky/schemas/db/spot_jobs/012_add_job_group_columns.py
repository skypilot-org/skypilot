"""Add job group columns to spot and job_info tables.

Adds:
- cluster_name (TEXT) to spot table for per-task cluster tracking
- execution (TEXT) to job_info table: 'parallel' (job group) or 'serial'
  (pipeline/single job)

Revision ID: 012
Revises: 011
Create Date: 2025-12-29

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.dag import DagExecution
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '012'
down_revision: Union[str, Sequence[str], None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add job group columns to spot and job_info tables."""
    with op.get_context().autocommit_block():
        # Add cluster_name column to spot table for per-task cluster tracking
        # in JobGroups (each task may run on a different cluster)
        db_utils.add_column_to_table_alembic('spot',
                                             'cluster_name',
                                             sa.Text(),
                                             server_default=None)
        # Add execution column to job_info table for execution mode:
        # 'parallel' (job group) or 'serial' (pipeline/single job)
        db_utils.add_column_to_table_alembic(
            'job_info',
            'execution',
            sa.Text(),
            server_default=DagExecution.SERIAL.value)
        # Update existing rows to have 'serial' execution mode
        op.execute(
            f'UPDATE job_info SET execution = \'{DagExecution.SERIAL.value}\' '
            'WHERE execution IS NULL')


def downgrade():
    """No downgrade logic."""
    pass
