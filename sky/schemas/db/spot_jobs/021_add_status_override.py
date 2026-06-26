"""Add status_override column to spot table.

This migration adds an optional status_override column to the spot table. The
core managed-job state machine never reads it; read paths may surface it in
place of `status` via the optional status_expr seam, letting a plugin present a
refined user-facing status without changing the job lifecycle.

Revision ID: 021
Revises: 020
Create Date: 2026-06-24

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '021'
down_revision: Union[str, Sequence[str], None] = '020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add status_override column to spot table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('spot',
                                             'status_override',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
