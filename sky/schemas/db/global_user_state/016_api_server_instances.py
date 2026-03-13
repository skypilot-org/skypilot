"""Add api_server_instances table for multi-server detection.

Revision ID: 016
Revises: 015
Create Date: 2026-03-12

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '016'
down_revision: Union[str, Sequence[str], None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add api_server_instances table."""
    with op.get_context().autocommit_block():
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'api_server_instances')


def downgrade():
    pass
