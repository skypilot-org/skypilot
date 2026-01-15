"""Adding a hash column for pool.

Revision ID: 003
Revises: 002
Create Date: 2025-07-18

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, Sequence[str], None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add columns for pool hash."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'pool_hash',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """Remove columns for pool hash."""
    pass
