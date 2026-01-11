"""Add cluster_name column to spot table for JobGroup per-task tracking.

Adds:
- cluster_name (TEXT) to spot table for per-task cluster tracking in JobGroups

Note: JobGroup config (is_job_group, placement, execution) is derived from the
dag_yaml_content already stored in job_info table, so no additional columns
are needed there.

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
    """Add cluster_name column to spot table for JobGroup per-task tracking."""
    with op.get_context().autocommit_block():
        # Add cluster_name column to spot table for per-task cluster tracking
        # in JobGroups (each task may run on a different cluster)
        db_utils.add_column_to_table_alembic('spot',
                                             'cluster_name',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
