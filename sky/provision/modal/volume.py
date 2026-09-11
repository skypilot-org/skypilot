"""Modal Volume provisioning."""

from typing import Any, Dict, List, Set, Tuple

import yaml

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.adaptors import modal as modal_adaptor

logger = sky_logging.init_logger(__name__)


def _environment_name(config: models.VolumeConfig):
    return config.config.get('environment_name')


def apply_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Create or resolve a Modal Volume."""
    name_on_cloud = config.name_on_cloud
    assert name_on_cloud is not None
    environment_name = _environment_name(config)
    volume = modal_adaptor.modal.Volume.from_name(
        name_on_cloud,
        environment_name=environment_name,
        create_if_missing=not config.config.get('use_existing', False))
    volume.hydrate()
    config.id_on_cloud = getattr(volume, 'object_id', None)
    logger.info(f'Created or resolved Modal Volume {name_on_cloud}.')
    return config


def delete_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Delete a Modal Volume."""
    name_on_cloud = config.name_on_cloud
    assert name_on_cloud is not None
    modal_adaptor.modal.Volume.objects.delete(
        name_on_cloud,
        allow_missing=True,
        environment_name=_environment_name(config))
    logger.info(f'Deleted Modal Volume {name_on_cloud}.')
    return config


def _cluster_uses_volume(cluster_record: Dict[str, Any],
                         volume_name_on_cloud: str) -> bool:
    handle = cluster_record.get('handle')
    if handle is None:
        return False
    cluster_yaml = getattr(handle, 'cluster_yaml', None)
    if not cluster_yaml:
        return False
    try:
        cluster_yaml_obj = yaml.safe_load(cluster_yaml)
    except Exception:  # pylint: disable=broad-except
        logger.debug('Failed to parse cluster YAML for Modal volume used-by.',
                     exc_info=True)
        return False
    node_config = (cluster_yaml_obj.get('available_node_types',
                                        {}).get('ray_head_default',
                                                {}).get('node_config', {}))
    for volume_mount in node_config.get('ModalVolumes', []):
        if volume_mount.get('VolumeNameOnCloud') == volume_name_on_cloud:
            return True
    return False


def get_volume_usedby(
    config: models.VolumeConfig,) -> Tuple[List[str], List[str]]:
    """Return SkyPilot Modal clusters known to reference this volume."""
    volume_name_on_cloud = config.name_on_cloud
    assert volume_name_on_cloud is not None
    cluster_names = []
    for cluster_record in global_user_state.get_clusters():
        if _cluster_uses_volume(cluster_record, volume_name_on_cloud):
            cluster_name = cluster_record.get('name')
            if cluster_name:
                cluster_names.append(cluster_name)
    return [], cluster_names


def get_all_volumes_usedby(
    configs: List[models.VolumeConfig],
) -> Tuple[Dict[str, Any], Dict[str, Any], Set[str]]:
    used_by_pods: Dict[str, Any] = {}
    used_by_clusters: Dict[str, Any] = {}
    failed_volume_names: Set[str] = set()
    for config in configs:
        try:
            pods, clusters = get_volume_usedby(config)
            used_by_pods[config.name_on_cloud] = pods
            used_by_clusters[config.name_on_cloud] = clusters
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to get usedby info for Modal volume '
                         f'{config.name}: {e}')
            failed_volume_names.add(config.name)
    return used_by_pods, used_by_clusters, failed_volume_names


def map_all_volumes_usedby(
        used_by_pods: Dict[str, Any], used_by_clusters: Dict[str, Any],
        config: models.VolumeConfig) -> Tuple[List[str], List[str]]:
    return (used_by_pods.get(config.name_on_cloud,
                             []), used_by_clusters.get(config.name_on_cloud,
                                                       []))
