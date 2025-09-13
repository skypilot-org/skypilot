"""OCI configuration bootstrapping.

Creates the resource group and deploys the configuration template to OCI for
a cluster to be launched.

History:
 - Hysun He (hysun.he@oracle.com) @ Oct.16, 2024: Initial implementation
"""

from sky import exceptions
from sky import sky_logging
from sky.adaptors import oci as oci_adaptor
from sky.clouds.utils import oci_utils
from sky.provision import common
from sky.provision.oci.query_utils import query_helper

logger = sky_logging.init_logger(__name__)


@common.log_function_start_end
def bootstrap_instances(
        region: str, cluster_name_on_cloud: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """See sky/provision/__init__.py"""
    # OCI module import and oci client
    oci_adaptor.get_core_client(region, oci_utils.oci_config.get_profile())

    # Find / create a compartment for creating instances.
    compartment = query_helper.find_compartment(region)

    # Find the configured VCN, or create a new one.
    vcn = query_helper.find_create_vcn_subnet(region)
    if vcn is None:
        # pylint: disable=line-too-long
        raise exceptions.ResourcesUnavailableError(
            'Failed to create a new VCN, possibly you hit the resource limitation.'
        )

    node_config = config.node_config

    # Subscribe the image if it is from Marketplace listing.
    query_helper.subscribe_image(
        compartment_id=compartment,
        listing_id=node_config['AppCatalogListingId'],
        resource_version=node_config['ResourceVersion'],
        region=region,
    )

    logger.info(f'Using cluster name: {cluster_name_on_cloud}')

    return config
