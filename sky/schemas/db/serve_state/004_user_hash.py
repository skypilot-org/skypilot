"""Add user_hash column to services.

Revision ID: 004
Revises: 003
Create Date: 2026-09-04

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '004'
down_revision: Union[str, Sequence[str], None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add user_hash column to record the creator of a service/pool."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('services',
                                             'user_hash',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    pass
