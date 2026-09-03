"""Add an index for the metrics daemon's pending-timeline query.

The daemon asks once a minute for the tasks that have started but have no
timeline written yet. Without an index that is a full scan of ``spot`` plus a
sort, to return the handful of jobs that just started -- and in the steady
state, to return nothing at all. Leading with ``t_time_to_running`` seeks
straight to the rows without a timeline, and ``start_at`` then serves the
ordering from the index rather than a temp b-tree: 18ms against nothing
measurable over 200k rows, and it grows with the job history rather than with
the work there is to do.

Revision ID: 026
Revises: 025
Create Date: 2026-09-03

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '026'
down_revision: Union[str, Sequence[str], None] = '025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = 'ix_spot_pending_timeline'


def upgrade():
    """Create the pending-timeline index if it does not already exist."""
    bind = op.get_bind()
    existing = {ix['name'] for ix in sa.inspect(bind).get_indexes('spot')}
    if _INDEX_NAME in existing:
        return
    with op.get_context().autocommit_block():
        op.create_index(_INDEX_NAME, 'spot', ['t_time_to_running', 'start_at'])


def downgrade():
    """No-op for backward compatibility."""
    pass
