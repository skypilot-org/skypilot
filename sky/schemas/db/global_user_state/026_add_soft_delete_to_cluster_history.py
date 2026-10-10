"""Add is_deleted column to cluster_history table.

Revision ID: 026
Revises: 025
Create Date: 2026-10-10

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '026'
down_revision: Union[str, Sequence[str], None] = '025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add is_deleted column for soft-deleting cluster history rows.

    Users can remove a terminated cluster from the history view (e.g. from
    the dashboard) without losing the underlying usage/cost record: the row
    is flagged instead of deleted, so `sky cost-report` and the dashboard
    history list skip it while the data stays recoverable.
    """
    from alembic import op  # pylint: disable=import-outside-toplevel

    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'is_deleted',
                                             sa.Integer(),
                                             server_default='0')


def downgrade():
    """No-op for backward compatibility."""
    pass
