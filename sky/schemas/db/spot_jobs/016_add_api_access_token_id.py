"""Add api_access_tokens table.

This migration creates a separate api_access_tokens table to store the token
ID of the API access token created for a managed job with api_access enabled,
so the token can be cleaned up when the job completes.

Revision ID: 016
Revises: 015
Create Date: 2026-02-23

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '016'
down_revision: Union[str, Sequence[str], None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create api_access_tokens table."""
    op.create_table(
        'api_access_tokens',
        sa.Column('job_id', sa.Integer, primary_key=True),
        sa.Column('token_id', sa.Text, nullable=False),
    )


def downgrade():
    """Drop api_access_tokens table."""
    op.drop_table('api_access_tokens')
