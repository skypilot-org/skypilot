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


def volume_refresh() -> None:
    """Refreshes volume status by querying cloud APIs.

    This is called by the background daemon to update volume state.
    It updates status, error messages, and usage information in the database.

    Status transitions:
    - NOT_READY: Volume has errors (e.g., pending due to misconfiguration)
    - IN_USE: Volume is healthy and in use
    - READY: Volume is healthy and not in use
    """
    volumes = global_user_state.get_volumes(is_ephemeral=False)

    # Group volumes by cloud for batch API calls
    cloud_to_configs: Dict[str, List[models.VolumeConfig]] = {}
    volume_name_to_config: Dict[str, models.VolumeConfig] = {}
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
        volume_name_to_config[volume.get('name')] = config

    # Check for volume errors (e.g., misconfiguration)
    cloud_to_volume_errors: Dict[str, Dict[str, Optional[str]]] = {}
    for cloud, configs in cloud_to_configs.items():
        try:
            volume_errors = provision.get_all_volumes_errors(cloud, configs)
            cloud_to_volume_errors[cloud] = volume_errors
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(
                f'Failed to get volume errors for volumes on {cloud}: {e}')
            cloud_to_volume_errors[cloud] = {}

    # Get usedby info for all volumes
    cloud_to_used_by_pods: Dict[str, Dict[str, Any]] = {}
    cloud_to_used_by_clusters: Dict[str, Dict[str, Any]] = {}
    cloud_to_failed_volume_names: Dict[str, set] = {}
    for cloud, configs in cloud_to_configs.items():
        try:
            used_by_pods, used_by_clusters, failed_volume_names = (
                provision.get_all_volumes_usedby(cloud, configs))
            cloud_to_used_by_pods[cloud] = used_by_pods
            cloud_to_used_by_clusters[cloud] = used_by_clusters
            cloud_to_failed_volume_names[cloud] = failed_volume_names
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'Failed to get usedby info for volumes on {cloud}: {e}')
            cloud_to_used_by_pods[cloud] = {}
            cloud_to_used_by_clusters[cloud] = {}
            cloud_to_failed_volume_names[cloud] = {
                config.name for config in configs
            }

    # Update volume statuses in database
    for volume in volumes:
        volume_name = volume.get('name')
        config = volume_name_to_config.get(volume_name)
        if config is None:
            continue

        cloud = config.cloud

        # Skip if usedby fetch failed
        if volume_name in cloud_to_failed_volume_names.get(cloud, set()):
            logger.debug(f'Skipping status update for volume {volume_name} '
                         f'due to failed usedby fetch')
            continue

        # Check for volume errors first
        volume_error = cloud_to_volume_errors.get(cloud, {}).get(volume_name)

        # Get usedby info
        usedby_pods, usedby_clusters = provision.map_all_volumes_usedby(
            cloud,
            cloud_to_used_by_pods.get(cloud, {}),
            cloud_to_used_by_clusters.get(cloud, {}),
            config,
        )

        with _volume_lock(volume_name):
            latest_volume = global_user_state.get_volume_by_name(volume_name)
            if latest_volume is None:
                logger.warning(f'Volume {volume_name} not found.')
                continue

            current_status = latest_volume.get('status')
            current_error = latest_volume.get('error_message')
            current_usedby_pods = latest_volume.get('usedby_pods', [])
            current_usedby_clusters = latest_volume.get('usedby_clusters', [])

            # Determine new status and error_message
            if volume_error:
                new_status = status_lib.VolumeStatus.NOT_READY
                new_error = volume_error
            elif usedby_pods:
                new_status = status_lib.VolumeStatus.IN_USE
                new_error = None
            else:
                new_status = status_lib.VolumeStatus.READY
                new_error = None

            # Update if anything changed
            status_changed = current_status != new_status
            error_changed = current_error != new_error
            usedby_changed = (
                set(current_usedby_pods) != set(usedby_pods) or
                set(current_usedby_clusters) != set(usedby_clusters))

            if status_changed or error_changed or usedby_changed:
                logger.info(f'Update volume {volume_name} status to '
                            f'{new_status.value}'
                            f'{", error: " + new_error if new_error else ""}')
                global_user_state.update_volume_status(
                    volume_name,
                    status=new_status,
                    error_message=new_error,
                    usedby_pods=usedby_pods,
                    usedby_clusters=usedby_clusters)
            volume_config = latest_volume.get('handle')
            if volume_config is None:
                continue
            # For in-cluster volumes created without setting the region
            # explicitly before PR
            # https://github.com/skypilot-org/skypilot/pull/8386, the region
            # will be None. In this case, when the user enables the external
            # kubeconfig, the region will be shown as the default context in
            # the kubeconfig file. We need to refresh the volume config to set
            # the region to the in-cluster context name for these volumes.
            need_refresh, volume_config = provision.refresh_volume_config(
                volume_config.cloud, volume_config)
            if need_refresh:
                global_user_state.update_volume_config(volume_name,
                                                       volume_config)


