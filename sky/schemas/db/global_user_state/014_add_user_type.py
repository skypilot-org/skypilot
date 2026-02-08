"""Add type column to users table.

Revision ID: 014
Revises: 013
Create Date: 2026-02-04

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '014'
down_revision: Union[str, Sequence[str], None] = '013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add type column to users table for user categorization.

    """
    from alembic import op  # pylint: disable=import-outside-toplevel

    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('users',
                                             'type',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
