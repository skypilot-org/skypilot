"""Columns for volume usedby information.

Revision ID: 013
Revises: 012
Create Date: 2025-01-08

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '013'
down_revision: Union[str, Sequence[str], None] = '012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add usedby columns to volumes table.

    These columns store JSON-encoded lists of pods/clusters using the volume.
    This allows volume_list to read from database without making API calls.
    """
    with op.get_context().autocommit_block():
        # Store as JSON text: '["pod1", "pod2"]'
        db_utils.add_column_to_table_alembic('volumes',
                                             'usedby_pods',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('volumes',
                                             'usedby_clusters',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """Remove usedby columns from volumes table."""
    pass
