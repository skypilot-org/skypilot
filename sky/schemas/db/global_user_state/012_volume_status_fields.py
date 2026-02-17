"""Add volume status fields for error tracking and usage caching.

Revision ID: 012
Revises: 011
Create Date: 2025-01-08

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '012'
down_revision: Union[str, Sequence[str], None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add error_message and usedby columns to volumes table.

    - error_message: Stores error/status message for NOT_READY volumes
    - usedby_pods: JSON-encoded list of pods using the volume
    - usedby_clusters: JSON-encoded list of clusters using the volume
    """
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('volumes',
                                             'error_message',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('volumes',
                                             'usedby_pods',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('volumes',
                                             'usedby_clusters',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
