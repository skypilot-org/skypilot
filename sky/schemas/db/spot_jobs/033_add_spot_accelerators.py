"""Persist per-task accelerators for finished-job Telemetry GPU panels.

After teardown the queue no longer has a live handle, so accelerators was
null and the dashboard hid GPU panels even when Prometheus still held the
series. Storing the launched accelerators dict on the spot row (with the
per-task Telemetry identity) restores hasAccelerator for finished GPU jobs.

Revision ID: 033
Revises: 032
Create Date: 2026-11-16

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '033'
down_revision: Union[str, Sequence[str], None] = '032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add accelerators JSON column to spot."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('spot',
                                             'accelerators',
                                             sa.JSON(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
