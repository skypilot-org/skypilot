"""Change job_events timestamp column to support timezone.

Revision ID: 010
Revises: 009
Create Date: 2025-12-22

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '010'
down_revision: Union[str, Sequence[str], None] = '009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Change timestamp column to TIMESTAMP WITH TIME ZONE.

    This only affects PostgreSQL - SQLite stores datetimes as text and handles
    timezone-aware datetimes automatically.
    """
    bind = op.get_bind()

    if bind.dialect.name == 'postgresql':
        # For PostgreSQL, change TIMESTAMP to TIMESTAMPTZ
        # The USING clause converts existing naive timestamps to UTC
        with op.get_context().autocommit_block():
            op.alter_column('job_events',
                            'timestamp',
                            type_=sa.DateTime(timezone=True),
                            existing_type=sa.DateTime(timezone=False),
                            postgresql_using='timestamp AT TIME ZONE \'UTC\'')
    # SQLite: no migration needed, timezone support is handled by SQLAlchemy


def downgrade():
    """No downgrade logic."""
    pass
