"""Volume management core."""

import contextlib
import os
from typing import Any, Dict, Generator, List, Optional
import uuid

import filelock

from sky import global_user_state
from sky import models
from sky import provision
from sky import sky_logging
from sky.schemas.api import responses
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# Filelocks for the storage management.
VOLUME_LOCK_PATH = os.path.expanduser('~/.sky/.{volume_name}.lock')
VOLUME_LOCK_TIMEOUT_SECONDS = 20


def volume_refresh():
    """Refreshes the volume status."""
    volumes = global_user_state.get_volumes()
    for volume in volumes:
        volume_name = volume.get('name')
        config = volume.get('handle')
        if config is None:
            logger.warning(f'Volume {volume_name} has no handle.'
                           'Skipping status refresh...')
            continue
        cloud = config.cloud
        usedby_pods, _ = provision.get_volume_usedby(cloud, config)
        with _volume_lock(volume_name):
            latest_volume = global_user_state.get_volume_by_name(volume_name)
            if latest_volume is None:
                logger.warning(f'Volume {volume_name} not found.')
                continue
            status = latest_volume.get('status')
            if not usedby_pods:
                if status != status_lib.VolumeStatus.READY:
                    logger.info(f'Update volume {volume_name} '
                                f'status to READY')
                    global_user_state.update_volume_status(
                        volume_name, status=status_lib.VolumeStatus.READY)
            else:
                if status != status_lib.VolumeStatus.IN_USE:
                    logger.info(f'Update volume {volume_name} '
                                f'status to IN_USE, usedby: {usedby_pods}')
                    global_user_state.update_volume_status(
                        volume_name, status=status_lib.VolumeStatus.IN_USE)


def volume_list() -> List[responses.VolumeRecord]:
    """Gets the volumes.

    Returns:
        [
            {
                'name': str,
                'type': str,
                'launched_at': int timestamp of creation,
                'cloud': str,
                'region': str,
                'zone': str,
                'size': str,
                'config': Dict[str, Any],
                'name_on_cloud': str,
                'user_hash': str,
                'workspace': str,
                'last_attached_at': int timestamp of last attachment,
                'last_use': last command,
                'status': sky.VolumeStatus,
                'usedby_pods': List[str],
                'usedby_clusters': List[str],
            }
        ]
    """
    with rich_utils.safe_status(ux_utils.spinner_message('Listing volumes')):
        volumes = global_user_state.get_volumes()
        cloud_to_configs: Dict[str, List[models.VolumeConfig]] = {}
        for volume in volumes:
            config = volume.get('handle')
            if config is None:
                volume_name = volume.get('name')
                logger.warning(f'Volume {volume_name} has no handle.')
                continue
            cloud = config.cloud
            if cloud not in cloud_to_configs:
                cloud_to_configs[cloud] = []
            cloud_to_configs[cloud].append(config)

        cloud_to_used_by_pods, cloud_to_used_by_clusters = {}, {}
        for cloud, configs in cloud_to_configs.items():
            used_by_pods, used_by_clusters = provision.get_all_volumes_usedby(
                cloud, configs)
            cloud_to_used_by_pods[cloud] = used_by_pods
            cloud_to_used_by_clusters[cloud] = used_by_clusters

        all_users = global_user_state.get_all_users()
        user_map = {user.id: user.name for user in all_users}
        records = []
        for volume in volumes:
            volume_name = volume.get('name')
            record = {
                'name': volume_name,
                'launched_at': volume.get('launched_at'),
                'user_hash': volume.get('user_hash'),
                'user_name': user_map.get(volume.get('user_hash'), ''),
                'workspace': volume.get('workspace'),
                'last_attached_at': volume.get('last_attached_at'),
                'last_use': volume.get('last_use'),
                'usedby_pods': [],
                'usedby_clusters': [],
            }
            status = volume.get('status')
            if status is not None:
                record['status'] = status.value
            else:
                record['status'] = ''
            config = volume.get('handle')
            if config is None:
                logger.warning(f'Volume {volume_name} has no handle.')
                continue
            cloud = config.cloud
            usedby_pods, usedby_clusters = provision.map_all_volumes_usedby(
                cloud,
                cloud_to_used_by_pods[cloud],
                cloud_to_used_by_clusters[cloud],
                config,
            )
            record['type'] = config.type
            record['cloud'] = config.cloud
            record['region'] = config.region
            record['zone'] = config.zone
            record['size'] = config.size
            record['config'] = config.config
            record['name_on_cloud'] = config.name_on_cloud
            record['usedby_pods'] = usedby_pods
            record['usedby_clusters'] = usedby_clusters
            records.append(responses.VolumeRecord(**record))
        return records


