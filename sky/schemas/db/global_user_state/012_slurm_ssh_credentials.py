"""Add table to store Slurm SSH private keys per user and Slurm cluster.

Revision ID: 012
Revises: 011
Create Date: 2025-12-18

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.global_user_state import Base
from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '012'
down_revision: Union[str, Sequence[str], None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Create slurm_user_ssh_keys table."""
    with op.get_context().autocommit_block():
        db_utils.add_table_to_db_sqlalchemy(Base.metadata, op.get_bind(),
                                            'slurm_user_ssh_keys')


def downgrade():
    """No-op for backward compatibility."""
    pass
