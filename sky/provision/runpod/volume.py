"""RunPod network volume provisioning."""
from typing import List, Tuple

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.adaptors import runpod

logger = sky_logging.init_logger(__name__)


def apply_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Creates a RunPod network volume and stores its id in config.config.

    Expects:
      - config.name_on_cloud: used as the RunPod network volume name
      - config.size: GiB size (string or int convertible)
    """
    name_on_cloud = config.name_on_cloud
    size = config.size
    assert name_on_cloud is not None
    assert size is not None

    # Build mutation
    from sky.provision.runpod.api.pods import (
        generate_net_volume_deployment_mutation,
    )
    mutation = generate_net_volume_deployment_mutation(
        name=name_on_cloud,
        volume_in_gb=int(size),
        volume_mount_path='/workspace',
        volume_key=name_on_cloud,
    )
    resp = runpod.runpod.api.graphql.run_graphql_query(mutation)
    data = resp.get('data', {})
    vol_info = data.get('networkVolumeCreate')
    if not vol_info:
        raise RuntimeError(f'Failed to create RunPod network volume: {resp}')

    # Persist returned identifiers for later delete/used-by queries.
    cfg = dict(config.config or {})
    cfg['network_volume_id'] = vol_info.get('id')
    cfg['data_center_id'] = vol_info.get('dataCenterId')
    config.config = cfg
    logger.info(
        f'Created RunPod network volume {name_on_cloud} '
        f"(id={cfg.get('network_volume_id')}, size={size}GiB)"
    )
    return config


def delete_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Deletes a RunPod network volume by id (or resolves id by name)."""
    name_on_cloud = config.name_on_cloud
    vol_id = (config.config or {}).get('network_volume_id')

    if vol_id is None:
        # Resolve volume id by name from user info as a fallback.
        user_info = runpod.runpod.get_user()
        for v in user_info.get('networkVolumes', []):
            if v.get('name') == name_on_cloud:
                vol_id = v.get('id')
                break
    if vol_id is None:
        logger.warning(
            f'RunPod network volume id not found for {name_on_cloud}; skip delete.'
        )
        return config

    mutation = f"""
    mutation {{
      networkVolumeDelete(input: {{ id: "{vol_id}" }})
    }}
    """
    resp = runpod.runpod.api.graphql.run_graphql_query(mutation)
    # API may return boolean or a simple status; we do best-effort and log.
    logger.info(
        f'Delete RunPod network volume {name_on_cloud} (id={vol_id}) response: {resp}'
    )
    return config


def get_volume_usedby(
    config: models.VolumeConfig,
) -> Tuple[List[str], List[str]]:
    """Gets the clusters currently using this RunPod network volume.

    Returns:
      (usedby_pods, usedby_clusters)
    usedby_clusters contains SkyPilot cluster display names inferred from pod names.
    """
    vol_id = (config.config or {}).get('network_volume_id')
    name_on_cloud = config.name_on_cloud
    if vol_id is None:
        # Best-effort resolve
        user_info = runpod.runpod.get_user()
        for v in user_info.get('networkVolumes', []):
            if v.get('name') == name_on_cloud:
                vol_id = v.get('id')
                break
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
    for pod_name in usedby_pod_names:
        matched = None
        for c in clusters:
            display = c.get('name')
            if not display:
                continue
            # Heuristic: RunPod pod name is often f"{cluster}-{role}"
            if pod_name.startswith(display + '-') or pod_name == display:
                matched = display
                break
        if matched and matched not in cluster_names:
            cluster_names.append(matched)

    return usedby_pod_names, cluster_names
