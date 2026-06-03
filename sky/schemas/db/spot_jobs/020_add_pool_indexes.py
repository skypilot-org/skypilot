"""Add indexes for pool dashboard queries.

The pool dashboard executes per-pool job lookups (via
`get_nonterminal_job_ids_by_pool` in `sky/jobs/state.py`) and `skip_finished`
queue scans on every refresh. Without these indexes both code paths fall back
to full scans of the `spot` and `job_info` tables, which becomes >1min on
deployments with tens of thousands of finished jobs and only a handful of
pools.

Indexes added:
  - job_info.pool: every per-pool query filters on this column.
  - job_info.current_cluster_name: per-replica `used_by` lookups filter on
    this column.
  - spot.status: non-terminal filtering (NOT IN (terminal_statuses)) is on
    the hot path for both pool_status and the dashboard's skip_finished
    managed-jobs fetch.

Revision ID: 020
Revises: 019
Create Date: 2026-05-18

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '020'
down_revision: Union[str, Sequence[str], None] = '019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = (
    ('ix_job_info_pool', 'job_info', ['pool']),
    ('ix_job_info_current_cluster_name', 'job_info', ['current_cluster_name']),
    ('ix_spot_status', 'spot', ['status']),
)


def _existing_indexes(table_name: str) -> set:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {ix['name'] for ix in inspector.get_indexes(table_name)}


def upgrade():
    """Create pool-dashboard indexes if they don't already exist.

    Idempotent: skips any index that already exists. Each index is created
    inside its own autocommit block so PostgreSQL is happy with implicit
    transactional DDL and SQLite can run the statements directly.
    """
    for index_name, table_name, columns in _INDEXES:
        if index_name in _existing_indexes(table_name):
            continue
        with op.get_context().autocommit_block():
            op.create_index(index_name, table_name, columns)


def downgrade():
    """No-op for backward compatibility."""
    pass
