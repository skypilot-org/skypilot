"""Add links column to clusters table for dashboard external links.

Revision ID: 018
Revises: 017
Create Date: 2026-05-17

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '018'
down_revision: Union[str, Sequence[str], None] = '017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add links column to store cluster external links as JSON."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('clusters',
                                             'links',
                                             sa.JSON(),
                                             server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
