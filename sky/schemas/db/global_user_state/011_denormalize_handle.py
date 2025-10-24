"""Denormalize handle fields into separate columns to avoid pickle.loads() overhead.

Revision ID: 011
Revises: 010
Create Date: 2025-10-24

"""
# pylint: disable=invalid-name
import dataclasses
import json
import pickle
from typing import Any, Dict, Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '011'
down_revision: Union[str, Sequence[str], None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _serialize_cluster_info(cluster_info: Any) -> Optional[str]:
    """Convert ClusterInfo to JSON string."""
    if cluster_info is None:
        return None
    return json.dumps(dataclasses.asdict(cluster_info))


def upgrade():
    """Add denormalized handle columns and backfill from existing pickled handles."""
    with op.get_context().autocommit_block():
        # Add the seven new columns
        db_utils.add_column_to_table_alembic('clusters',
                                             'launched_nodes',
                                             sa.Integer(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'launched_resources',
                                             sa.LargeBinary(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'cluster_name_on_cloud',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'cluster_yaml_path',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'stable_internal_external_ips_json',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'stable_ssh_ports_json',
                                             sa.Text(),
                                             server_default=None)
        db_utils.add_column_to_table_alembic('clusters',
                                             'cached_cluster_info_json',
                                             sa.Text(),
                                             server_default=None)
    
    # Backfill existing clusters
    connection = op.get_bind()
    result = connection.execute(
        sa.text('SELECT name, handle FROM clusters WHERE handle IS NOT NULL'))
    
    for row in result:
        cluster_name = row[0]
        handle_bytes = row[1]
        
        try:
            # Unpickle the handle
            handle = pickle.loads(handle_bytes)
            
            # Extract fields
            launched_nodes = handle.launched_nodes
            launched_resources_bytes = pickle.dumps(handle.launched_resources)
            cluster_name_on_cloud = handle.cluster_name_on_cloud
            cluster_yaml_path = handle._cluster_yaml
            stable_internal_external_ips_json = (
                json.dumps(handle.stable_internal_external_ips)
                if handle.stable_internal_external_ips else None)
            stable_ssh_ports_json = (
                json.dumps(handle.stable_ssh_ports)
                if handle.stable_ssh_ports else None)
            cached_cluster_info_json = _serialize_cluster_info(
                handle.cached_cluster_info)
            
            # Update the row
            connection.execute(
                sa.text('''UPDATE clusters SET
                    launched_nodes = :launched_nodes,
                    launched_resources = :launched_resources,
                    cluster_name_on_cloud = :cluster_name_on_cloud,
                    cluster_yaml_path = :cluster_yaml_path,
                    stable_internal_external_ips_json = :stable_internal_external_ips_json,
                    stable_ssh_ports_json = :stable_ssh_ports_json,
                    cached_cluster_info_json = :cached_cluster_info_json
                WHERE name = :cluster_name'''),
                {
                    'launched_nodes': launched_nodes,
                    'launched_resources': launched_resources_bytes,
                    'cluster_name_on_cloud': cluster_name_on_cloud,
                    'cluster_yaml_path': cluster_yaml_path,
                    'stable_internal_external_ips_json': stable_internal_external_ips_json,
                    'stable_ssh_ports_json': stable_ssh_ports_json,
                    'cached_cluster_info_json': cached_cluster_info_json,
                    'cluster_name': cluster_name,
                })
        except Exception as e:  # pylint: disable=broad-except
            # Skip clusters that fail to unpickle
            print(f'Failed to migrate cluster {cluster_name}: {e}')
            continue


def downgrade():
    """No-op for backward compatibility."""
    pass
