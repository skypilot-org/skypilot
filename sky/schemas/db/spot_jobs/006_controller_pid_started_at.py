"""Track controller PID start times.

Revision ID: 006
Revises: 005
Create Date: 2025-10-31

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, Sequence[str], None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add controller PID start time column."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'controller_pid_started_at',
                                             sa.Float(),
                                             server_default=None)


def downgrade():
    """No-op downgrade for controller PID start time column."""
    pass
