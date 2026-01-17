"""Add job group columns for spot table and job_info table.

Adds:
- cluster_name (TEXT) to spot table for per-task cluster tracking
- execution (TEXT) to job_info: 'parallel', 'sequential', or NULL
- placement (TEXT) to job_info: 'SAME_INFRA', 'ANY', or NULL

Note: is_job_group is derived from execution == 'parallel' at query time.

Revision ID: 012
Revises: 011
Create Date: 2025-12-29

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

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
        # Add execution column to job_info table
        # Values: 'parallel' (job group), 'sequential' (chain), or NULL
        db_utils.add_column_to_table_alembic('job_info',
                                             'execution',
                                             sa.Text(),
                                             server_default=None)
        # Add placement column to job_info table
        # Values: 'SAME_INFRA', 'ANY', or NULL
        db_utils.add_column_to_table_alembic('job_info',
                                             'placement',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
