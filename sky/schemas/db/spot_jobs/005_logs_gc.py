"""Adding columns for the GC time of task logs and controller logs.

Revision ID: 005
Revises: 004
Create Date: 2025-10-20

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
    """Add columns for logs gc."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'controller_logs_cleaned_at',
                                             sa.Float(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('spot',
                                             'logs_cleaned_at',
                                             sa.Float(),
                                             server_default=None)


def downgrade():
    """Remove columns for logs gc."""
    pass
