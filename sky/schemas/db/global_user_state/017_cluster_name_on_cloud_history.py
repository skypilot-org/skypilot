"""Add cluster_name_on_cloud column to cluster_history table.

Revision ID: 017
Revises: 016
Create Date: 2026-03-27

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '017'
down_revision: Union[str, Sequence[str], None] = '016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add cluster_name_on_cloud column to cluster_history table.

    Persists the cloud-provider cluster name so GPU metrics can be
    displayed for terminated clusters via Grafana.
    """
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'cluster_name_on_cloud',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
