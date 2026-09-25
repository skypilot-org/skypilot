"""Add job_dependencies table.

Each row says that the managed job ``spot_job_id`` waits for the managed job
``depends_on_job_id``: a controller claims it only once all of its
dependencies are DONE, and it runs only if all of them succeeded.

Revision ID: 029
Revises: 028
Create Date: 2026-09-25

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.jobs.state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '029'
down_revision: Union[str, Sequence[str], None] = '028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create the job_dependencies table."""
    with op.get_context().autocommit_block():
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'job_dependencies')


def downgrade():
    """No downgrade logic."""
    pass
