"""Volume management core."""

import contextlib
import os
from typing import Any, Dict, Generator, List, Optional, Set, Tuple
import uuid

import filelock

from sky import global_user_state
from sky import models
from sky import provision
from sky import sky_logging
from sky.schemas.api import responses
from sky.server import plugin_hooks
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import ux_utils
from sky.utils import volume as volume_utils

logger = sky_logging.init_logger(__name__)

# Filelocks for the storage management.
VOLUME_LOCK_PATH = os.path.expanduser('~/.sky/.{volume_name}.lock')
VOLUME_LOCK_TIMEOUT_SECONDS = 20


def volume_refresh(volume_names: Optional[List[str]] = None) -> None:
    """Refreshes volume status by querying cloud APIs.

    This is called by the background daemon to update volume state.
    It updates status, error messages, and usage information in the database.

    Status transitions:
    - NOT_READY: Volume has errors (e.g., pending due to misconfiguration)
    - IN_USE: Volume is healthy and in use
    - READY: Volume is healthy and not in use

    Args:
        volume_names: Refresh only these volumes instead of every one. A
            caller waiting on a single volume it just created would otherwise
            take a file lock and a database round-trip per volume in the
            table on every poll, contending with concurrent volume
            operations for the same locks. Names with no row are ignored.
            Cloud calls are per (context, namespace) rather than per volume,
            so this narrows the database work, not necessarily the API calls.
    """
    if volume_names is None:
        volumes = global_user_state.get_volumes(is_ephemeral=False)
    else:
        volumes = global_user_state.get_volumes_from_names(volume_names,
                                                           is_ephemeral=False)

    # Group volumes by cloud for batch API calls
    cloud_to_configs: Dict[str, List[models.VolumeConfig]] = {}
    cloud_to_volume_names: Dict[str, Set[str]] = {}
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
        cloud_to_volume_names.setdefault(cloud, set()).add(volume.get('name'))
        volume_name_to_config[volume.get('name')] = config

    # Check for volume errors (e.g., misconfiguration)
    cloud_to_volume_errors: Dict[str, Dict[str, Optional[str]]] = {}
    cloud_to_error_failed_names: Dict[str, Set[str]] = {}
    for cloud, configs in cloud_to_configs.items():
        try:
            # A cloud with no error check of its own returns both empty, which
            # leaves its volumes eligible for a status update driven by their
            # usedby info alone.
            volume_errors, error_failed_names = (
                provision.get_all_volumes_errors(cloud, configs))
            cloud_to_volume_errors[cloud] = volume_errors
            cloud_to_error_failed_names[cloud] = error_failed_names
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'Failed to get volume errors for volumes on {cloud}: {e}')
            cloud_to_volume_errors[cloud] = {}
            # Do not let an unreadable cloud silently clear every volume's
            # error and mark them all healthy -- keep their current status.
            cloud_to_error_failed_names[cloud] = cloud_to_volume_names.get(
                cloud, set())

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

        # Skip if the error fetch failed: reading an unknown result as "no
        # error" would flip a broken volume to READY.
        if volume_name in cloud_to_error_failed_names.get(cloud, set()):
            logger.debug(f'Skipping status update for volume {volume_name} '
                         f'due to failed error fetch')
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
                'error_may_resolve': bool,
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
            error_message = volume.get('error_message')
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
                'error_message': error_message,
                # NOT_READY covers both a volume still being provisioned and
                # one that will never bind. Only the recorded reason tells them
                # apart, so decide it here rather than leave every caller to
                # match on the message.
                'error_may_resolve':
                    volume_utils.volume_error_may_resolve(error_message),
                'creation_yaml': volume.get('creation_yaml'),
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
                plugin_hooks.fire_volume_deleted(name, config)
        logger.info(f'Deleted volumes: {names}')


