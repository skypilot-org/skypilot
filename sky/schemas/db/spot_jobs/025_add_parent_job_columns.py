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
        db_utils.add_column_to_table_alembic('job_info',
                                             'root_job_id',
                                             sa.Integer(),
                                             index=True)
        db_utils.add_column_to_table_alembic('job_info',
                                             'parent_job_id',
                                             sa.Integer(),
                                             index=True)
        db_utils.add_column_to_table_alembic('job_info', 'parent_task_id',
                                             sa.Integer())


def downgrade():
    """No downgrade logic."""
    pass
