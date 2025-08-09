"""Columns for whether the cluster is managed.

Revision ID: 005
Revises: 004
Create Date: 2025-08-08

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.π
revision: str = '005'
down_revision: Union[str, Sequence[str], None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add new table for cluster events."""
    with op.get_context().autocommit_block():
        # Add new table for cluster events.
        db_utils.add_tables_to_db_sqlalchemy(Base.metadata, op.get_bind())

        db_utils.add_column_to_table_alembic('cluster_events',
                                             'cluster_hash',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('cluster_events',
                                             'name',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('cluster_events',
                                             'starting_status',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('cluster_events',
                                             'ending_status',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('cluster_events',
                                             'reason',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('cluster_events',
                                             'transitioned_at',
                                             sa.Integer(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('cluster_events',
                                             'type',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    pass
