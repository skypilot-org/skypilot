"""Paperspace configuration bootstrapping."""

from sky import sky_logging
from sky.provision import common
import sky.provision.paperspace.constants as constants
from sky.provision.paperspace.utils import PaperspaceCloudClient

logger = sky_logging.init_logger(__name__)


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Bootstraps instances for the given cluster."""
    if not config.node_config['DiskSize'] in constants.DISK_SIZES:
        if config.node_config['DiskSize'] > constants.DISK_SIZES[-1]:
            raise ValueError(
                f'Paperspace largest disk size is {constants.DISK_SIZES[-1]},'
                f'requested {config.node_config["DiskSize"]}')

        size = 0
        for possible_size in constants.DISK_SIZES:
            if size < config.node_config['DiskSize'] < possible_size:
                logger.warning(
                    'Paperspace only supports disk sizes'
                    f'{constants.DISK_SIZES}, '
                    f'upsizing from {config.node_config["DiskSize"]} '
                    f'to {possible_size}')
                config.node_config['DiskSize'] = possible_size
                break

    client = PaperspaceCloudClient()
    network_id = client.setup_network(cluster_name, region)['id']
    config.node_config['NetworkId'] = network_id
    return config
