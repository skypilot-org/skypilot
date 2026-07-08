"""Add is_managed column to cluster_history table.

Revision ID: 020
Revises: 019
Create Date: 2026-06-18

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '020'
down_revision: Union[str, Sequence[str], None] = '019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add is_managed column to mirror the clusters table.

    This lets history queries (e.g. the dashboard cost report) filter out
    clusters launched by a controller (managed jobs and services) even after
    they are terminated, when the clusters table row is gone.
    """
    from alembic import op  # pylint: disable=import-outside-toplevel

    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'is_managed',
                                             sa.Integer(),
                                             server_default='0')


def downgrade():
    """No-op for backward compatibility."""
    pass
