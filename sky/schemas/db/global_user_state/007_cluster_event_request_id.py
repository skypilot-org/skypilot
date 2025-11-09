"""Add request_id to cluster_events.

Revision ID: 007
Revises: 006
Create Date: 2025-08-28

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '007'
down_revision: Union[str, Sequence[str], None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add request_id column to cluster_events."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('cluster_events',
                                             'request_id',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
