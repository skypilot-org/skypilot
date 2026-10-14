"""Add file_mounts_blob_id column to services.

Revision ID: 005
Revises: 004
Create Date: 2026-10-13

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, Sequence[str], None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add file_mounts_blob_id column to record a service's upload blob."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('services',
                                             'file_mounts_blob_id',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    pass
