"""Index the sourced job_events rows for the recovery-event metric.

The Prometheus collector counts job_events rows that carry a
recovery_source, grouped by source (see
sky/jobs/state.py::get_recovery_event_counts_by_source_workspace). Those
rows are every recovery or emergency attempt, whatever status they
carry: a RECOVERING relaunch, a RUNNING task kept on its cluster through
an emergency, a job group's STARTING members. The query used to list the
statuses so that the (new_status, recovery_source) index of migration 023
stayed usable; a partial index on exactly the sourced rows lets it drop
that list. Partial, following 027/028: sourced rows are a small fraction
of job_events, which is the largest table in the managed-jobs DB on busy
deployments. 023 stays for the consumers that filter by status and
source.

Revision ID: 030
Revises: 029
Create Date: 2026-10-08

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '030'
down_revision: Union[str, Sequence[str], None] = '029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = 'ix_job_events_recovery_source_sourced'
_PREDICATE = 'recovery_source IS NOT NULL'


def _existing_indexes(table_name: str) -> set:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {ix['name'] for ix in inspector.get_indexes(table_name)}


def upgrade():
    """Create the index if it doesn't already exist.

    Idempotent, and created inside an autocommit block so PostgreSQL is
    happy with implicit transactional DDL and SQLite can run the
    statement directly (same pattern as migration 023).
    """
    if _INDEX_NAME in _existing_indexes('job_events'):
        return
    with op.get_context().autocommit_block():
        op.create_index(_INDEX_NAME,
                        'job_events', ['recovery_source'],
                        postgresql_where=sa.text(_PREDICATE),
                        sqlite_where=sa.text(_PREDICATE))


def downgrade():
    """No-op for backward compatibility."""
    pass
