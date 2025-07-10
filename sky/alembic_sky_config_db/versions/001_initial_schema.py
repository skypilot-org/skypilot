"""Initial schema for sky config database

Revision ID: 001
Revises:
Create Date: 2024-01-01 12:00:00.000000

"""
# pylint: disable=invalid-name
from alembic import op
import sqlalchemy as sa

from sky.skypilot_config import Base
from sky.utils import db_utils

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create initial schema for config_yaml table"""
    # Create all tables with their current schema
    db_utils.add_tables_to_db_sqlalchemy(Base.metadata, op.get_bind())

def downgrade():
    """Drop all tables"""
    Base.metadata.drop_all(bind=op.get_bind()) 