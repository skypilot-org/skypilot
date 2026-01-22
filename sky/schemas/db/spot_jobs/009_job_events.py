"""Add job_events table for tracking managed job events.

Revision ID: 009
Revises: 008
Create Date: 2025-12-11

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.jobs.state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '009'
down_revision: Union[str, Sequence[str], None] = '008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create job_events table for tracking job events."""
    with op.get_context().autocommit_block():
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'job_events')


def downgrade():
    """Drop job_events table."""
    pass
