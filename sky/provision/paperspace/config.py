"""Paperspace configuration bootstrapping."""

import sky.provision.paperspace.constants as constants
from sky.provision import common
from sky.provision.paperspace.utils import PaperspaceCloudClient


def bootstrap_instances(
    region: str, cluster_name: str, config: common.ProvisionConfig
) -> common.ProvisionConfig:
    """Bootstraps instances for the given cluster."""
    if not config.node_config["DiskSize"] in constants.DISK_SIZES:
        raise ValueError(f"""Paperspace only supports disk sizes '{constants.DISK_SIZES}', requested '{config.node_config["DiskSize"]}'""")

    client = PaperspaceCloudClient()
    network_id = client.setup_network(cluster_name, region)["id"]
    config.node_config["NetworkId"] = network_id
    return config