"""Record the creator and workspace of each storage.

Revision ID: 028
Revises: 027
Create Date: 2026-10-28

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

import sqlalchemy as sa

from sky.skylet import constants
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '028'
down_revision: Union[str, Sequence[str], None] = '027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add user_hash and workspace columns to the storage table.

    Existing rows keep no creator and land in the default workspace, the
    same way the volumes table was extended.
    """
    from alembic import op  # pylint: disable=import-outside-toplevel

    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('storage',
                                             'user_hash',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic(
            'storage',
            'workspace',
            sa.Text(),
            server_default=constants.SKYPILOT_DEFAULT_WORKSPACE)


def downgrade():
    """No-op for backward compatibility."""
    pass
