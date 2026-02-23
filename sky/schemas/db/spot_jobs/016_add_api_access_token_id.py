"""Add api_access_token_id column to job_info table.

This migration adds api_access_token_id column to store the token ID of the
API access token created for a managed job with api_access enabled, so the
token can be cleaned up when the job completes.

Revision ID: 016
Revises: 015
Create Date: 2026-02-23

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '016'
down_revision: Union[str, Sequence[str], None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add api_access_token_id column to job_info table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'api_access_token_id',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
