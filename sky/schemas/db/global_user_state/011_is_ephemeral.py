"""Columns for whether the volume is ephemeral.

Revision ID: 011
Revises: 010
Create Date: 2025-11-14

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.π
revision: str = '011'
down_revision: Union[str, Sequence[str], None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add columns for whether the volume is ephemeral."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('volumes',
                                             'is_ephemeral',
                                             sa.Integer(),
                                             server_default='0')


def downgrade():
    """Remove columns for whether the volume is ephemeral."""
    pass
