"""Initial schema for KV cache database

Revision ID: 001
Revises:
Create Date: 2025-11-13 12:00:00.000000

"""
# pylint: disable=invalid-name
from alembic import op

from sky.utils.db import db_utils
from sky.utils.db.kv_cache import Base

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        # Create any missing tables with current schema first
        db_utils.add_all_tables_to_db_sqlalchemy(Base.metadata, op.get_bind())


def downgrade():
    # Drop all tables
    Base.metadata.drop_all(bind=op.get_bind())