def volume_delete(names: List[str]) -> None:
    """Deletes volumes.

    Args:
        names: List of volume names to delete.

    Raises:
        ValueError: If the volume does not exist
          or is in use or has no handle.
    """
    with rich_utils.safe_status(ux_utils.spinner_message('Deleting volumes')):
        for name in names:
            volume = global_user_state.get_volume_by_name(name)
            if volume is None:
                raise ValueError(f'Volume {name} not found.')
            config = volume.get('handle')
            if config is None:
                raise ValueError(f'Volume {name} has no handle.')
            cloud = config.cloud
            usedby_pods, usedby_clusters = provision.get_volume_usedby(
                cloud, config)
            if usedby_clusters:
                usedby_clusters_str = ', '.join(usedby_clusters)
                cluster_str = 'clusters' if len(
                    usedby_clusters) > 1 else 'cluster'
                raise ValueError(f'Volume {name} is used by {cluster_str}'
                                 f' {usedby_clusters_str}.')
            if usedby_pods:
                usedby_pods_str = ', '.join(usedby_pods)
                pod_str = 'pods' if len(usedby_pods) > 1 else 'pod'
                raise ValueError(
                    f'Volume {name} is used by {pod_str} {usedby_pods_str}.')
            logger.debug(f'Deleting volume {name} with config {config}')
            with _volume_lock(name):
                provision.delete_volume(cloud, config)
                global_user_state.delete_volume(name)


def volume_apply(
    name: str,
    volume_type: str,
    cloud: str,
    region: Optional[str],
    zone: Optional[str],
    size: Optional[str],
    config: Dict[str, Any],
    labels: Optional[Dict[str, str]] = None,
    use_existing: Optional[bool] = None,
) -> None:
    """Creates or registers a volume.

    Args:
        name: The name of the volume.
        volume_type: The type of the volume.
        cloud: The cloud of the volume.
        region: The region of the volume.
        zone: The zone of the volume.
        size: The size of the volume.
        config: The configuration of the volume.
        labels: The labels of the volume.
        use_existing: Whether to use an existing volume.

    """
    with rich_utils.safe_status(ux_utils.spinner_message('Creating volume')):
        # Reuse the method for cluster name on cloud to
        # generate the storage name on cloud.
        cloud_obj = registry.CLOUD_REGISTRY.from_str(cloud)
        assert cloud_obj is not None
        region, zone = cloud_obj.validate_region_zone(region, zone)
        if use_existing:
            name_on_cloud = name
        else:
            name_uuid = str(uuid.uuid4())[:6]
            name_on_cloud = common_utils.make_cluster_name_on_cloud(
                name, max_length=cloud_obj.max_cluster_name_length())
            name_on_cloud += '-' + name_uuid
        config = models.VolumeConfig(
            name=name,
            type=volume_type,
            cloud=str(cloud_obj),
            region=region,
            zone=zone,
            size=size,
            config=config,
            name_on_cloud=name_on_cloud,
            labels=labels,
        )
        logger.debug(
            f'Creating volume {name} on cloud {cloud} with config {config}')
        with _volume_lock(name):
            current_volume = global_user_state.get_volume_by_name(name)
            if current_volume is not None:
                logger.info(f'Volume {name} already exists.')
                return
            config = provision.apply_volume(cloud, config)
            global_user_state.add_volume(name, config,
                                         status_lib.VolumeStatus.READY)
        logger.info(f'Created volume {name} on cloud {cloud}')


@contextlib.contextmanager
def _volume_lock(volume_name: str) -> Generator[None, None, None]:
    """Context manager for volume lock."""
    try:
        with filelock.FileLock(VOLUME_LOCK_PATH.format(volume_name=volume_name),
                               VOLUME_LOCK_TIMEOUT_SECONDS):
            yield
    except filelock.Timeout as e:
        raise RuntimeError(
            f'Failed to update user due to a timeout '
            f'when trying to acquire the lock at '
            f'{VOLUME_LOCK_PATH.format(volume_name=volume_name)}. '
            'Please try again or manually remove the lock '
            f'file if you believe it is stale.') from e
