"""Columns for cluster pool.

Revision ID: 002
Revises: 001
Create Date: 2025-07-18

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
    """Add columns for cluster pool."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('version_specs',
                                             'yaml_content',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """Remove columns for cluster pool."""
    pass
