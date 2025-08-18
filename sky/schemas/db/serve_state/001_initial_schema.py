"""Initial schema for sky serve state database with backwards compatibility

Revision ID: 001
Revises:
Create Date: 2024-01-01 12:00:00.000000

"""
# pylint: disable=invalid-name
import json

from alembic import op
import sqlalchemy as sa

from sky.serve import constants
from sky.serve.serve_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create initial schema and add all backwards compatibility columns"""
    with op.get_context().autocommit_block():
        # Create all tables with their current schema
        db_utils.add_all_tables_to_db_sqlalchemy(Base.metadata, op.get_bind())

        # Add backwards compatibility columns using helper function that matches
        # original add_column_to_table_sqlalchemy behavior exactly
        db_utils.add_column_to_table_alembic('services',
                                             'requested_resources_str',
                                             sa.Text())
        db_utils.add_column_to_table_alembic(
            'services',
            'current_version',
            sa.Integer(),
            server_default=f'{constants.INITIAL_VERSION}')
        db_utils.add_column_to_table_alembic('services',
                                             'active_versions',
                                             sa.Text(),
                                             server_default=json.dumps([]))
        db_utils.add_column_to_table_alembic('services',
                                             'load_balancing_policy', sa.Text())
        db_utils.add_column_to_table_alembic('services',
                                             'tls_encrypted',
                                             sa.Integer(),
                                             server_default='0')
        db_utils.add_column_to_table_alembic('services',
                                             'pool',
                                             sa.Integer(),
                                             server_default='0')
        db_utils.add_column_to_table_alembic(
            'services',
            'controller_pid',
            sa.Integer(),
            value_to_replace_existing_entries=-1)
        db_utils.add_column_to_table_alembic('services', 'hash', sa.Text())
        db_utils.add_column_to_table_alembic('services', 'entrypoint',
                                             sa.Text())


def downgrade():
    """Drop all tables"""
    Base.metadata.drop_all(bind=op.get_bind())