def volume_list(
    is_ephemeral: Optional[bool] = None,
    refresh: bool = False,
) -> List[responses.VolumeRecord]:
    """Gets volumes from the database.

    Args:
        is_ephemeral: Whether to include ephemeral volumes.
        refresh: If True, refresh volume state from cloud APIs before returning.

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
                'usedby_fetch_failed': bool,
                'is_ephemeral': bool,
                'error_message': Optional[str],
            }
        ]
    """
    if refresh:
        volume_refresh()
    with rich_utils.safe_status(ux_utils.spinner_message('Listing volumes')):
        volumes = global_user_state.get_volumes(is_ephemeral=is_ephemeral)
        all_users = global_user_state.get_all_users()
        user_map = {user.id: user.name for user in all_users}

        records = []
        for volume in volumes:
            volume_name = volume.get('name')
            config = volume.get('handle')
            if config is None:
                logger.warning(f'Volume {volume_name} has no handle.')
                continue

            status = volume.get('status')
            record: Dict[str, Any] = {
                'name': volume_name,
                'launched_at': volume.get('launched_at'),
                'user_hash': volume.get('user_hash'),
                'user_name': user_map.get(volume.get('user_hash'), ''),
                'workspace': volume.get('workspace'),
                'last_attached_at': volume.get('last_attached_at'),
                'last_use': volume.get('last_use'),
                'status': status.value if status is not None else '',
                'usedby_pods': volume.get('usedby_pods', []),
                'usedby_clusters': volume.get('usedby_clusters', []),
                'usedby_fetch_failed': False,
                'is_ephemeral': volume.get('is_ephemeral', False),
                'error_message': volume.get('error_message'),
                'type': config.type,
                'cloud': config.cloud,
                'region': config.region,
                'zone': config.zone,
                'size': config.size,
                'config': config.config,
                'name_on_cloud': config.name_on_cloud,
            }
            records.append(responses.VolumeRecord(**record))
        return records


def volume_delete(names: List[str],
                  ignore_not_found: bool = False,
                  purge: bool = False) -> None:
    """Deletes volumes.

    Args:
        names: List of volume names to delete.
        ignore_not_found: If True, ignore volumes that are not found.
        purge: If True, delete the volume from the database even if the
          deletion API fails.

    Raises:
        ValueError: If the volume does not exist
          or is in use or has no handle.
    """
    with rich_utils.safe_status(ux_utils.spinner_message('Deleting volumes')):
        for name in names:
            volume = global_user_state.get_volume_by_name(name)
            if volume is None:
                if ignore_not_found:
                    continue
                raise ValueError(f'Volume {name} not found.')
            config = volume.get('handle')
            if config is None:
                raise ValueError(f'Volume {name} has no handle.')
            cloud = config.cloud
            if not purge:
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
                        f'Volume {name} is used by {pod_str} {usedby_pods_str}.'
                    )
            logger.debug(f'Deleting volume {name} with config {config}')
            with _volume_lock(name):
                try:
                    provision.delete_volume(cloud, config)
                except Exception as e:  # pylint: disable=broad-except
                    if purge:
                        logger.warning(f'Failed to delete volume {name} '
                                       f'on {cloud}: {e}. Purging from '
                                       'database.')
                    else:
                        raise
                global_user_state.delete_volume(name)
        logger.info(f'Deleted volumes: {names}')


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
    is_ephemeral: bool = False,
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
        is_ephemeral: Whether the volume is ephemeral.
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
            global_user_state.add_volume(
                name,
                config,
                status_lib.VolumeStatus.READY,
                is_ephemeral,
            )
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
