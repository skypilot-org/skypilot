"""Add durable autodown intents.

Revision ID: 021
Revises: 020
Create Date: 2026-08-03

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '022'
down_revision: Union[str, Sequence[str], None] = '021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create the standalone autodown_intents table."""
    with op.get_context().autocommit_block():
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'autodown_intents')


def downgrade():
    """Drop the autodown_intents table."""
    op.drop_table('autodown_intents')
