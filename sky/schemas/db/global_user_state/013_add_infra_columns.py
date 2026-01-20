"""Add cloud, region, zone columns to clusters and cluster_history tables.

Revision ID: 013
Revises: 012
Create Date: 2026-01-20

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '013'
down_revision: Union[str, Sequence[str], None] = '012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add cloud, region, zone columns for infrastructure filtering."""
    from alembic import op  # pylint: disable=import-outside-toplevel

    with op.get_context().autocommit_block():
        # Add columns to clusters table
        db_utils.add_column_to_table_alembic('clusters',
                                             'cloud',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'region',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'zone',
                                             sa.Text(),
                                             server_default=None)

        # Add columns to cluster_history table
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'cloud',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'region',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'zone',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """Remove cloud, region, zone columns."""
    pass
