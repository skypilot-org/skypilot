"""Add dashboard_dismissed_items table.

Revision ID: 016
Revises: 015
Create Date: 2026-02-27

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '016'
down_revision: Union[str, Sequence[str], None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create dashboard_dismissed_items table."""
    op.create_table(
        'dashboard_dismissed_items',
        sa.Column('item_type', sa.Text(), primary_key=True),
        sa.Column('item_id', sa.Text(), primary_key=True),
        sa.Column('user_hash', sa.Text()),
        sa.Column('dismissed_at', sa.Integer()),
    )


def downgrade():
    """Drop dashboard_dismissed_items table."""
    op.drop_table('dashboard_dismissed_items')
