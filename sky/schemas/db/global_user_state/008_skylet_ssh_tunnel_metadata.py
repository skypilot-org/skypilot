"""Add skylet_ssh_tunnel_metadata to clusters.

Revision ID: 008
Revises: 007
Create Date: 2025-09-09

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '008'
down_revision: Union[str, Sequence[str], None] = '007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add skylet_ssh_tunnel_metadata column to clusters."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('clusters',
                                             'skylet_ssh_tunnel_metadata',
                                             sa.LargeBinary(),
                                             server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
