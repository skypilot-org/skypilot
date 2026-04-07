"""Add priority_class column to job_info table.

This migration adds priority_class column to store the named priority class
for managed jobs, alongside the numeric priority value.

Revision ID: 017
Revises: 016
Create Date: 2026-03-18

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '017'
down_revision: Union[str, Sequence[str], None] = '016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add priority_class column to job_info table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'priority_class',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
