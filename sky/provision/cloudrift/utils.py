"""Utilities for CloudRift cloud provider."""

import os
import uuid
from typing import Any, Dict, List, Optional

from sky.adaptors import cloudrift
from sky.provision import common
from sky.utils import common_utils

# CloudRift credentials environment variable
_CLOUDRIFT_CREDENTIALS_PATH = 'CLOUDRIFT_CREDENTIALS_PATH'


class CloudRiftError(Exception):
    """Raised when CloudRift API returns an error."""
    pass


def get_credentials_path() -> Optional[str]:
    """Returns the path to the CloudRift credentials file.

    Returns:
        The path to the CloudRift credentials file, or None if not found.
    """
    # Check if the user has set the environment variable
    if _CLOUDRIFT_CREDENTIALS_PATH in os.environ:
        return os.environ[_CLOUDRIFT_CREDENTIALS_PATH]
    
    # Default path for CloudRift credentials
    default_path = os.path.expanduser('~/.config/cloudrift/config.yaml')
    if os.path.exists(default_path):
        return default_path
    
    return None

# TODO: Implement actual CloudRift client when API is available
# For now, return a dummy client object
class CloudRiftClient:
    """Dummy CloudRift client."""
    
    def __init__(self):
        self.instances = CloudRiftInstancesAPI()
        self.images = CloudRiftImagesAPI()

class CloudRiftInstancesAPI:
    """Dummy CloudRift instances API."""
    
    _instances = {}  # In-memory storage for instances
    
    def list_instance_types(self) -> Dict:
        """Lists instance types."""
        return {'instance_types': [
            {
                'name': 'standard-4',
                'vcpus': 4,
                'memory': 16,
                'accelerators': None
            },
            {
                'name': 'gpu-a100x1-80gb',
                'vcpus': 8,
                'memory': 32,
                'accelerators': {'A100': 1}
            }
        ]}

    def list_instances(self) -> List[Dict[str, Any]]:
        """Lists instances."""
        return list(self._instances.values())
    
    def list(self) -> Dict[str, List[Dict[str, Any]]]:
        """Lists instances (original API format)."""
        return {'instances': list(self._instances.values())}

    def create(self, instance_type: str, region: str, zone: Optional[str] = None) -> Dict:
        """Creates an instance."""
        instance_id = f'inst-{uuid.uuid4().hex[:8]}'
        instance = {
            'id': instance_id,
            'name': f'instance-{instance_id}',
            'status': 'running',
            'instance_type': instance_type,
            'region': region,
            'zone': zone,
            'public_ip': f'10.0.0.{uuid.uuid4().hex[:2]}',
            'private_ip': f'192.168.1.{uuid.uuid4().hex[:2]}'
        }
        self._instances[instance_id] = instance
        return {'instance': instance}

    def delete(self, instance_id: str) -> Dict:
        """Deletes an instance."""
        if instance_id in self._instances:
            instance = self._instances.pop(instance_id)
            return {'instance': instance}
        return {'instance': {'id': instance_id}}

class CloudRiftImagesAPI:
    """Dummy CloudRift images API."""
    
    def get(self, image_id: str) -> Dict:
        """Gets image information."""
        return {
            'image': {
                'id': image_id,
                'name': f'cloudrift-image-{image_id}',
                'size_gigabytes': 10.0
            }
        }

def filter_instances(
    cluster_name_on_cloud: str,
    status_filters: Optional[List[str]] = None
) -> Dict[str, Dict[str, Any]]:
    """Filter instances by cluster name and status.

    Args:
        cluster_name_on_cloud: The name of the cluster on cloud.
        status_filters: The status to filter by.

    Returns:
        A dict mapping from instance id to instance metadata.
    """
    # Call CloudRift API to list all instances
    instances_list = client().instances.list_instances()
    
    filtered = {}
    for instance in instances_list:
        name = instance.get('name', '')
        if not name.startswith(cluster_name_on_cloud):
            continue
        if status_filters is not None and instance.get('status') not in status_filters:
            continue
        filtered[name] = instance
    
    return filtered


def create_instance(
    region: str,
    cluster_name_on_cloud: str,
    instance_type: str,
    config: common.ProvisionConfig
) -> Dict[str, Any]:
    """Create an instance.

    Args:
        region: The region to create the instance in.
        cluster_name_on_cloud: The name of the cluster on cloud.
        instance_type: Either 'head' or 'worker'.
        config: The provision config.

    Returns:
        The created instance metadata.
    """
    # Generate a unique suffix for the instance name
    suffix = uuid.uuid4().hex[:8]
    name = f'{cluster_name_on_cloud}-{suffix}-{instance_type}'
    
    # Create the instance using CloudRift API
    response = client().instances.create(
        instance_type=config.instance_type,
        region=region,
        zone=None  # CloudRift doesn't use zones
    )
    
    # Extract the instance from the response
    instance = response.get('instance', {})
    # Add the name to the instance metadata
    instance['name'] = name
    # Set status to running
    instance['status'] = 'running'
    # Dummy public IP
    instance['public_ip'] = f'10.0.0.{uuid.uuid4().hex[:2]}'
    
    return instance


def start_instance(instance: Dict[str, Any]) -> None:
    """Start an instance.

    Args:
        instance: The instance to start.
    """
    # Set the status to running
    instance['status'] = 'running'


def stop_instance(instance: Dict[str, Any]) -> None:
    """Stop an instance.

    Args:
        instance: The instance to stop.
    """
    # Set the status to stopped
    instance['status'] = 'stopped'


def down_instance(instance: Dict[str, Any]) -> None:
    """Terminate an instance.

    Args:
        instance: The instance to terminate.
    """
    # Call CloudRift API to delete the instance
    client().instances.delete(instance_id=instance['id'])
    # Set the status to terminated
    instance['status'] = 'terminated'


def rename_instance(instance: Dict[str, Any], new_name: str) -> None:
    """Rename an instance.

    Args:
        instance: The instance to rename.
        new_name: The new name for the instance.
    """
    old_name = instance['name']
    instance['name'] = new_name


def open_ports_instance(instance: Dict[str, Any], ports: List[str]) -> None:
    """Open ports for an instance.

    Args:
        instance: The instance to open ports for.
        ports: The ports to open.
    """
    # In a real implementation, this would call CloudRift API to open ports
    # For now, we just log the action
    pass


def client():
    """Returns a CloudRift client.

    Returns:
        A CloudRift client object.

    Raises:
        CloudRiftError: If CloudRift client creation fails.
    """
    # Check that the CloudRift Python package is installed
    installed, err_msg = cloudrift.check_exceptions_dependencies_installed()
    if not installed:
        raise CloudRiftError(f'CloudRift dependencies not installed: {err_msg}')
    
    return CloudRiftClient()
