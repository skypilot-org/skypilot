"""Add controller_server_id column to job_info table.

This migration adds an optional controller_server_id column to the job_info
table. It identifies which server instance's controller claimed a job, so
that liveness checks can tell whether the process that claimed a job even
ran on this machine (see sky/jobs/controller_liveness.py). NULL preserves
today's behavior: a local pid check.

Revision ID: 024
Revises: 021
Create Date: 2026-07-14

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '024'
down_revision: Union[str, Sequence[str], None] = '021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add controller_server_id column to job_info table."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'controller_server_id',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No-op downgrade for controller_server_id column."""
    pass
