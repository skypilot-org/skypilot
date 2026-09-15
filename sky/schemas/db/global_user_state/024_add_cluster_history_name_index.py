"""Add an index for the cluster_history lookups keyed on the name column.

cluster_history is keyed on cluster_hash, but the readers that have to
survive a cluster's teardown filter on the ``name`` column instead, because
the name -> hash mapping lives in the clusters table and is removed when the
cluster goes away: get_clusters_from_history(cluster_names=...) and
get_cluster_history_provision_log_path. The table is never pruned, so both
were full-table scans over every cluster this server has ever launched.

Revision ID: 024
Revises: 023
Create Date: 2026-09-12

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

_INDEX_NAME = 'ix_cluster_history_name'


def _existing_indexes(table_name: str) -> set:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {ix['name'] for ix in inspector.get_indexes(table_name)}


def upgrade():
    """Create the index if it doesn't already exist.

    Idempotent, and inside an autocommit block so PostgreSQL accepts the
    implicit transactional DDL and SQLite runs the statement directly --
    the same pattern as 023 and the spot_jobs index migrations.

    The index is built non-concurrently, so it holds a write lock on
    cluster_history for as long as the build takes, during the server-startup
    migration. That was judged acceptable: it is the same shape as 023, which
    indexes the larger cluster_events table, and a plain btree build over a
    single text column takes a few seconds even at 10^5 rows -- well inside
    the startup window, while nothing is writing to the table yet.
    """
    if _INDEX_NAME in _existing_indexes('cluster_history'):
        return
    with op.get_context().autocommit_block():
        op.create_index(_INDEX_NAME, 'cluster_history', ['name'])


def downgrade():
    """No-op for backward compatibility."""
    pass
