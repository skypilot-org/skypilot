"""Track job claim identity.

Revision ID: 023
Revises: 022
Create Date: 2026-06-13

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '023'
down_revision: Union[str, Sequence[str], None] = '022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add claim_id column to job_info."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'claim_id',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No-op downgrade for claim_id column."""
    pass
