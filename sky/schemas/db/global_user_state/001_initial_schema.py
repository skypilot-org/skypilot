"""Initial schema for state database with backwards compatibility columns

Revision ID: 001
Revises:
Create Date: 2024-01-01 12:00:00.000000

"""
# pylint: disable=invalid-name
from alembic import op
import sqlalchemy as sa

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        # Create any missing tables with current schema first
        db_utils.add_tables_to_db_sqlalchemy(Base.metadata, op.get_bind())

        # Add all missing columns to clusters table
        # This allows each column addition to fail independently without rolling
        # back the entire migration, which is needed for backwards compatibility

        # Add all missing columns to clusters table
        db_utils.add_column_to_table_alembic('clusters',
                                             'autostop',
                                             sa.Integer(),
                                             server_default='-1')
        db_utils.add_column_to_table_alembic('clusters',
                                             'to_down',
                                             sa.Integer(),
                                             server_default='0')
        db_utils.add_column_to_table_alembic('clusters',
                                             'metadata',
                                             sa.Text(),
                                             server_default='{}')
        db_utils.add_column_to_table_alembic('clusters',
                                             'owner',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'cluster_hash',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'storage_mounts_metadata',
                                             sa.LargeBinary(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic(
            'clusters',
            'cluster_ever_up',
            sa.Integer(),
            server_default='0',
            # Set the value to 1 so that all the existing clusters before #2977
            # are considered as ever up, i.e:
            #   existing cluster's default (null) -> 1;
            #   new cluster's default -> 0;
            # This is conservative for the existing clusters: even if some INIT
            # clusters were never really UP, setting it to 1 means they won't be
            # auto-deleted during any failover.
            value_to_replace_existing_entries=1)
        db_utils.add_column_to_table_alembic('clusters',
                                             'status_updated_at',
                                             sa.Integer(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'config_hash',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'user_hash',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'workspace',
                                             sa.Text(),
                                             server_default='default')
        db_utils.add_column_to_table_alembic('clusters',
                                             'last_creation_yaml',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'last_creation_command',
                                             sa.Text(),
                                             server_default=None)

        # Add all missing columns to cluster_history table
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'user_hash',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'last_creation_yaml',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'last_creation_command',
                                             sa.Text(),
                                             server_default=None)

        # Add all missing columns to users table
        db_utils.add_column_to_table_alembic('users',
                                             'password',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('users',
                                             'created_at',
                                             sa.Integer(),
                                             server_default=None)


def downgrade():
    # Drop all tables
    Base.metadata.drop_all(bind=op.get_bind())
