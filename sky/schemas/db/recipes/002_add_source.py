"""Add source column to recipes.

Records where an externally managed recipe comes from (for example a git
repository). NULL for recipes created through the API.

Revision ID: 002
Revises: 001
Create Date: 2026-10-07

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, Sequence[str], None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add the source column."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('recipes',
                                             'source',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
