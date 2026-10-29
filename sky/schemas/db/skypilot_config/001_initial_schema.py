"""Initial schema for sky config database

Revision ID: 001
Revises:
Create Date: 2025-10-21

"""
# pylint: disable=invalid-name
from alembic import op

from sky.server import config_db
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create initial schema for config_yaml table"""
    config_db.ensure_config_db_schema()
    base = config_db.Base
    assert base is not None
    with op.get_context().autocommit_block():
        # Create all tables with their current schema
        db_utils.add_all_tables_to_db_sqlalchemy(base.metadata, op.get_bind())


def downgrade():
    """Drop all tables"""
    config_db.ensure_config_db_schema()
    base = config_db.Base
    assert base is not None
    base.metadata.drop_all(bind=op.get_bind())
