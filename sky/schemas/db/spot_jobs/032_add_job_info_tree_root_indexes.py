"""Index job_info by tree root, alone and under the user and workspace scopes.

A managed jobs queue page is a slice of trees ordered by their root's id, and
its cursor is a bound on that id. Indexing coalesce(root_job_id, spot_job_id)
lets a page read its rows from the index instead of sorting every job.

Raw SQL with IF NOT EXISTS: SQLAlchemy cannot reflect an expression index on
SQLite, so the usual inspect-then-create check would not see these.

A plain build, not CONCURRENTLY: each index blocks writes to job_info only
while it builds, well under a second at 100k jobs. CONCURRENTLY waits for every
open transaction on the table and would stall server startup behind an idle one,
and a failed concurrent build leaves an invalid index that IF NOT EXISTS skips.

Revision ID: 032
Revises: 031
Create Date: 2026-11-27

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.jobs import state_db

# revision identifiers, used by Alembic.
revision: str = '032'
down_revision: Union[str, Sequence[str], None] = '031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create the indexes that don't exist yet."""
    with op.get_context().autocommit_block():
        for name, leading in state_db.TREE_ROOT_INDEXES.items():
            columns = ', '.join([*leading, state_db.TREE_ROOT_SQL])
            op.execute(f'CREATE INDEX IF NOT EXISTS {name} '
                       f'ON job_info ({columns})')


def downgrade():
    """No-op for backward compatibility."""
    pass
