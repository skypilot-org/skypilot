"""Add cluster_job_specs table for each cluster job's task YAML / entrypoint.

Revision ID: 026
Revises: 025
Create Date: 2026-10-13

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '026'
down_revision: Union[str, Sequence[str], None] = '025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create the cluster_job_specs table if it does not already exist."""
    with op.get_context().autocommit_block():
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'cluster_job_specs')


def downgrade():
    """No-op for backward compatibility."""
    pass
