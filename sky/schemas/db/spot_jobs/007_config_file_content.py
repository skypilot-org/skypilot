"""Add config_file_content column for persisting job config files.

Revision ID: 007
Revises: 006
Create Date: 2025-11-19

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '007'
down_revision: Union[str, Sequence[str], None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add config_file_content column to persist job config file contents."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'config_file_content',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
