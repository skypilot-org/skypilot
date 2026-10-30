"""UpCloud File Storage volume provisioning."""
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.provision.upcloud import utils

logger = sky_logging.init_logger(__name__)

# Private network of file storage volumes; clusters mounting the volume join
# this network instead of creating their own. Its subnet is picked per volume
# so it does not overlap other private networks in the zone.
FILE_STORAGE_SHARE_PATH = '/skypilot'
_FILE_STORAGE_NETWORK_NAME = 'skypilot'
_FILE_STORAGE_TIMEOUT = 1800


def _find_file_storage(client: utils.UpCloudClient, name_on_cloud: str,
                       zone: str) -> Optional[Dict[str, Any]]:
    services = [s for s in client.list_file_storages() if s.get('zone') == zone]
    for service in services:
        if utils.has_label(service, utils.LABEL_VOLUME, name_on_cloud):
            return service
    for service in services:
        if service.get('name') == name_on_cloud:
            return service
    return None


def _file_storage_address(service: Dict[str, Any]) -> Tuple[str, str]:
    for network in service.get('networks') or []:
        if network.get('ip_address') and network.get('uuid'):
            return str(network['ip_address']), str(network['uuid'])
    raise utils.UpCloudError(
        f'UpCloud file storage {service.get("uuid")} has no network address.')


def _wait_file_storage_running(client: utils.UpCloudClient,
                               uuid: str) -> Dict[str, Any]:
    deadline = time.time() + _FILE_STORAGE_TIMEOUT
    while True:
        service = client.get_file_storage(uuid)
        state = str(service.get('operational_state', '')).lower()
        if state == 'running':
            return service
        if time.time() > deadline:
            raise TimeoutError(f'Timed out waiting for UpCloud file storage '
                               f'{uuid} to be running (state={state}).')
        time.sleep(utils.POLL_INTERVAL)


def _resolve_size(config: models.VolumeConfig, actual: Any) -> None:
    if actual is None:
        return
    if config.size is not None and config.size != str(actual):
        logger.warning(f'UpCloud volume {config.name_on_cloud} has size '
                       f'{actual} but config size is {config.size}, '
                       'overriding the config size with the volume size.')
    config.size = str(actual)


def apply_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Creates or resolves an UpCloud file storage by its name_on_cloud."""
    client = utils.UpCloudClient()
    name_on_cloud = config.name_on_cloud
    zone = config.region
    assert zone is not None
    service = _find_file_storage(client, name_on_cloud, zone)
    if service is None:
        if config.config.get('use_existing'):
            raise ValueError(f'UpCloud file storage {name_on_cloud} does not '
                             f'exist in {zone} while use_existing is True.')
        if config.size is None:
            raise RuntimeError('UpCloud file storage size must be specified '
                               'to create a volume.')
        network, cidr = utils.create_network_in_free_range(
            client, f'skypilot-volume-{name_on_cloud}', zone, name_on_cloud,
            {utils.LABEL_VOLUME: name_on_cloud})
        try:
            service = client.create_file_storage({
                'name': name_on_cloud,
                'zone': zone,
                'configured_status': 'started',
                'size_gib': int(config.size),
                'networks': [{
                    'uuid': network['uuid'],
                    'name': _FILE_STORAGE_NETWORK_NAME,
                    'family': 'IPv4',
                }],
                'shares': [{
                    'name': _FILE_STORAGE_NETWORK_NAME,
                    'path': FILE_STORAGE_SHARE_PATH,
                    'acl': [{
                        'name': _FILE_STORAGE_NETWORK_NAME,
                        'target': cidr,
                        'permission': 'rw',
                    }],
                }],
                'labels': [{
                    'key': utils.LABEL_VOLUME,
                    'value': name_on_cloud
                }],
            })
        except utils.UpCloudError:
            # Do not leave the new network behind when the storage fails.
            client.delete_network(network['uuid'])
            raise
        service = _wait_file_storage_running(client, str(service['uuid']))
        logger.info(f'Created UpCloud file storage {name_on_cloud} '
                    f'(id={service["uuid"]}).')
    address, network_uuid = _file_storage_address(service)
    config.id_on_cloud = str(service['uuid'])
    config.config['nfs_address'] = address
    config.config['nfs_path'] = FILE_STORAGE_SHARE_PATH
    config.config['network'] = network_uuid
    _resolve_size(config, service.get('size_gib'))
    return config


def delete_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Deletes an UpCloud file storage and the network SkyPilot created."""
    client = utils.UpCloudClient()
    zone = config.region
    assert zone is not None
    service = _find_file_storage(client, config.name_on_cloud, zone)
    if service is None:
        logger.warning(f'UpCloud file storage {config.name_on_cloud} not '
                       'found; skip delete.')
        return config
    _, network_uuid = _file_storage_address(service)
    client.delete_file_storage(str(service['uuid']))
    deadline = time.time() + _FILE_STORAGE_TIMEOUT
    while _find_file_storage(client, config.name_on_cloud, zone):
        if time.time() > deadline:
            raise TimeoutError('Timed out waiting for UpCloud file storage '
                               f'{config.name_on_cloud} deletion.')
        time.sleep(utils.POLL_INTERVAL)
    network = client.get_network(network_uuid)
    if utils.has_label(network, utils.LABEL_VOLUME, config.name_on_cloud):
        client.delete_network(network_uuid)
    logger.info(f'Deleted UpCloud file storage {config.name_on_cloud}.')
    return config


