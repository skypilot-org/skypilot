"""Columns for whether the cluster is managed.

Revision ID: 005
Revises: 004
Create Date: 2025-08-08

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.π
revision: str = '005'
down_revision: Union[str, Sequence[str], None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add new table for cluster events."""
    with op.get_context().autocommit_block():
        # Add new table for cluster events.
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'cluster_events')


def downgrade():
    pass
