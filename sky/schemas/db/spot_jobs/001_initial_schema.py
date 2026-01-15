"""Initial schema for spot jobs database with backwards compatibility columns

Revision ID: 001
Revises:
Create Date: 2024-01-01 12:00:00.000000

"""
# pylint: disable=invalid-name
import json

from alembic import op
import sqlalchemy as sa

from sky.jobs.state import Base
from sky.skylet import constants
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

        # Spot table columns
        db_utils.add_column_to_table_alembic('spot', 'failure_reason',
                                             sa.Text())
        db_utils.add_column_to_table_alembic('spot',
                                             'spot_job_id',
                                             sa.Integer(),
                                             copy_from='job_id')
        db_utils.add_column_to_table_alembic(
            'spot',
            'task_id',
            sa.Integer(),
            server_default='0',
            value_to_replace_existing_entries=0)
        db_utils.add_column_to_table_alembic('spot',
                                             'task_name',
                                             sa.Text(),
                                             copy_from='job_name')
        db_utils.add_column_to_table_alembic(
            'spot',
            'specs',
            sa.Text(),
            value_to_replace_existing_entries=json.dumps(
                {'max_restarts_on_errors': 0}))
        db_utils.add_column_to_table_alembic('spot', 'local_log_file',
                                             sa.Text())
        db_utils.add_column_to_table_alembic(
            'spot',
            'metadata',
            sa.Text(),
            server_default='{}',
            value_to_replace_existing_entries='{}')

        # Job info table columns
        db_utils.add_column_to_table_alembic('job_info', 'schedule_state',
                                             sa.Text())
        db_utils.add_column_to_table_alembic('job_info', 'controller_pid',
                                             sa.Integer())
        db_utils.add_column_to_table_alembic('job_info', 'dag_yaml_path',
                                             sa.Text())
        db_utils.add_column_to_table_alembic('job_info', 'env_file_path',
                                             sa.Text())
        db_utils.add_column_to_table_alembic('job_info', 'user_hash', sa.Text())
        db_utils.add_column_to_table_alembic(
            'job_info',
            'workspace',
            sa.Text(),
            value_to_replace_existing_entries=constants.
            SKYPILOT_DEFAULT_WORKSPACE)
        db_utils.add_column_to_table_alembic(
            'job_info',
            'priority',
            sa.Integer(),
            server_default=str(constants.DEFAULT_PRIORITY),
            value_to_replace_existing_entries=constants.DEFAULT_PRIORITY)
        db_utils.add_column_to_table_alembic('job_info', 'entrypoint',
                                             sa.Text())
        db_utils.add_column_to_table_alembic('job_info',
                                             'original_user_yaml_path',
                                             sa.Text())


def downgrade():
    """Drop all tables"""
    Base.metadata.drop_all(bind=op.get_bind())
