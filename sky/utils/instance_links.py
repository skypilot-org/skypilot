"""Utility functions for generating instance links for cloud providers."""
from typing import Dict, Optional

from sky.provision import common
from sky.provision import constants as provision_constants


def generate_instance_links(
    cluster_info: common.ClusterInfo,
    region: Optional[str] = None,
    cluster_name: Optional[str] = None,
) -> Dict[str, str]:
    """Generate instance links for a cluster based on the cloud provider.

    Creates links to filtered views in cloud consoles that show all instances
    belonging to the cluster (useful for multi-node jobs).

    Args:
        cluster_info: ClusterInfo object containing instance information.
        region: Optional region string (used for some cloud providers).
        cluster_name: Optional cluster name for tag-based filtering.

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

    # We need a cluster name to generate filtered links
    if not cluster_name:
        return links

    # Tag used by SkyPilot to identify cluster instances
    tag_key = provision_constants.TAG_RAY_CLUSTER_NAME

    if provider_name == 'aws':
        # AWS EC2 Console URL with tag filter:
        # Shows all instances with the matching cluster tag
        aws_region = region or provider_config.get('region', 'us-east-1')
        instance_url = (
            f'https://{aws_region}.console.aws.amazon.com/ec2/v2/home'
            f'?region={aws_region}#Instances:tag:{tag_key}={cluster_name}')
        links['AWS Instances'] = instance_url

    elif provider_name == 'gcp':
        # GCP Compute Engine Console URL with label filter via pageState:
        # Shows all instances with the matching cluster label
        # URL format discovered from GCP console:
        # pageState=("instances":("p":0,"f":"<encoded_filter>"))
        # where the filter is: [{"k":"","t":10,"v":"\"label:value\"","s":true}]
        # with t=10 indicating a label filter
        project_id = provider_config.get('project_id')
        if project_id:
            # Build the filter JSON with GCP's specific encoding:
            # - Square/curly brackets are double URL-encoded (%255B = %5B = [)
            # - Other special chars use underscore notation (_22 = %22 = ", _3A = :)
            # The colon between tag key and value must also be encoded as _3A
            filter_encoded = (
                f'%255B%257B_22k_22_3A_22_22_2C_22t_22_3A10_2C_22v_22_3A_22'
                f'_5C_22{tag_key}_3A{cluster_name}_5C_22_22_2C_22s_22_3Atrue'
                f'%257D%255D')
            page_state = (
                f'(%22instances%22:(%22p%22:0,%22f%22:%22{filter_encoded}%22))')
            instance_url = (
                f'https://console.cloud.google.com/compute/instances'
                f'?project={project_id}&pageState={page_state}')
            links['GCP Instances'] = instance_url

    elif provider_name == 'azure':
        # Azure Portal URL to resource group filtered by tag:
        # Uses Azure Resource Graph query to find VMs with matching tag
        subscription_id = provider_config.get('subscription_id')
        resource_group = provider_config.get('resource_group')
        if subscription_id and resource_group:
            # Link to resource group's VM list - Azure doesn't have direct
            # tag filter URLs, but the resource group view shows all cluster VMs
            instance_url = (
                f'https://portal.azure.com/#@/resource/subscriptions'
                f'/{subscription_id}/resourceGroups/{resource_group}'
                '/overview')
            links['Azure Resource Group'] = instance_url

    return links
