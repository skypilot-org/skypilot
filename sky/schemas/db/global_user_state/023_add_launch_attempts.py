"""Add launch_attempts table for launch latency breakdown.

Revision ID: 023
Revises: 022
Create Date: 2026-09-02

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '023'
down_revision: Union[str, Sequence[str], None] = '022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create the launch_attempts table if it does not already exist."""
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, bind,
                                            'launch_attempts')
        # Creating the table brings its indexes with it, but checkfirst skips
        # the whole statement when the table is already there, and then the
        # indexes are never created. Make the revision idempotent instead of
        # half-applied. (A database already stamped at this revision is not
        # reached by this -- alembic will not re-run it -- which is what 024
        # is for.)
        for index in Base.metadata.tables['launch_attempts'].indexes:
            index.create(bind=bind, checkfirst=True)


def downgrade():
    """No-op for backward compatibility."""
    pass
