"""Columns for cluster pool.

Revision ID: 002
Revises: 001
Create Date: 2025-07-18

"""

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade():
    """Add columns for cluster pool."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic(
            'job_info',
            'pool',
            sa.Text(),
            default_statement='DEFAULT NULL')
        db_utils.add_column_to_table_alembic(
            'job_info',
            'current_cluster_name',
            sa.Text(),
            default_statement='DEFAULT NULL')
        db_utils.add_column_to_table_alembic(
            'job_info',
            'job_id_on_pm',
            sa.Integer(),
            default_statement='DEFAULT NULL')


def downgrade():
    """Remove columns for cluster pool."""
    # TODO(tian): remove columns
