"""Utilities for CloudRift cloud provider."""

import os
import uuid
from typing import Any, Dict, List, Optional, Mapping, Union

from sky.provision import common
from sky.utils import common_utils
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Union

import requests
from packaging import version
from requests import Response

from dstack._internal.core.errors import BackendError, BackendInvalidCredentialsError
from dstack._internal.utils.logging import get_logger

logger = get_logger(__name__)

# CloudRift credentials environment variable
_CLOUDRIFT_CREDENTIALS_PATH = 'CLOUDRIFT_CREDENTIALS_PATH'
CLOUDRIFT_SERVER_ADDRESS = "https://api.cloudrift.ai"
CLOUDRIFT_API_VERSION = "2025-05-29"


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


class RiftClient:
    def __init__(self, api_key: Optional[str] = None):
        self.server_address = CLOUDRIFT_SERVER_ADDRESS
        self.public_api_root = os.path.join(CLOUDRIFT_SERVER_ADDRESS, "api/v1")
        self.internal_api_root = os.path.join(CLOUDRIFT_SERVER_ADDRESS, "internal")
        self.api_key = api_key if api_key else os.getenv("CLOUDRIFT_API_KEY")

    def validate_api_key(self) -> bool:
        """
        Validates the API key by making a request to the server.
        Returns True if the API key is valid, False otherwise.
        """
        try:
            response = self._make_request("auth/me")
            if isinstance(response, dict):
                return response.get("email", False)
            return False
        except BackendInvalidCredentialsError:
            return False
        except Exception as e:
            logger.error(f"Error validating API key: {e}")
            return False

    def get_instance_types(self) -> List[Dict]:
        request_data = {"selector": {"ByServiceAndLocation": {"services": ["vm"]}}}
        response_data = self._make_request("instance-types/list", request_data)
        if isinstance(response_data, dict):
            return response_data.get("instance_types", [])
        return []

    def list_recipies(self) -> List[Dict]:
        request_data = {}
        response_data = self._make_request("recipes/list", request_data)
        if isinstance(response_data, dict):
            return response_data.get("groups", [])
        return []

    def get_vm_recipies(self) -> List[Dict]:
        """
        Retrieves a list of VM recipes from the CloudRift API.
        Returns a list of dictionaries containing recipe information.
        """
        recipe_group = self.list_recipies()
        vm_recipes = []
        for group in recipe_group:
            tags = group.get("tags ", [])
            has_vm = "vm" in tags
            if group.get("name", "").lower() != "linux" and not has_vm:
                continue

            recipes = group.get("recipes", [])
            for recipe in recipes:
                details = recipe.get("details", {})
                if details.get("VirtualMachine", False):
                    vm_recipes.append(recipe)

        return vm_recipes

    def get_vm_image_url(self) -> Optional[str]:
        recipes = self.get_vm_recipies()
        ubuntu_images = []
        for recipe in recipes:
            has_nvidia_driver = "nvidia-driver" in recipe.get("tags", [])
            if not has_nvidia_driver:
                continue

            recipe_name = recipe.get("name", "")
            if "Ubuntu" not in recipe_name:
                continue

            url = recipe["details"].get("VirtualMachine", {}).get("image_url", None)
            version_match = re.search(r".* (\d+\.\d+)", recipe_name)
            if url and version_match and version_match.group(1):
                ubuntu_version = version.parse(version_match.group(1))
                ubuntu_images.append((ubuntu_version, url))

        ubuntu_images.sort(key=lambda x: x[0])  # Sort by version
        if ubuntu_images:
            return ubuntu_images[-1][1]

        return None

    def deploy_instance(
        self, instance_type: str, name:str, region: str, ssh_keys: List[str], cmd: str
    ) -> List[str]:
        image_url = self.get_vm_image_url()
        if not image_url:
            raise BackendError("No suitable VM image found.")

        request_data = {
            "config": {
                "VirtualMachine": {
                    "cloudinit_url": "https://storage.googleapis.com/cloudrift-vm-disks/cloudinit/ubuntu-base.cloudinit", # TODO FIX
                    #"cloudinit_commands": cmd, # TODO FIX
                    "image_url": image_url,
                    "ssh_key": {"PublicKeys": ssh_keys},
                }
            },
            "selector": {
                "ByInstanceTypeAndLocation": {
                    #"datacenters": [region], # TODO add region
                    "instance_type": instance_type,
                }
            },
            "with_public_ip": True,
        }
        logger.debug("Deploying instance with request data: %s", request_data)
        print(request_data)

        response_data = self._make_request("instances/rent", request_data)
        if isinstance(response_data, dict):
            return response_data.get("instance_ids", [])
        return []

    def list_instances(self, instance_ids: Optional[List[str]] = None) -> List[Dict]:
        request_data = {
            "selector": {
                "ByStatus": ["Initializing", "Active", "Deactivating"],
            }
        }
        # TODO use instance_ids
        logger.debug("Listing instances with request data: %s", request_data)
        response_data = self._make_request("instances/list", request_data)
        if isinstance(response_data, dict):
            return response_data.get("instances", [])

        return []

    def get_instance_by_id(self, instance_id: str) -> Optional[Dict]:
        request_data = {"selector": {"ById": [instance_id]}}
        logger.debug("Getting instance with request data: %s", request_data)
        response_data = self._make_request("instances/list", request_data)
        if isinstance(response_data, dict):
            instances = response_data.get("instances", [])
            if isinstance(instances, list) and len(instances) > 0:
                return instances[0]

        return None

    def is_instance_ready(self, instance_id: str) -> bool:
        """
        Checks if the instance with the given ID is ready.
        Returns True if the instance is ready, False otherwise.
        """
        instance_info = self.get_instance_by_id(instance_id)
        if instance_info:
            instance_type = instance_info.get("node_mode", "")
            if instance_type == "VirtualMachine":
                vms = instance_info.get("virtual_machines", [])
                if len(vms) > 0:
                    vm_ready = vms[0].get("ready", False)
                    return vm_ready
            else:
                return instance_info.get("status", "") == "Active"
        return False

    def terminate_instance(self, instance_id: str) -> bool:
        request_data = {"selector": {"ById": [instance_id]}}
        logger.debug("Terminating instance with request data: %s", request_data)
        response_data = self._make_request("instances/terminate", request_data)
        if isinstance(response_data, dict):
            info = response_data.get("terminated", [])
            return len(info) > 0

        return False

    def _make_request(
        self,
        endpoint: str,
        data: Optional[Mapping[str, Any]] = None,
        method: str = "POST",
        **kwargs,
    ) -> Union[Mapping[str, Any], str, Response]:
        headers = {}
        if self.api_key is not None:
            headers["X-API-Key"] = self.api_key

        version = CLOUDRIFT_API_VERSION
        full_url = f"{self.public_api_root}/{endpoint}"

        try:
            response = requests.request(
                method,
                full_url,
                headers=headers,
                json={"version": version, "data": data},
                timeout=120,
                **kwargs,
            )

            if not response.ok:
                response.raise_for_status()
            try:
                response_json = response.json()
                if isinstance(response_json, str):
                    return response_json
                if version is not None and version < response_json["version"]:
                    logger.warning(
                        "The API version %s is lower than the server version %s. ",
                        version,
                        response_json["version"],
                    )
                return response_json["data"]
            except requests.exceptions.JSONDecodeError:
                return response
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (
                requests.codes.forbidden,
                requests.codes.unauthorized,
            ):
                raise BackendInvalidCredentialsError(e.response.text)
            raise



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

