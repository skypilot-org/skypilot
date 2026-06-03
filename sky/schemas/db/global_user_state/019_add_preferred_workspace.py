"""Add preferred_workspace column to users table.

Revision ID: 019
Revises: 018
Create Date: 2026-05-29

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '019'
down_revision: Union[str, Sequence[str], None] = '018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add preferred_workspace column for the user-set default workspace."""
    from alembic import op  # pylint: disable=import-outside-toplevel

    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('users',
                                             'preferred_workspace',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
