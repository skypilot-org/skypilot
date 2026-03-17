"""Add cluster_api_access_tokens table.

This migration creates a separate cluster_api_access_tokens table to store
the token ID of the API access token created for a cluster with api_access
enabled, so the token can be cleaned up when the cluster is terminated.

Revision ID: 016
Revises: 015
Create Date: 2026-03-16

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '016'
down_revision: Union[str, Sequence[str], None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create cluster_api_access_tokens table."""
    with op.get_context().autocommit_block():
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'cluster_api_access_tokens')


def downgrade():
    """Drop cluster_api_access_tokens table."""
    op.drop_table('cluster_api_access_tokens')
