"""Add links column for storing cluster instance links.

Revision ID: 011
Revises: 010
Create Date: 2026-01-07

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '011'
down_revision: Union[str, Sequence[str], None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add links column to store instance links as JSON."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('spot',
                                             'links',
                                             sa.JSON(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
