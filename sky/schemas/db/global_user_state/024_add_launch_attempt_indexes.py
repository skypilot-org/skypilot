"""Add the launch_attempts lookup indexes.

Both statements they serve read the whole table without them, and both run
against a table that grows with the retention window rather than with the work
there is to do:

- The milestone writers hold the on-cloud name (it is what is stamped on the
  pods) and look up the in-flight attempt by either name. With only
  ``cluster_name`` indexed, neither branch of the OR could be used and the
  lookup became a scan: 21ms against 4us over 200k rows, several times per
  launch, on the provision path.
- The abandoned sweep runs once a minute and in the steady state finds
  nothing. On ``provision_start`` alone it had to read every row older than
  the bound -- nearly the whole table -- to discover that: 120ms per tick,
  inside a write transaction that blocks other writers for its duration.

A separate revision from 023 so that a database already stamped at 023 still
gets them.

Revision ID: 024
Revises: 023
Create Date: 2026-09-03

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '024'
down_revision: Union[str, Sequence[str], None] = '023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = (
    ('ix_launch_attempts_cluster_on_cloud', ['cluster_name_on_cloud']),
    ('ix_launch_attempts_open', ['outcome', 'provision_start']),
)


def upgrade():
    """Create the indexes that are not there yet."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'launch_attempts' not in inspector.get_table_names():
        # 023 owns creating it, so this should not happen -- but inspecting a
        # table that is not there raises, and a raising migration fails the
        # whole upgrade, which takes the server down over a metrics table.
        # Create it instead of either crashing or skipping: skipping would
        # leave such a database without the feature for good.
        with op.get_context().autocommit_block():
            db_utils.add_table_to_db_sqlalchemy(Base.metadata, bind,
                                                'launch_attempts')
            for index in Base.metadata.tables['launch_attempts'].indexes:
                index.create(bind=bind, checkfirst=True)
        return

    existing = {ix['name'] for ix in inspector.get_indexes('launch_attempts')}
    with op.get_context().autocommit_block():
        for name, columns in _INDEXES:
            if name not in existing:
                op.create_index(name, 'launch_attempts', columns)


def downgrade():
    """No-op for backward compatibility."""
    pass
