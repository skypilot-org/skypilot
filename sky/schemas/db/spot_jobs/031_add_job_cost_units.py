"""Add job_info.cost_units.

The cost units a claimed job counts for against its controller process's job
cap (see sky/jobs/controller.py::ControllerManager._claimed_load), written by
the controller that holds the job whenever the cost changes. NULL means the
full cost (controller_utils.JOB_COST_UNITS): every job before this revision,
and every job not yet claimed. Readers outside the controller (the stall
scan's claim gates) sum it, and a controller that claims the job again after
a restart starts from it.

Revision ID: 031
Revises: 030
Create Date: 2026-10-27

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
    """Add the cost_units column."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'cost_units',
                                             sa.Integer(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
