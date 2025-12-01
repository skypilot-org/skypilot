"""Utility functions for generating instance links for cloud providers."""
from typing import Dict, Optional

from sky.provision import common


def generate_instance_links(
    cluster_info: common.ClusterInfo,
    region: Optional[str] = None,
) -> Dict[str, str]:
    """Generate instance links for a cluster based on the cloud provider.

    Args:
        cluster_info: ClusterInfo object containing instance information.
        region: Optional region string (used for some cloud providers).

    Returns:
        Dictionary mapping link labels to URLs. Empty dict if links cannot be
        generated (e.g., for Kubernetes or unsupported clouds).
    """
    links: Dict[str, str] = {}
    provider_name = cluster_info.provider_name.lower()
    provider_config = cluster_info.provider_config or {}

    # Skip Kubernetes and other non-cloud providers
    if provider_name in ('kubernetes', 'local'):
        return links

    # Get head instance ID
    head_instance_id = cluster_info.head_instance_id
    if not head_instance_id:
        return links

    if provider_name == 'aws':
        # AWS EC2 Console URL format:
        # https://{region}.console.aws.amazon.com/ec2/v2/home?region={region}#Instances:instanceId={instance_id}
        aws_region = region or provider_config.get('region', 'us-east-1')
        instance_url = (
            f'https://{aws_region}.console.aws.amazon.com/ec2/v2/home'
            f'?region={aws_region}#Instances:instanceId={head_instance_id}'
        )
        links['AWS Instance'] = instance_url

    elif provider_name == 'gcp':
        # GCP Compute Engine Console URL format:
        # https://console.cloud.google.com/compute/instancesDetail/zones/{zone}/instances/{instance_id}?project={project_id}
        project_id = provider_config.get('project_id')
        zone = provider_config.get('availability_zone')
        if project_id and zone:
            instance_url = (
                f'https://console.cloud.google.com/compute/instancesDetail'
                f'/zones/{zone}/instances/{head_instance_id}?project={project_id}'
            )
            links['GCP Instance'] = instance_url

    elif provider_name == 'azure':
        # Azure Portal URL format:
        # https://portal.azure.com/#@/resource/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.Compute/virtualMachines/{instance_id}
        subscription_id = provider_config.get('subscription_id')
        resource_group = provider_config.get('resource_group')
        if subscription_id and resource_group:
            instance_url = (
                f'https://portal.azure.com/#@/resource/subscriptions'
                f'/{subscription_id}/resourceGroups/{resource_group}'
                f'/providers/Microsoft.Compute/virtualMachines/{head_instance_id}'
            )
            links['Azure Instance'] = instance_url

    return links

