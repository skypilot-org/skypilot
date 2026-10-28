"""Add an index for the cluster_events lookups keyed on the name column.

cluster_events is keyed (cluster_hash, reason, transitioned_at), but two
readers filter on the ``name`` column instead, because it is the only one
that survives a cluster's teardown:
get_latest_cluster_events (the details column of every `sky jobs queue -v`)
and get_cluster_events_by_name (the job-events timeline). Neither name nor
type is indexed, so both were full-table scans over every cluster's whole
history.

Revision ID: 023
Revises: 022
Create Date: 2026-09-09

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

_INDEX_NAME = 'ix_cluster_events_name_type'


def _existing_indexes(table_name: str) -> set:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {ix['name'] for ix in inspector.get_indexes(table_name)}


def upgrade():
    """Create the index if it doesn't already exist.

    Idempotent, and inside an autocommit block so PostgreSQL accepts the
    implicit transactional DDL and SQLite runs the statement directly --
    the same pattern as the spot_jobs index migrations.
    """
    if _INDEX_NAME in _existing_indexes('cluster_events'):
        return
    with op.get_context().autocommit_block():
        # transitioned_at trails the equality columns so the ORDER BY of
        # both readers is served by the index rather than by a sort.
        op.create_index(_INDEX_NAME, 'cluster_events',
                        ['name', 'type', 'transitioned_at'])


def downgrade():
    """No-op for backward compatibility."""
    pass
