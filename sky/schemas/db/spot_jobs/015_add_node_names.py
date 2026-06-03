"""Add node_names column to job_info table.

This migration adds node_names column to store the infrastructure node names
where the managed job runs, for dashboard display.

Revision ID: 015
Revises: 014
Create Date: 2026-02-04

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '015'
down_revision: Union[str, Sequence[str], None] = '014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add node_names column to job_info table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'node_names',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
