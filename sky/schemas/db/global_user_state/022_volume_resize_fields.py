"""Add resize columns to volumes table.

Revision ID: 022
Revises: 021
Create Date: 2026-08-25

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '022'
down_revision: Union[str, Sequence[str], None] = '021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add the resize_status, resize_target_size and resize_message columns.

    A volume's recorded size is the capacity it actually has, so an expansion
    that has not finished -- or that is waiting on the workload to restart
    before the filesystem can grow -- is invisible without these.
    """
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('volumes',
                                             'resize_status',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('volumes',
                                             'resize_target_size',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('volumes',
                                             'resize_message',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
