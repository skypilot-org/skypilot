"""Add is_batch column to job_info for batch coordinator identification.

Batch coordinator jobs (ds.map()) are serialized one-at-a-time per pool
by the scheduler.  This column lets the scheduler distinguish batch jobs
from regular pool jobs without parsing the DAG YAML.

Revision ID: 016
Revises: 015
Create Date: 2026-03-05

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '016'
down_revision: Union[str, Sequence[str], None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add is_batch boolean column to job_info table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic(
            'job_info',
            'is_batch',
            sqlalchemy.Boolean,
            server_default=sqlalchemy.sql.expression.false())


def downgrade():
    """No downgrade logic."""
    pass
