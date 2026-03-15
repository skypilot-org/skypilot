"""Add node_names column to clusters and cluster_history tables.

Revision ID: 015
Revises: 014
Create Date: 2026-02-04

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '015'
down_revision: Union[str, Sequence[str], None] = '014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add node_names column for dashboard display."""
    from alembic import op  # pylint: disable=import-outside-toplevel

    with op.get_context().autocommit_block():
        # Add node_names column to clusters table
        db_utils.add_column_to_table_alembic('clusters',
                                             'node_names',
                                             sa.Text(),
                                             server_default=None)

        # Add node_names column to cluster_history table
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'node_names',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """Remove node_names column."""
    pass
