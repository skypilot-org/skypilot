"""Add cloud, region, zone columns for infrastructure info and sorting.

Revision ID: 012
Revises: 011
Create Date: 2026-01-20

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
    """Add cloud, region, zone columns to job_info table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'cloud',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('job_info',
                                             'region',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('job_info',
                                             'zone',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
