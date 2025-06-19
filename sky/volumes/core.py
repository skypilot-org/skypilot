
"""Workspace management core."""

import concurrent.futures
from typing import Any, Callable, Dict, List, Optional, Generator

import filelock

from sky import check as sky_check
from sky import exceptions
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.skylet import constants
from sky.usage import usage_lib
from sky.users import permission
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import config_utils
from sky import data
from sky.utils import schemas
from sky.workspaces import utils as workspaces_utils
import contextlib
import os
import sky

import fastapi
import filelock
from passlib.hash import apr_md5_crypt

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.server.requests import payloads
from sky.skylet import constants
from sky.users import permission
from sky.users import rbac
from sky.provision import common
from sky.utils import common_utils
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as api_requests
from sky import global_user_state
from sky import provision
from sky import clouds
from sky.storages import storage
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

# Filelocks for the storage management.
STORAGE_LOCK_PATH = os.path.expanduser('~/.sky/.{storage_name}.lock')
STORAGE_LOCK_TIMEOUT_SECONDS = 20

def storage_ls() -> List[Dict[str, Any]]:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Gets the storages.

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
    storages = global_user_state.get_storage()
    for storage in storages:
        storage['store'] = list(storage.pop('handle').sky_stores.keys())
    return storages


def storage_delete(name: str) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Deletes a storage.

    Raises:
        ValueError: If the storage does not exist.
    """
    # TODO(zhwu): check the storage owner matches the current user
    handle = global_user_state.get_handle_from_storage_name(name)
    if handle is None:
        raise ValueError(f'Storage name {name!r} not found.')
    else:
        storage_object = data.Storage(name=handle.storage_name,
                                      source=handle.source,
                                      sync_on_reconstruction=False)
        storage_object.delete()

def storage_create(name: str, type: str, size: int, cloud: str, region: Optional[str], zone: Optional[str], spec: Dict[str, Any]) -> None:
    """Creates a storage."""
    # Reuse the method for cluster name on cloud to generate the storage name on cloud.
    cloud_obj=sky.CLOUD_REGISTRY.from_str(cloud)
    name_on_cloud=common_utils.make_cluster_name_on_cloud(
        name, max_length=cloud_obj.max_cluster_name_length())
    config=common.StorageConfig(
        name=name,
        type=type,
        size=size,
        cloud=cloud,
        region=region,
        zone=zone,
        spec=spec,
        name_on_cloud=name_on_cloud,
    )
    logger.info(f'Creating storage {name} on cloud {cloud} with config {config}')
    with _storage_lock(name):
        config=provision.create_storage(cloud,config)
        global_user_state.add_or_update_storage(name,config,status_lib.StorageStatus.READY)

@contextlib.contextmanager
def _storage_lock(storage_name: str) -> Generator[None, None, None]:
    """Context manager for storage lock."""
    try:
        with filelock.FileLock(STORAGE_LOCK_PATH.format(storage_name=storage_name),
                               STORAGE_LOCK_TIMEOUT_SECONDS):
            yield
    except filelock.Timeout as e:
        raise RuntimeError(f'Failed to update user due to a timeout '
                           f'when trying to acquire the lock at '
                           f'{STORAGE_LOCK_PATH.format(storage_name=storage_name)}. '
                           'Please try again or manually remove the lock '
                           f'file if you believe it is stale.') from e