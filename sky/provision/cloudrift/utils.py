"""Utilities for CloudRift cloud provider."""

import os
import uuid
from typing import Any, Dict, List, Optional, Mapping, Union

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
        self.instance_types = CloudRiftInstanceTypesAPI()


class RiftClient:
    def __init__(self,
                 server_address=constants.SERVER_ADDRESS,
                 user_email: Optional[str] = None,
                 user_password: Optional[str] = None,
                 token: Optional[str] = None,
                 api_key: Optional[str] = None):
        self.server_address = server_address
        self.public_api_root = os.path.join(server_address, 'api/v1')
        self.public_api_v2_root = os.path.join(server_address, 'api/v2')
        self.internal_api_root = os.path.join(server_address, 'internal')
        self.token = token
        self.pat = None
        if user_email is not None and user_password is not None:
            if self.token is not None:
                raise ValueError("Cannot provide both user_email and token")
            self.login(user_email, user_password)
        self.api_key = api_key

    def _make_request(self,
                      method: str,
                      url: str,
                      data: Optional[Mapping[str, Any]] = None,
                      version: Optional[str] = None,
                      **kwargs) -> Union[Mapping[str, Any], str, Response]:
        headers = {}
        if self.api_key is not None:
            headers['X-API-Key'] = self.api_key
        if self.token is not None:
            headers['Authorization'] = f"Bearer {self.token}"

        if version is None and (self.public_api_root in url or self.public_api_v2_root in url):
            version = constants.PUBLIC_API_VERSION

        if version is not None and data is not None:
            response = requests.request(method, url, headers=headers,
                                        json={"version": version, "data": data}, **kwargs)
        else:
            if data is not None:
                kwargs['data'] = data
            response = requests.request(method, url, headers=headers, **kwargs)
        if not response.ok:
            raise HTTPError(f"{response.reason}: {response.text}", response=response)
        try:
            response_json = response.json()
            if isinstance(response_json, str):
                return response_json
            if version is not None:
                assert version >= response_json['version']
            return response_json['data']
        except requests.exceptions.JSONDecodeError:
            return response

    def login(self, email: str, password: str):
        resp = self._make_request('post', f'{self.public_api_root}/auth/login',
                                  data={"email": email, "password": password})
        self.token = resp["token"]
        self.pat = resp['pat']
        return resp

    def logout(self):
        self._make_request('post', f'{self.public_api_root}/auth/logout')
        self.token = None

    def me(self):
        return self._make_request('post', f'{self.public_api_root}/auth/me')

    def updates_enabled(self) -> bool:
        result = self._make_request('get', f'{self.internal_api_root}/dev/updates_enabled')
        return bool(result)

    def update_server(self, num_updates: int = 1):
        """Update the server state, e.g., after renting a new instance."""
        result = None
        for _ in range(num_updates):
            result = self._make_request('post', f'{self.internal_api_root}/dev/update_all')
        return result



class CloudRiftInstanceTypesAPI:
    def __init__(self, client: ['RiftClient']):
        self.client = client
        self.api_root = os.path.join(client.public_api_root, 'instance-types')

    def list(self, services: Optional[Sequence[str]] = None, datacenters: Optional[Sequence[str]] = None,
             providers: Optional[Sequence[str]] = None):
        data = {}
        if services is not None or datacenters is not None or providers is not None:
            selector = {}
            if services is not None:
                selector['services'] = services
            if datacenters is not None:
                selector['datacenters'] = datacenters
            if providers is not None:
                selector['providers'] = providers

            if 'providers' in selector:
                if 'datacenters' in selector:
                    raise ValueError("Cannot filter by both providers and datacenters")
                data = {'selector': {'ByServiceAndProvider': selector}}
            else:
                data = {'selector': {'ByServiceAndLocation': selector}}
        return self.client._make_request('post', data=data, url=f'{self.api_root}/list')['instance_types']


