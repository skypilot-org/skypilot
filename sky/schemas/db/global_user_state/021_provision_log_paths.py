"""Add provision_log_paths to cluster_history.

Revision ID: 021
Revises: 020
Create Date: 2026-07-13

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '021'
down_revision: Union[str, Sequence[str], None] = '020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add provision_log_paths (JSON list, oldest first) to cluster_history.

    The scalar provision_log_path only holds the latest launch try; a
    managed-job recovery re-launches the same cluster name in place, so the
    pre-recovery tries' provision logs were unrecoverable from the DB. The
    list column keeps every try so the debug dump can collect all of them.
    """
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'provision_log_paths',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No-op for backward compatibility."""
    pass
