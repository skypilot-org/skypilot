"""Utilities for CloudRift cloud provider."""

import os
from typing import Dict, Optional

from sky.adaptors import cloudrift
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
    
    # TODO: Implement actual CloudRift client when API is available
    # For now, return a dummy client object
    class DummyClient:
        """Dummy CloudRift client."""
        
        def __init__(self):
            self.instances = DummyInstancesAPI()
            self.images = DummyImagesAPI()
    
    class DummyInstancesAPI:
        """Dummy CloudRift instances API."""
        
        def list(self) -> Dict:
            """Lists instances."""
            return {'instances': []}
    
    class DummyImagesAPI:
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
    
    return DummyClient()
