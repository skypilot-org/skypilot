"""fix initial revision

Revision ID: 003
Revises: 002
Create Date: 2025-08-07

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, Sequence[str], None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.get_context().autocommit_block():
        # add missing columns to clusters table
        db_utils.add_column_to_table_alembic('clusters',
                                             'storage_mounts_metadata',
                                             sa.LargeBinary(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'cluster_ever_up',
                                             sa.Integer(),
                                             server_default='0')
        db_utils.add_column_to_table_alembic('clusters',
                                             'status_updated_at',
                                             sa.Integer(),
                                             server_default=None)

        # remove mistakenly added columns
        db_utils.drop_column_from_table_alembic('clusters', 'launched_nodes')
        db_utils.drop_column_from_table_alembic('clusters', 'disk_tier')
        db_utils.drop_column_from_table_alembic('clusters',
                                                'config_hash_locked')
        db_utils.drop_column_from_table_alembic('clusters', 'handle_locked')
        db_utils.drop_column_from_table_alembic('clusters', 'num_failures')
        db_utils.drop_column_from_table_alembic('clusters', 'configs')


def downgrade() -> None:
    """Downgrade schema."""
    pass
