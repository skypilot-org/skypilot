"""Hyperbolic provision module for SkyPilot."""
from typing import Any, Dict, Optional

from sky.provision.hyperbolic.instance import query_instances


def bootstrap_instances(config: Dict[str, Any], **kwargs) -> None:
    """Bootstrap instances for Hyperbolic."""
    del config, kwargs  # unused


def get_cluster_info(region: str, cluster_name: str,
                     **kwargs) -> Dict[str, Any]:
    """Get cluster information for Hyperbolic."""
    del region, cluster_name, kwargs  # unused
    return {}


def open_ports(cluster_name: str, ports: list, **kwargs) -> None:
    """Open ports for Hyperbolic."""
    del cluster_name, ports, kwargs  # unused


def cleanup_ports(cluster_name: str, **kwargs) -> None:
    """Cleanup ports for Hyperbolic."""
    del cluster_name, kwargs  # unused


def bulk_provision(config, **kwargs):
    # Implement your provisioning logic here.
    # This function should return a record of the provisioned nodes.
    return {
        'head_node_type': 'ray.head.default',
        'worker_node_types': ['ray.worker.default']
    }


def _query_cluster_status_via_cloud_api(handle):
    """Query the status of nodes in the cluster via Hyperbolic's API."""
    cluster_name_on_cloud = getattr(handle, 'cluster_name', None)
    provider_config = getattr(handle, 'provider_config', None)
    instance_statuses = query_instances(cluster_name_on_cloud, provider_config)
    node_statuses = []
    for name, status in instance_statuses.items():
        node_statuses.append({
            'name': name,
            'status': status,
            'instance_id': name,
            'public_ip': '127.0.0.1',  # Placeholder
            'private_ip': '127.0.0.1',  # Placeholder
        })
    return node_statuses


def cluster_name_in_hint(cluster_name_on_cloud, cluster_name):
    """Check if a node's name matches the cluster name pattern.
    
    Args:
        cluster_name_on_cloud: The name of the node on the cloud.
        cluster_name: The name of the cluster.
    
    Returns:
        True if the node's name matches the cluster name pattern, False otherwise.
    """
    if cluster_name_on_cloud is None:
        print(
            f"[DEBUG] cluster_name_on_cloud is None! cluster_name={cluster_name}"
        )
        return False
    return cluster_name_on_cloud.startswith(cluster_name)
