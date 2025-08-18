"""Add provision_log_path to clusters and cluster_history.

Revision ID: 006
Revises: 005
Create Date: 2025-08-12

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, Sequence[str], None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add provision_log_path columns."""
    with op.get_context().autocommit_block():
        # clusters.provision_log_path
        db_utils.add_column_to_table_alembic('clusters',
                                             'provision_log_path',
                                             sa.Text(),
                                             server_default=None)

        # cluster_history.provision_log_path
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'provision_log_path',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
