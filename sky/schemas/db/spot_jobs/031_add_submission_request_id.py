"""Add submission_request_id column to job_info.

The id of the API server request that created the job and is submitting it to
the scheduler. The managed job status refresh uses it to fail a job whose
submission ended before the job reached the scheduler.

Revision ID: 031
Revises: 030
Create Date: 2026-11-26

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '031'
down_revision: Union[str, Sequence[str], None] = '030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add submission_request_id to job_info."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'submission_request_id', sa.Text())


def downgrade():
    """No downgrade logic."""
    pass
