"""Initial schema for recipes database
Revision ID: 001
Revises:
Create Date: 2025-01-26 12:00:00.000000
"""
# pylint: disable=invalid-name
from alembic import op

from sky.recipes.db import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create initial schema for recipes table."""
    with op.get_context().autocommit_block():
        # Create all tables with their current schema
        db_utils.add_all_tables_to_db_sqlalchemy(Base.metadata, op.get_bind())


def downgrade():
    """Drop all tables."""
    Base.metadata.drop_all(bind=op.get_bind())
