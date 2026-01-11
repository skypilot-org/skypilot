"""Add JobGroup columns to job_info and spot tables.

Adds:
- is_job_group (BOOLEAN) to job_info table
- placement (TEXT) to job_info table
- execution (TEXT) to job_info table
- cluster_name (TEXT) to spot table for per-task cluster tracking

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
    """Add JobGroup columns to job_info and spot tables."""
    with op.get_context().autocommit_block():
        # Add is_job_group column to job_info table
        db_utils.add_column_to_table_alembic('job_info',
                                             'is_job_group',
                                             sa.Boolean(),
                                             server_default='0')
        # Add placement column to job_info table
        db_utils.add_column_to_table_alembic('job_info',
                                             'placement',
                                             sa.Text(),
                                             server_default=None)
        # Add execution column to job_info table
        db_utils.add_column_to_table_alembic('job_info',
                                             'execution',
                                             sa.Text(),
                                             server_default=None)
        # Add cluster_name column to spot table for per-task cluster tracking
        db_utils.add_column_to_table_alembic('spot',
                                             'cluster_name',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
