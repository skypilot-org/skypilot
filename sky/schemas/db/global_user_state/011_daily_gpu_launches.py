"""Add table for tracking daily GPU launches.

Revision ID: 011
Revises: 010
Create Date: 2025-11-16

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '011'
down_revision: Union[str, Sequence[str], None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add new table for daily GPU launches."""
    with op.get_context().autocommit_block():
        # Add new table for daily GPU launches.
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'daily_gpu_launches')


def downgrade():
    pass
