"""Add columns for stored DAG/env file contents.

Revision ID: 004
Revises: 003
Create Date: 2025-10-27

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
    """Add columns to persist job file contents in the database."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'dag_yaml_content',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('job_info',
                                             'original_user_yaml_content',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('job_info',
                                             'env_file_content',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
