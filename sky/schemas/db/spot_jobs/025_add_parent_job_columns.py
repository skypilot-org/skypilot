"""Add root_job_id, parent_job_id and parent_task_id columns to job_info.

A managed job launched from inside another managed job (for example an
eval job launched by the watcher task of a job group) records where it came
from. The links are written once on the child's row at launch and never
mutate the parent's rows, so a running job group's controller is not
touched when members are added dynamically.

- ``root_job_id``: the top-level job of the tree. Load-bearing: the group
  the job is shown under, and the lifecycle it shares (cancelled with the
  root, swept when the root's primary tasks finish). One indexed column
  answers both "which group" and "everything in this group".
- ``parent_job_id``: the job that launched this one. Equals the root for a
  direct member; differs only for a job launched by a dynamic member.
- ``parent_task_id``: the task within the parent that launched this one.
  Display only (only meaningful next to ``parent_job_id``).

All three are NULL for top-level jobs.

Revision ID: 025
Revises: 024
Create Date: 2026-09-09

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '025'
down_revision: Union[str, Sequence[str], None] = '024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add root_job_id (indexed), parent_job_id (indexed), parent_task_id."""
    with op.get_context().autocommit_block():
        # Nullable, no default: NULL is "top-level job", for existing jobs
        # and for any insert that leaves the columns out.
        for column_name in ('root_job_id', 'parent_job_id', 'parent_task_id'):
            db_utils.add_column_to_table_alembic('job_info', column_name,
                                                 sa.Integer())
        # Indexes created explicitly (as migrations 020/023/024 do) rather
        # than through Column(index=True), so they exist regardless of how
        # the installed Alembic handles the flag on add_column.
        bind = op.get_bind()
        existing = {
            ix['name'] for ix in sa.inspect(bind).get_indexes('job_info')
        }
        for column_name in ('root_job_id', 'parent_job_id'):
            index_name = f'ix_job_info_{column_name}'
            if index_name not in existing:
                op.create_index(index_name, 'job_info', [column_name])


def downgrade():
    """No downgrade logic."""
    pass
