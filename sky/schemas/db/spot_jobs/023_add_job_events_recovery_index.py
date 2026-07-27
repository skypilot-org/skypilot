"""Add an index for recovery-event metric queries on job_events.

The Prometheus collector counts RECOVERING job_events rows grouped by
recovery_source on every cache refresh (see
sky/jobs/state.py::get_recovery_event_counts_by_source_workspace).
job_events is the largest table in the managed-jobs DB on busy
deployments and neither new_status nor recovery_source is indexed, so
without this index every refresh is a full-table scan.

Revision ID: 023
Revises: 022
Create Date: 2026-07-21

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '023'
down_revision: Union[str, Sequence[str], None] = '022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = 'ix_job_events_new_status_recovery_source'


def _existing_indexes(table_name: str) -> set:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {ix['name'] for ix in inspector.get_indexes(table_name)}


def upgrade():
    """Create the index if it doesn't already exist.

    Idempotent, and created inside an autocommit block so PostgreSQL is
    happy with implicit transactional DDL and SQLite can run the
    statement directly (same pattern as migration 020).
    """
    if _INDEX_NAME in _existing_indexes('job_events'):
        return
    with op.get_context().autocommit_block():
        op.create_index(_INDEX_NAME, 'job_events',
                        ['new_status', 'recovery_source'])


def downgrade():
    """No-op for backward compatibility."""
    pass
