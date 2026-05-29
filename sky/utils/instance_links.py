"""Utility functions for generating instance links for cloud providers."""
from typing import Dict

from sky import sky_logging
from sky.provision import common
from sky.provision import constants as provision_constants

logger = sky_logging.init_logger(__name__)

# URL templates for each cloud provider
# Placeholders:
#   {region} - Cloud region
#   {project_id} - GCP project ID
#   {subscription_id} - Azure subscription ID
#   {resource_group} - Azure resource group
#   {tag_key} - Tag key used to identify cluster instances
#   {cluster_name} - Name of the cluster

AWS_INSTANCES_URL = ('https://{region}.console.aws.amazon.com/ec2/v2/home'
                     '?region={region}#Instances:tag:{tag_key}={cluster_name}')

# Azure doesn't support direct tag filter URLs, so we link to the resource group
AZURE_RESOURCE_GROUP_URL = (
    'https://portal.azure.com/#@/resource/subscriptions'
    '/{subscription_id}/resourceGroups/{resource_group}/overview')

# GCP Console base URL
GCP_INSTANCES_BASE_URL = 'https://console.cloud.google.com/compute/instances'


def _build_gcp_instances_url(project_id: str, tag_key: str,
                             cluster_name: str) -> str:
    """Build GCP instances URL with label filter.

    GCP Console uses a pageState parameter with a specially encoded filter.
    The filter JSON structure is:
        [{"k":"","t":10,"v":"\"label_key:label_value\"","s":true}]

    Where:
        - k: filter key (empty for label filters)
        - t: filter type (10 = label filter)
        - v: filter value with escaped quotes around "label_key:label_value"
        - s: unknown, always true

    GCP uses a mix of:
        - Standard URL encoding for outer structure (%22 for ")
        - Underscore notation inside the filter (_22 for ", _3A for :, etc.)
        - Double URL-encoding for brackets (%255B = %5B = [)
    """
    # Build the filter value: \"tag_key:cluster_name\"
    # Using underscore notation: _5C_22 = \", _3A = :
    filter_value = f'_5C_22{tag_key}_3A{cluster_name}_5C_22'

    # Build the filter object using underscore notation for internal quotes and
    # colons.
    # {"k":"","t":10,"v":"<filter_value>","s":true}
    # _22 = ", _3A = :, _2C = ,
    filter_obj = (
        f'_22k_22_3A_22_22_2C'  # "k":"",
        f'_22t_22_3A10_2C'  # "t":10,
        f'_22v_22_3A_22{filter_value}_22_2C'  # "v":"<value>",
        f'_22s_22_3Atrue')  # "s":true

    # Wrap in array brackets (double URL-encoded: %255B = %5B = [, %257D = %7D)
    filter_array = f'%255B%257B{filter_obj}%257D%255D'

    # Build pageState: ("instances":("p":0,"f":"<filter>"))
    # %22 = " (standard URL encoding)
    page_state = f'(%22instances%22:(%22p%22:0,%22f%22:%22{filter_array}%22))'

    return (
        f'{GCP_INSTANCES_BASE_URL}?project={project_id}&pageState={page_state}')


def generate_instance_links(
    cluster_info: common.ClusterInfo,
    cluster_name: str,
) -> Dict[str, str]:
    """Generate instance links for a cluster based on the cloud provider.

    Creates links to filtered views in cloud consoles that show all instances
    belonging to the cluster (useful for multi-node jobs).

    Args:
        cluster_info: ClusterInfo object containing instance information.
        cluster_name: Cluster name for tag-based filtering.

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

    # Tag used by SkyPilot to identify cluster instances
    tag_key = provision_constants.TAG_RAY_CLUSTER_NAME

    if provider_name == 'aws':
        region = provider_config.get('region')
        if not region:
            logger.debug('AWS region not found in provider config, '
                         'skipping instance links')
            return links
        links['AWS Instances'] = AWS_INSTANCES_URL.format(
            region=region,
            tag_key=tag_key,
            cluster_name=cluster_name,
        )

    elif provider_name == 'gcp':
        project_id = provider_config.get('project_id')
        if not project_id:
            logger.debug('GCP project_id not found in provider config, '
                         'skipping instance links')
            return links
        links['GCP Instances'] = _build_gcp_instances_url(
            project_id=project_id,
            tag_key=tag_key,
            cluster_name=cluster_name,
        )

    elif provider_name == 'azure':
        subscription_id = provider_config.get('subscription_id')
        resource_group = provider_config.get('resource_group')
        if not subscription_id or not resource_group:
            logger.debug('Azure subscription_id or resource_group not found '
                         'in provider config, skipping instance links')
            return links
        links['Azure Resource Group'] = AZURE_RESOURCE_GROUP_URL.format(
            subscription_id=subscription_id,
            resource_group=resource_group,
        )

    return links
