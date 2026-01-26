"""Mithril volume provisioning."""
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.provision.mithril import utils
from sky.provision.mithril.utils import MithrilError

logger = sky_logging.init_logger(__name__)

_TYPE_TO_DISK_INTERFACE = {
    'mithril-file-share': 'File',
    'mithril-block': 'Block',
}


def _list_volumes(region: Optional[str] = None) -> List[Dict[str, Any]]:
    config = utils.get_config()
    params: Dict[str, Any] = {
        'project': config['project_id'],
    }
    if region is not None:
        params['region'] = region
    return utils.make_request('GET', '/v2/volumes', params=params)


def _populate_config_from_volume(config: models.VolumeConfig,
                                 vol: Dict[str, Any]) -> None:
    name = vol['name']
    config.name_on_cloud = name
    config.id_on_cloud = vol['fid']
    size = vol['capacity_gb']
    if config.size is not None and config.size != str(size):
        logger.warning(
            f'Mithril volume {config.name_on_cloud} has size {size} but '
            f'config size is {config.size}, overriding the config size '
            f'with the volume size.')
    config.size = str(size)


def _list_bids() -> List[Dict[str, Any]]:
    config = utils.get_config()
    base_params: Dict[str, Any] = ({
        'project': quote(config['project_id'])
    } if config['project_id'] else {})
    bids: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        params = base_params.copy()
        if cursor:
            params['next_cursor'] = cursor
        response = utils.make_request('GET', '/v2/spot/bids', params=params)
        bids.extend(response.get('data', []))
        cursor = response.get('next_cursor')
        if not cursor:
            break
    return bids


def _list_reservations() -> List[Dict[str, Any]]:
    config = utils.get_config()
    base_params: Dict[str, Any] = ({
        'project': quote(config['project_id'])
    } if config['project_id'] else {})
    reservations: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        params = base_params.copy()
        if cursor:
            params['next_cursor'] = cursor
        response = utils.make_request('GET', '/v2/reservation', params=params)
        reservations.extend(response.get('data', []))
        cursor = response.get('next_cursor')
        if not cursor:
            break
    return reservations


def _try_resolve_volume_by_name(
        name_on_cloud: str, region: Optional[str]) -> Optional[Dict[str, Any]]:
    vols = _list_volumes(region=region)
    return next((v for v in vols if v['name'] == name_on_cloud), None)


def apply_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Create or resolve a Mithril volume via REST API.

    If a volume with the same `name_on_cloud` exists, reuse it. Otherwise,
    create a new one using POST /v2/volumes.
    """
    name_on_cloud = config.name
    assert name_on_cloud is not None
    config.name_on_cloud = name_on_cloud
    region = config.region
    if region is None:
        raise MithrilError('Mithril region is required for volumes. '
                           'Set the region in the infra field.')

    vol = _try_resolve_volume_by_name(name_on_cloud, region)
    if vol is not None:
        existing_size = vol['capacity_gb']
        if config.size is not None:
            requested_size = int(config.size)
            if requested_size != existing_size:
                raise MithrilError(
                    f'Mithril volume {name_on_cloud} already exists with '
                    f'size {existing_size}GB, but config size is '
                    f'{config.size}.')
        _populate_config_from_volume(config, vol)
        logger.info(f'Using existing Mithril volume {name_on_cloud} '
                    f'(fid={config.id_on_cloud})')
        return config

    size = config.size
    if size is None:
        raise MithrilError(
            'Mithril volume size must be specified to create a volume.')
    size_int = int(size)
    if size_int <= 0:
        raise ValueError('Volume size must be positive.')

    disk_interface = _TYPE_TO_DISK_INTERFACE.get(config.type)
    if disk_interface is None:
        raise MithrilError('Unknown Mithril volume type.')

    api_config = utils.get_config()
    payload = {
        'name': name_on_cloud,
        'project': api_config['project_id'],
        'disk_interface': disk_interface,
        'region': region,
        'size_gb': size_int,
    }
    resp = utils.make_request('POST', '/v2/volumes', payload=payload)
    _populate_config_from_volume(config, resp)
    logger.info(f'Created Mithril volume {name_on_cloud} '
                f'(fid={config.id_on_cloud})')
    return config


def delete_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Deletes a Mithril volume via REST API if fid is known or resolvable."""
    name_on_cloud = config.name_on_cloud or config.name
    assert name_on_cloud is not None
    vol_id = config.id_on_cloud
    if not vol_id:
        raise MithrilError(f'Mithril volume fid not found for {name_on_cloud}.')
    utils.make_request('DELETE', f'/v2/volumes/{vol_id}')
    logger.info(f'Deleted Mithril volume {name_on_cloud} (fid={vol_id})')
    return config


def get_volume_usedby(
        config: models.VolumeConfig) -> Tuple[List[str], List[str]]:
    """Gets the clusters currently using this Mithril volume."""
    name_on_cloud = config.name_on_cloud or config.name
    assert name_on_cloud is not None
    region = config.region
    vol = _try_resolve_volume_by_name(name_on_cloud, region)
    if vol is None:
        return [], []

    bid_ids = vol['bids']
    reservation_ids = vol['reservations']
    if not bid_ids and not reservation_ids:
        return [], []

    usedby_names: List[str] = []
    if bid_ids:
        bids = _list_bids()
        bid_name_by_id = {bid['fid']: bid['name'] for bid in bids}
        for bid_id in bid_ids:
            name = bid_name_by_id.get(bid_id, bid_id)
            if name not in usedby_names:
                usedby_names.append(name)
    if reservation_ids:
        reservations = _list_reservations()
        reservation_name_by_id = {
            reservation['fid']: reservation['name']
            for reservation in reservations
        }
        for reservation_id in reservation_ids:
            name = reservation_name_by_id.get(reservation_id, reservation_id)
            if name not in usedby_names:
                usedby_names.append(name)

    clusters = global_user_state.get_clusters()
    cluster_names: List[str] = []
    for usedby_name in usedby_names:
        matched = None
        for c in clusters:
            handle = c.get('handle')
            if handle is None:
                continue
            cluster_name_on_cloud = getattr(handle, 'cluster_name_on_cloud',
                                            None)
            display = c.get('name')
            if cluster_name_on_cloud and (
                    usedby_name == cluster_name_on_cloud or
                    usedby_name.startswith(cluster_name_on_cloud)):
                matched = display
                break
        if matched and matched not in cluster_names:
            cluster_names.append(matched)

    return usedby_names, cluster_names


def get_all_volumes_usedby(
    configs: List[models.VolumeConfig],
) -> Tuple[Dict[str, Any], Dict[str, Any], Set[str]]:
    """Gets the usedby resources of all volumes."""
    used_by_instances, used_by_clusters = {}, {}
    failed_volume_names = set()
    for config in configs:
        try:
            usedby_instances, usedby_clusters = get_volume_usedby(config)
            used_by_instances[config.name_on_cloud] = usedby_instances
            used_by_clusters[config.name_on_cloud] = usedby_clusters
        except (MithrilError, KeyError, ValueError) as e:
            logger.debug(f'Failed to get usedby info for Mithril volume '
                         f'{config.name}: {e}')
            failed_volume_names.add(config.name)
            continue
    return used_by_instances, used_by_clusters, failed_volume_names


def map_all_volumes_usedby(
        used_by_instances: Dict[str, Any], used_by_clusters: Dict[str, Any],
        config: models.VolumeConfig) -> Tuple[List[str], List[str]]:
    """Maps the usedby resources of a volume."""
    return (used_by_instances.get(config.name_on_cloud, []),
            used_by_clusters.get(config.name_on_cloud, []))