class CloudRiftInstancesAPI:
    def __init__(self, client: ['RiftClient']):
        self.client = client
        self.api_root = os.path.join(client.public_api_root, 'instances')

    def rent(self,
             instance: str,
             datacenters: Optional[List[str]] = None,
             with_public_ip: bool = False,
             vm: Optional[dict] = None,
             docker: Optional[dict] = None,
             node_id: Optional[str] = None,
             blocking: bool = True,
             timeout: int = 10,
             reservation_type_id: Optional[str] = None,
             reservation_id: Optional[str] = None,
             team_id: Optional[str] = None,
             bare_metal: Optional[dict] = None,
             wait_for_active: bool = True
             ):
        if self.client.token is None:
            raise RuntimeError("Token is required for renting an executor")

        if node_id is None:
            request = {
                'selector': {'ByInstanceTypeAndLocation': {'instance_type': instance, 'datacenters': datacenters}},
                'with_public_ip': with_public_ip
            }
        else:
            request = {
                'selector': {'ByNodeId': {'node_id': node_id, 'instance_type': instance}},
                'with_public_ip': with_public_ip
            }

        if vm is not None and docker is not None:
            raise ValueError("Only one of vm or docker can be specified")
        elif vm is not None:
            if 'cloudinit_config' not in vm:
                vm['cloudinit_config'] = 'Auto'
            request['config'] = {'VirtualMachine': vm}
        elif bare_metal is not None:
            request['config'] = {'BareMetal': bare_metal}
        else:
            request['config'] = {'Docker': {'image': None}} if docker is None else {'Docker': docker}

        # Handle reservation types
        if reservation_type_id and reservation_id:
            raise ValueError("Only one of reservation_type_id or reservation_id can be specified")
        elif reservation_type_id:
            request["reservation"] = {"TypeID": reservation_type_id}
        elif reservation_id:
            request["reservation"] = {"ID": reservation_id}

        if team_id is not None:
            request["team_id"] = team_id

        instance_id = self.client._make_request('post', url=f'{self.api_root}/rent',
                                                data=request)['instance_ids'][0]
        if not blocking:
            return instance_id

        start = time.time()
        if self.client.updates_enabled():
            while time.time() - start < timeout:
                instance_info = self.info(instance_id=instance_id, team_id=team_id)
                if instance_info and wait_for_active and instance_info['status'] == 'Active':
                    return instance_id
                if instance_info and not wait_for_active and instance_info['status'] != 'Initializing':
                    return instance_id
                # Wait for provider response
                time.sleep(0.2)
                self.client.update_server()
        else:
            while time.time() - start < timeout:
                instance_info = self.info(instance_id=instance_id, team_id=team_id)
                if instance_info and wait_for_active and instance_info['status'] == 'Active':
                    return instance_id
                if instance_info and not wait_for_active and instance_info['status'] != 'Initializing':
                    return instance_id
                time.sleep(1)

        raise TimeoutError(f"Instance {instance_id} hasn't started")

    def _list(self, selector):
        return self.client._make_request('post', url=f"{self.api_root}/list", data={'selector': selector})['instances']

    def info(self, instance_id: str, team_id: Optional[str] = None):
        if team_id is not None:
            selector = {'ByTeamId': {"id": team_id}}
            if instance_id is not None:
                selector['ByTeamId']["instance_ids"] = [instance_id]
            instances = self._list(selector=selector)
        else:
            instances = self._list(selector={'ById': [instance_id]})

        if len(instances) == 0:
            return None

        return instances[0]

    def list(self, all=False, team_id: Optional[str] = None):
        if all:
            status = ['Initializing', 'Active', 'Deactivating', 'Inactive']
        else:
            status = ['Initializing', 'Active', 'Deactivating']
        selector = {'ByStatus': status}

        if team_id is not None:
            selector = {'ByTeamId': {"id": team_id}}

        return self._list(selector=selector)

    def terminate(self, instance_id, blocking=True, timeout=10.0, team_id: Optional[str] = None):
        if team_id is not None:
            selector = {'ByTeamId': {"id": team_id, "instance_ids": [instance_id]}}
        else:
            selector = {'ById': [instance_id]}

        return self._terminate(selector=selector, blocking=blocking, timeout=timeout)

    def terminate_all(self, blocking=True, timeout=10.0):
        return self._terminate(selector={'ByStatus': ['Initializing', 'Active']}, blocking=blocking, timeout=timeout)

    def wait_for_status(self, instance_id: str, target_status: str, timeout: float = 10.0):
        start = time.time()
        while time.time() - start < timeout:
            instance_info = self.info(instance_id=instance_id)
            if instance_info and instance_info['status'] == target_status:
                return instance_info
            if self.client.updates_enabled():
                time.sleep(0.2)
                self.client.update_server()
            else:
                time.sleep(1)
        raise TimeoutError(f"Instance {instance_id} hasn't reached status {target_status}")

    def _terminate(self, selector, blocking=True, timeout=10.0):
        terminated = self.client._make_request('post', url=f"{self.api_root}/terminate", data={'selector': selector})[
            'terminated']
        if not blocking:
            return

        if "ByTeamId" in selector:
            selector['ByTeamId']["instance_ids"] = [t['id'] for t in terminated]
        else:
            selector = {'ById': [t['id'] for t in terminated]}

        start = time.time()
        if self.client.updates_enabled():
            while time.time() - start < timeout:
                self.client.update_server()
                instances = self._list(selector=selector)
                if all(inst['status'] == 'Inactive' for inst in instances):
                    assert len(instances) == len(terminated)
                    return terminated
                # wait for provider response
                time.sleep(0.2)
        else:
            while time.time() - start < timeout:
                instances = self._list(selector=selector)
                if all(inst['status'] == 'Inactive' for inst in instances):
                    assert len(instances) == len(terminated)
                    return terminated
                time.sleep(1)

        raise TimeoutError(f"Some of the instances haven't stopped")

class ProvidersClient:
    def __init__(self, client: ['RiftClient']):
        self.client = client
        self.api_root = os.path.join(client.public_api_root, 'providers')

    def list(self, names: Optional[Sequence[str]] = None):
        if names is None:
            data = {}
        else:
            data = {'selector': {'ByName': names}}
        return self.client._make_request('post', data=data, url=f'{self.api_root}/list')['providers']


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
