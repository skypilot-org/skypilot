"""RunPod network volume provisioning."""
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.adaptors import runpod
from sky.utils import common_utils
from sky.utils import volume as volume_lib

logger = sky_logging.init_logger(__name__)

# Lazy imports
requests = adaptors_common.LazyImport('requests')

_REST_BASE = 'https://rest.runpod.io/v1'
_MAX_RETRIES = 3
_TIMEOUT = 10


def _get_api_key() -> str:
    api_key = getattr(runpod.runpod, 'api_key', None)
    if not api_key:
        # Fallback to env if SDK global not set
        api_key = os.environ.get('RUNPOD_API_KEY')
    if not api_key:
        raise RuntimeError(
            'RunPod API key is not set. Please set runpod.api_key '
            'or RUNPOD_API_KEY.')
    return str(api_key)


def _rest_request(method: str,
                  path: str,
                  json: Optional[Dict[str, Any]] = None) -> Any:
    url = f'{_REST_BASE}{path}'
    headers = {
        'Authorization': f'Bearer {_get_api_key()}',
        'Content-Type': 'application/json',
    }
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = requests.request(method,
                                    url,
                                    headers=headers,
                                    json=json,
                                    timeout=_TIMEOUT)
        except Exception as e:  # pylint: disable=broad-except
            # Retry on transient network errors
            if attempt >= _MAX_RETRIES:
                raise RuntimeError(f'RunPod REST network error: {e}') from e
            time.sleep(1)
            continue

        # Retry on 5xx and 429
        if resp.status_code >= 500 or resp.status_code == 429:
            if attempt >= _MAX_RETRIES:
                raise RuntimeError(
                    f'RunPod REST error {resp.status_code}: {resp.text}')
            time.sleep(1)
            continue

        if resp.status_code >= 400:
            # Non-retryable client error
            raise RuntimeError(
                f'RunPod REST error {resp.status_code}: {resp.text}')

        if resp.text:
            try:
                return resp.json()
            except Exception:  # pylint: disable=broad-except
                return resp.text
        return None


def _list_volumes() -> List[Dict[str, Any]]:
    # GET /v1/networkvolumes returns a list
    result = _rest_request('GET', '/networkvolumes')
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

    vol_id = _try_resolve_volume_id(name_on_cloud)
    if vol_id is None:
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
        data_center_id = config.zone
        if not data_center_id:
            raise RuntimeError(
                'RunPod DataCenterId is required to create a network '
                'volume. Set the zone in the infra field.')
        payload = {
            'dataCenterId': data_center_id,
            'name': name_on_cloud,
            'size': size_int,
        }
        resp = _rest_request('POST', '/networkvolumes', json=payload)
        if isinstance(resp, dict):
            config.id_on_cloud = resp.get('id')
        else:
            raise RuntimeError(
                f'Failed to create RunPod network volume: {resp}')
        logger.info(f'Created RunPod network volume {name_on_cloud} '
                    f'(id={config.id_on_cloud})')
        return config

    # Use existing matched volume
    config.id_on_cloud = vol_id
    logger.debug(f'Using existing RunPod network volume {name_on_cloud} '
                 f'(id={config.id_on_cloud})')
    return config


def delete_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Deletes a RunPod network volume via REST API if id is known or
       resolvable. If the volume id is not known, try to resolve it by name.
    """
    name_on_cloud = config.name_on_cloud
    vol_id = config.id_on_cloud
    if not vol_id:
        vol_id = _try_resolve_volume_id(name_on_cloud)
    if not vol_id:
        logger.warning(
            f'RunPod network volume id not found for {name_on_cloud}; '
            f'skip delete')
        return config
    _rest_request('DELETE', f'/networkvolumes/{vol_id}')
    logger.info(f'Deleted RunPod network volume {name_on_cloud} '
                f'(id={vol_id})')
    return config


def _try_resolve_volume_id(name_on_cloud: str) -> Optional[str]:
    vols = _list_volumes()
    matched = next((v for v in vols if v.get('name') == name_on_cloud), None)
    if matched is not None:
        return matched.get('id')
    return None


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
    if vol_id is None:
        vol_id = _try_resolve_volume_id(name_on_cloud)
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
