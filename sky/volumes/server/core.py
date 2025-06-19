"""Volume management core."""

import contextlib
import os
from typing import Any, Dict, Generator, List, Optional
import uuid

import filelock

import sky
from sky import global_user_state
from sky import models
from sky import provision
from sky import sky_logging
from sky.utils import common_utils
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

# Filelocks for the storage management.
VOLUME_LOCK_PATH = os.path.expanduser('~/.sky/.{volume_name}.lock')
VOLUME_LOCK_TIMEOUT_SECONDS = 20


def volume_list() -> List[Dict[str, Any]]:
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
                'spec': Dict[str, Any],
                'name_on_cloud': str,
                'user_hash': str,
                'workspace': str,
                'last_attached_at': int timestamp of last attachment,
                'last_use': last command,
                'status': sky.VolumeStatus,
            }
        ]
    """
    volumes = global_user_state.get_volumes()
    records = []
    for volume in volumes:
        volume_name = volume.get('name')
        record = {
            'name': volume_name,
            'launched_at': volume.get('launched_at'),
            'user_hash': volume.get('user_hash'),
            'workspace': volume.get('workspace'),
            'last_attached_at': volume.get('last_attached_at'),
            'last_use': volume.get('last_use'),
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
        record['type'] = config.type
        record['cloud'] = config.cloud
        record['region'] = config.region
        record['zone'] = config.zone
        record['spec'] = config.spec
        record['name_on_cloud'] = config.name_on_cloud
        records.append(record)
    return records


def volume_delete(names: List[str]) -> None:
    """Deletes volumes.

    Args:
        names: List of volume names to delete.

    Raises:
        ValueError: If the volume does not exist or has no handle.
    """
    for name in names:
        volume = global_user_state.get_volume_by_name(name)
        if volume is None:
            raise ValueError(f'Volume {name} not found.')
        config = volume.get('handle')
        if config is None:
            raise ValueError(f'Volume {name} has no handle.')
        logger.info(f'Deleting volume {name} with config {config}')
        cloud = config.cloud
        with _volume_lock(name):
            provision.delete_volume(cloud, config)
            global_user_state.delete_volume(name)


def volume_apply(name: str, volume_type: str, cloud: str, region: Optional[str],
                 zone: Optional[str], spec: Dict[str, Any]) -> None:
    """Creates or registers a volume.

    Args:
        name: The name of the volume.
        volume_type: The type of the volume.
        cloud: The cloud of the volume.
        region: The region of the volume.
        zone: The zone of the volume.
        spec: The specification of the volume.

    """
    # Reuse the method for cluster name on cloud to
    # generate the storage name on cloud.
    cloud_obj = sky.CLOUD_REGISTRY.from_str(cloud)
    assert cloud_obj is not None
    name_uuid = str(uuid.uuid4())[:6]
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name + '-' + name_uuid, max_length=cloud_obj.max_cluster_name_length())
    config = models.VolumeConfig(
        name=name,
        type=volume_type,
        cloud=cloud,
        region=region,
        zone=zone,
        spec=spec,
        name_on_cloud=name_on_cloud,
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
