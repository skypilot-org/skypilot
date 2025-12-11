"""Add job_status_transition table for tracking managed job status changes.

Revision ID: 008
Revises: 007
Create Date: 2025-12-06

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.jobs.state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '008'
down_revision: Union[str, Sequence[str], None] = '007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create job_status_transition table for tracking status changes."""
    with op.get_context().autocommit_block():
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'job_status_transition')


def downgrade():
    """Drop job_status_transition table."""
    pass
