"""Columns for whether the cluster is managed.

Revision ID: 012
Revises: 011
Create Date: 2025-12-05

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.π
revision: str = '012'
down_revision: Union[str, Sequence[str], None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add new table for cluster failures."""
    with op.get_context().autocommit_block():
        # Add new table for cluster failures.
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'cluster_failures')


def downgrade():
    pass
