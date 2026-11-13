"""UpCloud Cloud configuration bootstrapping."""

from sky import exceptions
from sky import sky_logging
from sky.provision import common
from sky.provision.upcloud import utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


def _ensure_cluster_network(client: utils.UpCloudClient, region: str,
                            cluster_name_on_cloud: str) -> str:
    for network in client.list_networks(cluster_name=cluster_name_on_cloud,
                                        zone=region):
        uuid = network.get('uuid')
        if uuid:
            return str(uuid)
    labels = {utils.LABEL_CLUSTER: cluster_name_on_cloud}
    network, cidr = utils.create_network_in_free_range(
        client, f'skypilot-{cluster_name_on_cloud}', region,
        cluster_name_on_cloud, labels)
    uuid = network.get('uuid')
    if not uuid:
        raise utils.UpCloudError(
            f'No UUID in create_network response: {network}')
    logger.info(f'Created UpCloud private network {uuid} ({cidr}) for '
                f'{cluster_name_on_cloud}.')
    return str(uuid)


def _find_network_by_name(client: utils.UpCloudClient, region: str,
                          name: str) -> str:
    """Returns the UUID of the private network called `name` in `region`."""
    matches = [
        n for n in client.list_networks(zone=region)
        if n.get('name') == name and
        str(n.get('type', '')).lower() == 'private' and n.get('uuid')
    ]
    if not matches:
        raise exceptions.ResourcesUnavailableError(
            f'No UpCloud private network named {name!r} in zone {region}.')
    if len(matches) > 1:
        uuids = ', '.join(str(n['uuid']) for n in matches)
        raise exceptions.ResourcesUnavailableError(
            f'Multiple UpCloud private networks named {name!r} in zone '
            f'{region}: {uuids}. Rename them so the name is unique.')
    return str(matches[0]['uuid'])


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Resolves the private network the cluster nodes are attached to.

    Uses the network named by `upcloud.network_name` or the network of
    mounted file storage volumes when given; otherwise a per-cluster SDN
    network is created and deleted together with the cluster.
    """
    client = utils.UpCloudClient()
    volume_network = config.provider_config.get('network')
    network_name = config.provider_config.get('network_name')
    if network_name:
        network_uuid = _find_network_by_name(client, region, str(network_name))
        if volume_network and volume_network != network_uuid:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'upcloud.network_name is {network_name!r} '
                    f'({network_uuid}), but the mounted file storage volumes '
                    f'are on network {volume_network}.')
    elif volume_network:
        network_uuid = str(volume_network)
    else:
        network_uuid = _ensure_cluster_network(client, region, cluster_name)
    config.provider_config['network_uuid'] = network_uuid
    return config
