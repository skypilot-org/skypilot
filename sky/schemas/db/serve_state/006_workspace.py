"""Add workspace column to services.

Revision ID: 006
Revises: 005
Create Date: 2026-11-11

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, Sequence[str], None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add workspace column to record the workspace a service runs in."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('services',
                                             'workspace',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    pass
