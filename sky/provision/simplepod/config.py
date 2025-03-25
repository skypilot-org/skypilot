"""SimplePod configuration bootstrapping."""
import typing
from typing import Dict, List, Optional

from sky import clouds
from sky import exceptions
from sky.clouds import service_catalog
from sky.provision import docker_utils
from sky.provision import common
from sky.provision.simplepod import instance
from sky.provision.simplepod import simplepod_utils
from sky.utils import command_runner
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky.data import Storage

def _get_image_id(resources: 'resources_utils.ResourceDict',
                  region: str,
                  clouds: List[str]) -> str:
    """Returns the image id for the given region."""
    if resources['image_id'] is None:
        return 'ubuntu-22.04'  # Default SimplePod image
    image_id = resources['image_id']
    if isinstance(image_id, str):
        return image_id
    elif isinstance(image_id, dict):
        if image_id.get('simplepod') is not None:
            return image_id['simplepod']
        return 'ubuntu-22.04'  # Fallback to default
    return 'ubuntu-22.04'

def bootstrap_instances(region: str,
                       cluster_name: str,
                       config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Bootstrap instances configuration for SimplePod.

    Fills cloud-specific configuration fields based on SimplePod's requirements.
    """
    config = config.copy()
    resources = config.resources

    # Set image if not specified
    resources['image_id'] = _get_image_id(resources, region, ['simplepod'])

    # Fill instance type/resources if not specified
    if resources.get('instance_type') is None:
        if resources.get('accelerators') is not None:
            acc_name = list(resources['accelerators'].keys())[0]
            acc_count = list(resources['accelerators'].values())[0]
            resources['instance_type'] = f'{acc_name}:{acc_count}'

    # Set up instance manager
    config.provider = instance.SimplePodInstanceManager()
    return config
