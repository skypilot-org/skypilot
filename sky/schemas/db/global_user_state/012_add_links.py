"""Add links column for storing cluster instance links.

Revision ID: 012
Revises: 011
Create Date: 2025-01-XX

"""
# pylint: disable=invalid-name
from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision = '012'
down_revision = '011'
branch_labels = None
depends_on = None


def upgrade():
    """Add links column to store instance links as JSON."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('clusters',
                                             'links',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass

