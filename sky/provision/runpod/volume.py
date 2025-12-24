"""RunPod network volume provisioning."""
from typing import Any, Dict, List, Optional, Tuple

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.adaptors import runpod
from sky.utils import common_utils
from sky.utils import volume as volume_lib

logger = sky_logging.init_logger(__name__)


def _list_volumes() -> List[Dict[str, Any]]:
    # GET /v1/networkvolumes returns a list
    result = runpod.rest_request('GET', '/networkvolumes')
    if isinstance(result, list):
        return result
    # Some deployments may wrap the list.
    if isinstance(result, dict):
        for key in ('items', 'data', 'networkVolumes'):
            if key in result and isinstance(result[key], list):
                return result[key]
    return []


def apply_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Create or resolve a RunPod network volume via REST API.

    If a volume with the same `name_on_cloud` exists, reuse it. Otherwise,
    create a new one using POST /v1/networkvolumes.
    """
    name_on_cloud = config.name_on_cloud
    assert name_on_cloud is not None
    data_center_id = config.zone
    if not data_center_id:
        raise RuntimeError('RunPod DataCenterId is required for network '
                           'volumes. Set the zone in the infra field.')
    vol = _try_resolve_volume_by_name(name_on_cloud, data_center_id)
    if vol is None:
        use_existing = config.config.get('use_existing')
        if use_existing:
            raise ValueError(
                f'RunPod network volume {name_on_cloud} does not exist while '
                f'use_existing is True.')
        # Create new volume via REST
        size = config.size
        if size is None:
            raise RuntimeError(
                'RunPod network volume size must be specified to create '
                'a volume.')
        try:
            size_int = int(size)
            if size_int < volume_lib.MIN_RUNPOD_NETWORK_VOLUME_SIZE_GB:
                raise RuntimeError(
                    f'RunPod network volume size must be at least '
                    f'{volume_lib.MIN_RUNPOD_NETWORK_VOLUME_SIZE_GB}GB.')
        except Exception as e:  # pylint: disable=broad-except
            raise RuntimeError(f'Invalid volume size {size!r}: {e}') from e

        payload = {
            'dataCenterId': data_center_id,
            'name': name_on_cloud,
            'size': size_int,
        }
        resp = runpod.rest_request('POST', '/networkvolumes', json=payload)
        if isinstance(resp, dict):
            config.id_on_cloud = resp.get('id')
        else:
            raise RuntimeError(
                f'Failed to create RunPod network volume: {resp}')
        logger.info(f'Created RunPod network volume {name_on_cloud} '
                    f'(id={config.id_on_cloud})')
        return config

    # Use existing matched volume
    id_on_cloud = vol.get('id')
    if id_on_cloud is None:
        raise RuntimeError(
            f'RunPod network volume {name_on_cloud} has no id returned.')
    config.id_on_cloud = id_on_cloud
    size = vol.get('size')
    if size is not None:
        if config.size is not None and config.size != str(size):
            logger.warning(
                f'RunPod network volume {name_on_cloud} has size {size} but '
                f'config size is {config.size}, overriding the config size '
                f'with the volume size.')
        config.size = str(size)
    else:
        logger.warning(
            f'RunPod network volume {name_on_cloud} has no size returned.')
    logger.debug(f'Using existing RunPod network volume {name_on_cloud} '
                 f'(id={config.id_on_cloud})')
    return config


def delete_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Deletes a RunPod network volume via REST API if id is known or
       resolvable. If the volume id is not known, try to resolve it by name.
    """
    name_on_cloud = config.name_on_cloud
    assert name_on_cloud is not None
    data_center_id = config.zone
    assert data_center_id is not None
    vol_id = config.id_on_cloud
    if not vol_id:
        vol_id = _try_resolve_volume_id(name_on_cloud, data_center_id)
    if not vol_id:
        logger.warning(
            f'RunPod network volume id not found for {name_on_cloud}; '
            f'skip delete')
        return config
    runpod.rest_request('DELETE', f'/networkvolumes/{vol_id}')
    logger.info(f'Deleted RunPod network volume {name_on_cloud} '
                f'(id={vol_id})')
    return config


def _try_resolve_volume_id(name_on_cloud: str,
                           data_center_id: str) -> Optional[str]:
    vols = _list_volumes()
    matched = next((v for v in vols if v.get('name') == name_on_cloud and
                    v.get('dataCenterId') == data_center_id), None)
    if matched is not None:
        return matched.get('id')
    return None


def _try_resolve_volume_by_name(
        name_on_cloud: str, data_center_id: str) -> Optional[Dict[str, Any]]:
    vols = _list_volumes()
    return next((v for v in vols if v.get('name') == name_on_cloud and
                 v.get('dataCenterId') == data_center_id), None)


def get_volume_usedby(
    config: models.VolumeConfig,) -> Tuple[List[str], List[str]]:
    """Gets the clusters currently using this RunPod network volume.

    Returns:
      (usedby_pods, usedby_clusters)
    usedby_clusters contains SkyPilot cluster display names inferred from
      pod names, which may be wrong.
    """
    vol_id = config.id_on_cloud
    name_on_cloud = config.name_on_cloud
    assert name_on_cloud is not None
    data_center_id = config.zone
    assert data_center_id is not None
    if vol_id is None:
        vol_id = _try_resolve_volume_id(name_on_cloud, data_center_id)
    if vol_id is None:
        return [], []

    # Query all pods for current user and filter by networkVolumeId
    query = """
    query Pods {
      myself {
        pods {
          id
          name
          networkVolumeId
        }
      }
    }
    """
    resp = runpod.runpod.api.graphql.run_graphql_query(query)
    pods = resp.get('data', {}).get('myself', {}).get('pods', [])
    used_pods = [p for p in pods if p.get('networkVolumeId') == vol_id]
    usedby_pod_names = [p.get('name') for p in used_pods if p.get('name')]

    # Map pod names back to SkyPilot cluster names using heuristics.
    clusters = global_user_state.get_clusters()
    cluster_names: List[str] = []
    user_hash = common_utils.get_user_hash()
    for pod_name in usedby_pod_names:
        matched = None
        for c in clusters:
            display = c.get('name')
            if not display:
                continue
            # Heuristic: RunPod pod name is f"{cluster}-{user_hash}-{xxx}"
            # This can be wrong.
            cluster_prefix = display + '-' + user_hash + '-'
            if pod_name.startswith(cluster_prefix):
                matched = display
                break
        if matched and matched not in cluster_names:
            cluster_names.append(matched)

    return usedby_pod_names, cluster_names


def get_all_volumes_usedby(
    configs: List[models.VolumeConfig],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Gets the usedby resources of all volumes."""
    used_by_results = [get_volume_usedby(config) for config in configs]
    used_by_pods, used_by_clusters = {}, {}
    for i in range(len(configs)):
        config = configs[i]
        used_by_pods[config.name_on_cloud] = used_by_results[i][0]
        used_by_clusters[config.name_on_cloud] = used_by_results[i][1]
    return used_by_pods, used_by_clusters


def map_all_volumes_usedby(
        used_by_pods: Dict[str, Any], used_by_clusters: Dict[str, Any],
        config: models.VolumeConfig) -> Tuple[List[str], List[str]]:
    """Maps the usedby resources of a volume."""
    return (used_by_pods.get(config.name_on_cloud,
                             []), used_by_clusters.get(config.name_on_cloud,
                                                       []))
