"""Column for volume error message.

Revision ID: 012
Revises: 011
Create Date: 2025-01-08

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '012'
down_revision: Union[str, Sequence[str], None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add error_message column to volumes table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('volumes',
                                             'error_message',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """Remove error_message column from volumes table."""
    with op.get_context().autocommit_block():
        op.drop_column('volumes', 'error_message')
