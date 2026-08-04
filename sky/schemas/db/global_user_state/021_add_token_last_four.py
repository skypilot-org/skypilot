"""Add last_four column to service_account_tokens table.

Revision ID: 021
Revises: 020
Create Date: 2026-06-19

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '021'
down_revision: Union[str, Sequence[str], None] = '020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add last_four column to store the last 4 characters of the token."""
    from alembic import op  # pylint: disable=import-outside-toplevel

    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('service_account_tokens',
                                             'last_four', sa.Text())


def downgrade():
    """No-op for backward compatibility."""
    pass
