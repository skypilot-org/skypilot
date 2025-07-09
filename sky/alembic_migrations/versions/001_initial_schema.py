"""Initial schema with all tables and backwards compatibility columns

Revision ID: 001
Revises:
Create Date: 2024-01-01 12:00:00.000000

"""
# pylint: disable=invalid-name
from alembic import op
import sqlalchemy as sa

from sky.global_user_state import Base

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    print('Upgrading database schema...')
    # Create any missing tables with current schema
    Base.metadata.create_all(bind=op.get_bind())

    # Get current connection and inspector
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Get column lists for all tables (they all exist now after create_all)
    clusters_columns = {
        col['name'] for col in inspector.get_columns('clusters')
    }
    cluster_history_columns = {
        col['name'] for col in inspector.get_columns('cluster_history')
    }
    users_columns = {col['name'] for col in inspector.get_columns('users')}

    # Helper function to safely add column using cached column list
    def add_column_if_not_exists(table_name, column_name, column_type,
                                 existing_columns, **kwargs):
        if column_name not in existing_columns:
            op.add_column(table_name,
                          sa.Column(column_name, column_type, **kwargs))

    # Add all missing columns to clusters table
    add_column_if_not_exists('clusters',
                             'autostop',
                             sa.Integer(),
                             clusters_columns,
                             server_default='-1')
    add_column_if_not_exists('clusters',
                             'metadata',
                             sa.Text(),
                             clusters_columns,
                             server_default='{}')
    add_column_if_not_exists('clusters',
                             'to_down',
                             sa.Integer(),
                             clusters_columns,
                             server_default='0')
    add_column_if_not_exists('clusters',
                             'owner',
                             sa.Text(),
                             clusters_columns,
                             server_default=None)
    add_column_if_not_exists('clusters',
                             'cluster_hash',
                             sa.Text(),
                             clusters_columns,
                             server_default=None)
    add_column_if_not_exists('clusters',
                             'storage_mounts_metadata',
                             sa.LargeBinary(),
                             clusters_columns,
                             server_default=None)
    add_column_if_not_exists('clusters',
                             'cluster_ever_up',
                             sa.Integer(),
                             clusters_columns,
                             server_default='0')
    add_column_if_not_exists('clusters',
                             'status_updated_at',
                             sa.Integer(),
                             clusters_columns,
                             server_default=None)
    add_column_if_not_exists('clusters',
                             'user_hash',
                             sa.Text(),
                             clusters_columns,
                             server_default=None)
    add_column_if_not_exists('clusters',
                             'config_hash',
                             sa.Text(),
                             clusters_columns,
                             server_default=None)
    add_column_if_not_exists('clusters',
                             'workspace',
                             sa.Text(),
                             clusters_columns,
                             server_default='default')
    add_column_if_not_exists('clusters',
                             'last_creation_yaml',
                             sa.Text(),
                             clusters_columns,
                             server_default=None)
    add_column_if_not_exists('clusters',
                             'last_creation_command',
                             sa.Text(),
                             clusters_columns,
                             server_default=None)

    # Add all missing columns to cluster_history table
    add_column_if_not_exists('cluster_history',
                             'user_hash',
                             sa.Text(),
                             cluster_history_columns,
                             server_default=None)
    add_column_if_not_exists('cluster_history',
                             'last_creation_yaml',
                             sa.Text(),
                             cluster_history_columns,
                             server_default=None)
    add_column_if_not_exists('cluster_history',
                             'last_creation_command',
                             sa.Text(),
                             cluster_history_columns,
                             server_default=None)

    # Add all missing columns to users table
    add_column_if_not_exists('users',
                             'password',
                             sa.Text(),
                             users_columns,
                             server_default=None)
    add_column_if_not_exists('users',
                             'created_at',
                             sa.Integer(),
                             users_columns,
                             server_default=None)


def downgrade():
    # Drop all tables
    Base.metadata.drop_all(bind=op.get_bind())
