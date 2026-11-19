"""Persist cluster_name_on_cloud per task for finished-job Telemetry.

job_info.cluster_name_on_cloud (031) is job-scoped, so multi-task jobs
overwrite each other on every launch. Storing the name on the spot row
(keyed by managed job id + task id) keeps each finished task's Grafana
var-cluster correct after its own cluster is torn down.

Revision ID: 032
Revises: 031
Create Date: 2026-11-16

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '032'
down_revision: Union[str, Sequence[str], None] = '031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add cluster_name_on_cloud to spot (per-task Telemetry identity)."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('spot',
                                             'cluster_name_on_cloud',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
