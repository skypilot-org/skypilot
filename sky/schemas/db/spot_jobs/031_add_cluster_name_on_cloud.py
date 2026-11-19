"""Persist cluster_name_on_cloud for finished-job Grafana Telemetry.

After a managed job reaches a terminal state the live cluster handle is
gone, so the dashboard used to clear cluster_name_on_cloud and hide the
Telemetry section. Persisting the name (same pattern as cloud/region/zone)
lets the UI keep filtering Grafana with var-cluster for the job's
start_at→end_at window while Prometheus retention still holds the series.

Revision ID: 031
Revises: 030
Create Date: 2026-11-13

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '031'
down_revision: Union[str, Sequence[str], None] = '030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add cluster_name_on_cloud to job_info."""
    with op.get_context().autocommit_block():
        db_utils.add_column_to_table_alembic('job_info',
                                             'cluster_name_on_cloud',
                                             sa.Text(),
                                             server_default=None)


def downgrade():
    """No downgrade logic."""
    pass
