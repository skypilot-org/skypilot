"""Add full_resources column to spot table.

Revision ID: 008
Revises: 007
Create Date: 2025-12-03

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '008'
down_revision: Union[str, Sequence[str], None] = '007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add full_resources column to spot table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('spot',
                                             'full_resources',
                                             sa.JSON(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
