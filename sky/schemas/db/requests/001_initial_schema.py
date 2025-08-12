"""Initial schema for state database with backwards compatibility columns

Revision ID: 001
Revises:
Create Date: 2024-01-01 12:00:00.000000

"""
# pylint: disable=invalid-name
from alembic import op
import sqlalchemy as sa

from sky.server.requests import requests
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    with op.get_context().autocommit_block():
        # Create any missing tables with current schema first
        db_utils.add_all_tables_to_db_sqlalchemy(requests.Base.metadata,
                                                 op.get_bind())
    
        # Add all missing columns to clusters table
        # This allows each column addition to fail independently without rolling
        # back the entire migration, which is needed for backwards compatibility
        db_utils.add_column_to_table_alembic(
            requests.REQUEST_TABLE,
            requests.COL_STATUS_MSG,
            sa.Text(),
            server_default=None)
        db_utils.add_column_to_table_alembic(
            requests.REQUEST_TABLE,
            requests.COL_SHOULD_RETRY,
            sa.Boolean(),
            server_default=False)
        db_utils.add_column_to_table_alembic(
            requests.REQUEST_TABLE,
            requests.COL_FINISHED_AT,
            sa.Float(),
            server_default=None)
        db_utils.add_column_to_table_alembic(
            requests.REQUEST_TABLE,
            requests.COL_HOST_UUID,
            sa.Text(),
            server_default=None)

