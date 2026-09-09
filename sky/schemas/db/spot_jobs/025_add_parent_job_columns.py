"""Add parent_job_id and parent_task_id columns to job_info table.

A managed job launched from inside another managed job (for example an
eval job launched by the watcher task of a job group) records the launching
job and task here. The link is written once on the child's row at launch
and never mutates the parent's rows, so a running job group's controller
is not touched when members are added dynamically.

``parent_job_id`` is indexed: descendant lookups (cancel cascade, queue and
dashboard grouping) filter on it.

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
    """Add parent_job_id (indexed) and parent_task_id to job_info."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'parent_job_id',
                                             sa.Integer(),
                                             server_default=None,
                                             index=True)
        db_utils.add_column_to_table_alembic('job_info',
                                             'parent_task_id',
                                             sa.Integer(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