def _cluster_display_names() -> Dict[str, str]:
    names: Dict[str, str] = {}
    for record in global_user_state.get_clusters():
        handle = record.get('handle')
        name_on_cloud = getattr(handle, 'cluster_name_on_cloud', None)
        if name_on_cloud and record.get('name'):
            names[name_on_cloud] = record['name']
    return names


def _attached_server_uuids(client: utils.UpCloudClient,
                           config: models.VolumeConfig) -> List[str]:
    zone = config.region
    assert zone is not None
    service = _find_file_storage(client, config.name_on_cloud, zone)
    if service is None:
        return []
    _, network_uuid = _file_storage_address(service)
    servers = (client.get_network(network_uuid).get('servers') or
               {}).get('server', [])
    if isinstance(servers, (str, dict)):
        servers = [servers]
    uuids = []
    for server in servers:
        uuid = server.get('uuid') if isinstance(server, dict) else server
        if uuid:
            uuids.append(str(uuid))
    return uuids


def get_volume_usedby(
        config: models.VolumeConfig) -> Tuple[List[str], List[str]]:
    """Gets the servers and SkyPilot clusters using this UpCloud volume.

    Returns:
      (usedby_servers, usedby_clusters)
    """
    client = utils.UpCloudClient()
    server_uuids = _attached_server_uuids(client, config)
    display_names = _cluster_display_names()
    usedby_servers: List[str] = []
    usedby_clusters: List[str] = []
    for uuid in server_uuids:
        server = client.get_server(uuid)
        usedby_servers.append(str(server.get('hostname', uuid)))
        cluster = utils.get_labels(server).get(utils.LABEL_CLUSTER)
        if cluster is None:
            continue
        display = display_names.get(cluster, cluster)
        if display not in usedby_clusters:
            usedby_clusters.append(display)
    return usedby_servers, usedby_clusters


def get_all_volumes_usedby(
    configs: List[models.VolumeConfig],
) -> Tuple[Dict[str, Any], Dict[str, Any], Set[str]]:
    """Gets the usedby resources of all volumes."""
    used_by_servers, used_by_clusters = {}, {}
    failed_volume_names = set()
    for config in configs:
        try:
            usedby_servers, usedby_clusters = get_volume_usedby(config)
            used_by_servers[config.name_on_cloud] = usedby_servers
            used_by_clusters[config.name_on_cloud] = usedby_clusters
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to get usedby info for UpCloud volume '
                         f'{config.name}: {e}')
            failed_volume_names.add(config.name)
    return used_by_servers, used_by_clusters, failed_volume_names


def map_all_volumes_usedby(
        used_by_servers: Dict[str, Any], used_by_clusters: Dict[str, Any],
        config: models.VolumeConfig) -> Tuple[List[str], List[str]]:
    """Maps the usedby resources of a volume."""
    return (used_by_servers.get(config.name_on_cloud, []),
            used_by_clusters.get(config.name_on_cloud, []))
