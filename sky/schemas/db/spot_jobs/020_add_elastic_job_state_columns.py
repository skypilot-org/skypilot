"""Add below_min_since and all_ready_at columns to spot table.

Two nullable unix-epoch float timestamps tracking elastic-managed-job
state transitions:
- below_min_since: most recent moment (running + succeeded) < min_nodes
- all_ready_at: first moment running == launched node count

Revision ID: 020
Revises: 019
Create Date: 2026-05-13
"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

revision: str = '020'
down_revision: Union[str, Sequence[str], None] = '019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add below_min_since and all_ready_at columns to spot table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic(
            'spot', 'below_min_since', sa.Float(), server_default=None)
        db_utils.add_column_to_table_alembic(
            'spot', 'all_ready_at', sa.Float(), server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
