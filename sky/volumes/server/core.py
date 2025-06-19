"""Volume management core."""

import contextlib
import os
from typing import Any, Dict, Generator, List, Optional

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
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Gets the volumes.

    Returns:
        [
            {
                'name': str,
                'launched_at': int timestamp of creation,
                'store': List[sky.StoreType],
                'last_use': int timestamp of last use,
                'status': sky.StorageStatus,
            }
        ]
    """
    volumes = global_user_state.get_volume()
    return volumes


def volume_delete(name: str) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Deletes a volume.

    Raises:
        ValueError: If the volume does not exist.
    """
    # TODO(zhwu): check the volume owner matches the current user
    volume = global_user_state.get_volume_by_name(name)
    if volume is None:
        raise ValueError(f'Volume name {name!r} not found.')


def volume_apply(name: str, type: str, cloud: str, region: Optional[str], zone: Optional[str], spec: Dict[str, Any]) -> None:
    """Creates or registers a volume."""
    # Reuse the method for cluster name on cloud to generate the storage name on cloud.
    cloud_obj = sky.CLOUD_REGISTRY.from_str(cloud)
    assert cloud_obj is not None
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, max_length=cloud_obj.max_cluster_name_length())
    config = models.VolumeConfig(
        name=name,
        type=type,
        cloud=cloud,
        region=region,
        zone=zone,
        spec=spec,
        name_on_cloud=name_on_cloud,
    )
    logger.info(f'Creating volume {name} on cloud {cloud} with config {config}')
    with _volume_lock(name):
        config = provision.apply_volume(cloud, config)
        global_user_state.add_volume(name, config, status_lib.VolumeStatus.READY)

@contextlib.contextmanager
def _volume_lock(volume_name: str) -> Generator[None, None, None]:
    """Context manager for volume lock."""
    try:
        with filelock.FileLock(VOLUME_LOCK_PATH.format(volume_name=volume_name),
                               VOLUME_LOCK_TIMEOUT_SECONDS):
            yield
    except filelock.Timeout as e:
        raise RuntimeError(f'Failed to update user due to a timeout '
                           f'when trying to acquire the lock at '
                           f'{VOLUME_LOCK_PATH.format(volume_name=volume_name)}. '
                           'Please try again or manually remove the lock '
                           f'file if you believe it is stale.') from e