def _initial_volume_status(
    cloud: str, config: models.VolumeConfig
) -> Tuple[status_lib.VolumeStatus, Optional[str]]:
    """Determines the status to record for a freshly created volume.

    Creating the backing resource does not mean it is usable: with an
    Immediate-binding storage class the PersistentVolume is provisioned
    asynchronously and may never bind, so recording READY unconditionally
    would advertise a volume that cannot be mounted. Ask the cloud what it
    actually looks like, using the same check the refresh daemon uses so the
    two can never disagree.

    Falls back to READY when the cloud cannot be queried, matching the
    optimistic behavior everywhere else on this path.
    """
    try:
        volume_errors, failed_volume_names = provision.get_all_volumes_errors(
            cloud, [config])
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to check the initial status of volume '
                     f'{config.name}: {e}')
        return status_lib.VolumeStatus.READY, None
    if config.name in failed_volume_names:
        return status_lib.VolumeStatus.READY, None
    error_message = volume_errors.get(config.name)
    if error_message:
        return status_lib.VolumeStatus.NOT_READY, error_message
    return status_lib.VolumeStatus.READY, None


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
    creation_yaml: Optional[str] = None,
) -> None:
    """Creates or registers a volume.

    Callers validate the volume themselves; /volumes/apply does it for API
    clients, and the in-process callers construct their own names and configs.

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
        creation_yaml: The YAML config used to create this volume.
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
        volume_config = models.VolumeConfig(
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
        logger.debug(f'Creating volume {name} on cloud {cloud} with config '
                     f'{volume_config}')
        with _volume_lock(name):
            current_volume = global_user_state.get_volume_by_name(name)
            if current_volume is not None:
                logger.info(f'Volume {name} already exists.')
                return
            volume_config = provision.apply_volume(cloud, volume_config)
            # Only check for duplicates when registering an existing
            # resource. Newly created volumes have a UUID suffix in
            # name_on_cloud so they cannot collide.
            if use_existing:
                _check_duplicate_backend_resource(name, volume_config)
            if is_ephemeral:
                # add_volume forces ephemeral volumes to IN_USE, and they are
                # created inline during provisioning, so probing the cloud
                # here would cost a call whose answer is discarded.
                initial_status = status_lib.VolumeStatus.READY
                initial_error = None
            else:
                initial_status, initial_error = _initial_volume_status(
                    cloud, volume_config)
            global_user_state.add_volume(
                name,
                volume_config,
                initial_status,
                is_ephemeral,
                creation_yaml=creation_yaml,
                error_message=initial_error,
            )
        logger.info(f'Created volume {name} on cloud {cloud}')


def _same_backend_resource(a: models.VolumeConfig,
                           b: models.VolumeConfig) -> bool:
    """Return True if two VolumeConfigs reference the same backend resource."""
    if a.cloud != b.cloud:
        return False

    cloud_lower = a.cloud.lower()

    if cloud_lower == 'kubernetes':
        return (a.name_on_cloud == b.name_on_cloud and a.region == b.region and
                a.config.get('namespace') == b.config.get('namespace'))

    if cloud_lower == 'runpod':
        # If both have id_on_cloud, compare by id (most reliable).
        if a.id_on_cloud is not None and b.id_on_cloud is not None:
            return a.id_on_cloud == b.id_on_cloud
        # Fallback: compare by (name_on_cloud, zone).
        return (a.name_on_cloud == b.name_on_cloud and a.zone == b.zone)

    # Generic fallback for future cloud types.
    return (a.name_on_cloud == b.name_on_cloud and a.region == b.region and
            a.zone == b.zone)


def _check_duplicate_backend_resource(name: str,
                                      config: models.VolumeConfig) -> None:
    """Check if another volume already references the same backend resource.

    Raises:
        ValueError: If a duplicate is found.
    """
    existing_volumes = global_user_state.get_volumes()
    for vol in existing_volumes:
        vol_name = vol.get('name')
        if vol_name == name:
            continue
        vol_config = vol.get('handle')
        if vol_config is None:
            continue
        if _same_backend_resource(config, vol_config):
            raise ValueError(
                f'Volume {name!r} maps to the same backend resource '
                f'as existing volume {vol_name!r} '
                f'(cloud={config.cloud}, '
                f'name_on_cloud={config.name_on_cloud!r}). '
                f'Use the existing volume {vol_name!r} instead, or '
                f'delete it first with: sky volumes delete {vol_name}')


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
