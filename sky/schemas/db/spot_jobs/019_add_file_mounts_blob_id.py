"""Add file_mounts_blob_id column to job_info table.

This migration adds file_mounts_blob_id column so that uploaded file-mount
blobs can be ref-counted by long-lived managed jobs (including those in
QUEUED/RECOVERING states) rather than only by the requests that submitted
them.

Revision ID: 019
Revises: 018
Create Date: 2026-04-24

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '019'
down_revision: Union[str, Sequence[str], None] = '018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add file_mounts_blob_id column to job_info table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'file_mounts_blob_id',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
