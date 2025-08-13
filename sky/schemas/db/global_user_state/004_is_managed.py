"""Columns for whether the cluster is managed.

Revision ID: 004
Revises: 003
Create Date: 2025-08-07

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.π
revision: str = '004'
down_revision: Union[str, Sequence[str], None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add columns for whether the cluster is managed."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('clusters',
                                             'is_managed',
                                             sa.Integer(),
                                             server_default='0')


def downgrade():
    """Remove columns for whether the cluster is managed."""
    pass
