"""Add creation_yaml column to volumes table.

Revision ID: 016
Revises: 015
Create Date: 2026-03-17

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '016'
down_revision: Union[str, Sequence[str], None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add creation_yaml column to volumes table.

    Stores the YAML configuration used to create the volume, so it can be
    displayed in the dashboard volume detail page.
    """
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('volumes',
                                             'creation_yaml',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
