"""Add dynamic_task_index and dynamic_task_count columns to job_info.

A job launched from inside a job group (a dynamic task, see migration 025)
gets a stable ordinal within its tree so it reads and is addressed like a
task of the group: the group's own tasks are 0..n-1, dynamic tasks number
on from n in the order they attached, and ``<root job id>-<index>`` names
one in ``sky jobs cancel`` and ``sky jobs logs``.

- ``dynamic_task_index``: the member's ordinal within its root's tree.
  NULL for top-level jobs.
- ``dynamic_task_count``: on the root's row, how many dynamic tasks have
  attached so far. Incremented atomically when a member attaches, which is
  what makes the index assignment race-free without a lock of our own: one
  UPDATE on one row is serialized by the database. NULL means 0.

A unique index on (root_job_id, dynamic_task_index) is the safety net; with
the counter it never fires.

Revision ID: 026
Revises: 025
Create Date: 2026-09-11

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '026'
down_revision: Union[str, Sequence[str], None] = '025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add dynamic_task_index and dynamic_task_count, plus the unique index."""
    with op.get_context().autocommit_block():
        for column_name in ('dynamic_task_index', 'dynamic_task_count'):
            db_utils.add_column_to_table_alembic('job_info', column_name,
                                                 sa.Integer())
        bind = op.get_bind()
        existing = {
            ix['name'] for ix in sa.inspect(bind).get_indexes('job_info')
        }
        index_name = 'ux_job_info_root_dynamic_task_index'
        if index_name not in existing:
            op.create_index(index_name,
                            'job_info', ['root_job_id', 'dynamic_task_index'],
                            unique=True)


def downgrade():
    """No downgrade logic."""
    pass
