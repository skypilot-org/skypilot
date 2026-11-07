"""Lambda Cloud persistent filesystem provisioning."""
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.provision.lambda_cloud import lambda_utils

logger = sky_logging.init_logger(__name__)

# https://docs.lambda.ai/api/cloud#createFilesystem
_FILESYSTEM_NAME_PATTERN = re.compile(r'^[a-zA-Z]+[0-9a-zA-Z-]*$')
_MAX_FILESYSTEM_NAME_LENGTH = 60


def _region_name(filesystem: Dict[str, Any]) -> Optional[str]:
    region = filesystem.get('region')
    if isinstance(region, dict):
        return region.get('name')
    return region


def _find_filesystem(client: lambda_utils.LambdaCloudClient, name: str,
                     region: str) -> Optional[Dict[str, Any]]:
    return next((fs for fs in client.list_filesystems()
                 if fs.get('name') == name and _region_name(fs) == region),
                None)


def _validate_name_on_cloud(name: str) -> None:
    if (len(name) > _MAX_FILESYSTEM_NAME_LENGTH or
            not _FILESYSTEM_NAME_PATTERN.match(name)):
        raise ValueError(
            f'Invalid Lambda filesystem name {name!r}: it must start with a '
            'letter, contain only letters, digits and hyphens, and be at most '
            f'{_MAX_FILESYSTEM_NAME_LENGTH} characters. SkyPilot appends a '
            'user hash and a suffix to new volume names, so keep the volume '
            'name under 45 characters.')


def apply_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Creates a Lambda filesystem, or resolves an existing one by name.

    A filesystem with the same `name_on_cloud` in the same region is reused.
    """
    name_on_cloud = config.name_on_cloud
    region = config.region
    assert name_on_cloud is not None
    if not region:
        raise ValueError('A region is required for Lambda filesystems.')
    client = lambda_utils.LambdaCloudClient()
    filesystem = _find_filesystem(client, name_on_cloud, region)
    if filesystem is None:
        if config.config.get('use_existing'):
            raise ValueError(f'Lambda filesystem {name_on_cloud!r} does not '
                             f'exist in region {region!r} while use_existing '
                             'is True.')
        _validate_name_on_cloud(name_on_cloud)
        filesystem = client.create_filesystem(name_on_cloud, region)
        logger.info(f'Created Lambda filesystem {name_on_cloud} '
                    f'(id={filesystem.get("id")}) in {region}.')
    else:
        logger.debug(f'Using existing Lambda filesystem {name_on_cloud} '
                     f'(id={filesystem.get("id")}) in {region}.')
    filesystem_id = filesystem.get('id')
    if not filesystem_id:
        raise RuntimeError(
            f'Lambda filesystem {name_on_cloud!r} has no id returned.')
    config.id_on_cloud = filesystem_id
    return config


def delete_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Deletes a Lambda filesystem. Lambda refuses while it is mounted."""
    name_on_cloud = config.name_on_cloud
    region = config.region
    assert name_on_cloud is not None and region is not None
    client = lambda_utils.LambdaCloudClient()
    filesystem_id = config.id_on_cloud
    if not filesystem_id:
        filesystem = _find_filesystem(client, name_on_cloud, region)
        filesystem_id = filesystem.get('id') if filesystem else None
    if not filesystem_id:
        logger.warning(f'Lambda filesystem {name_on_cloud} not found in '
                       f'{region}; skip delete.')
        return config
    client.delete_filesystem(filesystem_id)
    logger.info(f'Deleted Lambda filesystem {name_on_cloud} '
                f'(id={filesystem_id}).')
    return config


def _instances_by_filesystem(
        client: lambda_utils.LambdaCloudClient) -> Dict[str, List[str]]:
    """Maps filesystem name to the names of the instances mounting it."""
    usedby: Dict[str, List[str]] = {}
    for instance in client.list_instances():
        if instance.get('status') == 'terminated':
            continue
        instance_name = instance.get('name')
        if not instance_name:
            continue
        for filesystem_name in instance.get('file_system_names') or []:
            usedby.setdefault(filesystem_name, []).append(instance_name)
    return usedby


def _clusters_for_instances(instance_names: List[str]) -> List[str]:
    """Maps instance names back to SkyPilot cluster names.

    Lambda instances are named `<cluster_name_on_cloud>-{head,worker}`.
    """
    clusters = []
    for record in global_user_state.get_clusters():
        handle = record.get('handle')
        name_on_cloud = getattr(handle, 'cluster_name_on_cloud', None)
        if not name_on_cloud:
            continue
        if any(
                instance_name.startswith(f'{name_on_cloud}-')
                for instance_name in instance_names):
            clusters.append(record['name'])
    return clusters


def get_volume_usedby(
    config: models.VolumeConfig,) -> Tuple[List[str], List[str]]:
    """Gets the instances and clusters mounting this filesystem.

    Lambda lists mounted filesystems by name, and names are unique per
    account, so the region does not need to be checked.

    Returns:
      (usedby_instances, usedby_clusters)
    """
    client = lambda_utils.LambdaCloudClient()
    instance_names = _instances_by_filesystem(client).get(
        config.name_on_cloud, [])
    return instance_names, _clusters_for_instances(instance_names)


def get_all_volumes_usedby(
    configs: List[models.VolumeConfig],
) -> Tuple[Dict[str, Any], Dict[str, Any], Set[str]]:
    """Gets the usedby resources of all volumes with one instance listing.

    Returns:
        usedby_instances: Volume name on cloud to instances mounting it.
        usedby_clusters: Volume name on cloud to clusters mounting it.
        failed_volume_names: Volumes whose usedby info failed to fetch.
    """
    try:
        by_filesystem = _instances_by_filesystem(
            lambda_utils.LambdaCloudClient())
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to list Lambda instances: {e}')
        return {}, {}, {config.name for config in configs}
    used_by_instances: Dict[str, Any] = {}
    used_by_clusters: Dict[str, Any] = {}
    for config in configs:
        instance_names = by_filesystem.get(config.name_on_cloud, [])
        used_by_instances[config.name_on_cloud] = instance_names
        used_by_clusters[config.name_on_cloud] = _clusters_for_instances(
            instance_names)
    return used_by_instances, used_by_clusters, set()


def map_all_volumes_usedby(
        used_by_instances: Dict[str, Any], used_by_clusters: Dict[str, Any],
        config: models.VolumeConfig) -> Tuple[List[str], List[str]]:
    """Maps the usedby resources of a volume."""
    return (used_by_instances.get(config.name_on_cloud, []),
            used_by_clusters.get(config.name_on_cloud, []))
