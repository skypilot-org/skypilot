"""Add index on job_info.schedule_state for scheduler queries.

The job scheduler filters on ``schedule_state`` on every scheduling
attempt: ``get_waiting_job_async`` (outer query and the busy-batch-pools
subquery), ``get_num_launching_jobs``, ``get_num_alive_jobs``, and
``get_managed_jobs_highest_priority`` in ``sky/jobs/state.py``. Since
``maybe_schedule_next_jobs`` runs on every job state transition, these
queries are the hottest path in the spot DB. Without an index each one
is a full scan of ``job_info``; on a deployment with tens of thousands
of finished jobs this is ~10ms per call and dominates DB time during
bursts of concurrent job submissions. Active-state rows are always a
tiny fraction of the table, so an index on ``schedule_state`` is highly
selective for these queries.

Revision ID: 024
Revises: 023
Create Date: 2026-07-28

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '024'
down_revision: Union[str, Sequence[str], None] = '023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = 'ix_job_info_schedule_state'


def upgrade():
    """Create the schedule_state index if it doesn't already exist."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {ix['name'] for ix in inspector.get_indexes('job_info')}
    if _INDEX_NAME in existing:
        return
    with op.get_context().autocommit_block():
        op.create_index(_INDEX_NAME, 'job_info', ['schedule_state'])


def downgrade():
    """No-op for backward compatibility."""
    pass
