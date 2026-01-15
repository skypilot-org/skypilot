"""Paperspace API client wrapper for SkyPilot."""

import json
import os
import time
import typing
from typing import Any, Dict, List, Optional, Union

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.provision.paperspace import constants
from sky.utils import common_utils

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)

CREDENTIALS_PATH = '~/.paperspace/config.json'
API_ENDPOINT = 'https://api.paperspace.com/v1'
INITIAL_BACKOFF_SECONDS = 10
MAX_BACKOFF_FACTOR = 10
MAX_ATTEMPTS = 6
ADD_KEY_SCRIPT = 'sky-add-key'


class PaperspaceCloudError(Exception):
    pass


def raise_paperspace_api_error(response: 'requests.Response') -> None:
    """Raise PaperspaceCloudError if appropriate."""
    status_code = response.status_code
    if status_code == 200:
        return
    if status_code == 429:
        raise PaperspaceCloudError('Your API requests are being rate limited.')
    try:
        resp_json = response.json()
        code = resp_json.get('code')
        message = resp_json.get('message')
    except json.decoder.JSONDecodeError as e:
        raise PaperspaceCloudError(
            'Response cannot be parsed into JSON. Status '
            f'code: {status_code}; reason: {response.reason}; '
            f'content: {response.text}') from e
    raise PaperspaceCloudError(f'{code}: {message}')


def _try_request_with_backoff(
        method: str,
        url: str,
        headers: Dict[str, str],
        data: Optional[Union[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    backoff = common_utils.Backoff(initial_backoff=INITIAL_BACKOFF_SECONDS,
                                   max_backoff_factor=MAX_BACKOFF_FACTOR)
    for i in range(MAX_ATTEMPTS):
        if method == 'get':
            response = requests.get(url, headers=headers, params=data)
        elif method == 'post':
            response = requests.post(url, headers=headers, data=data)
        elif method == 'put':
            response = requests.put(url, headers=headers, data=data)
        elif method == 'patch':
            response = requests.patch(url, headers=headers, data=data)
        elif method == 'delete':
            response = requests.delete(url, headers=headers)
        else:
            raise ValueError(f'Unsupported requests method: {method}')
        # If rate limited, wait and try again
        if response.status_code == 429 and i != MAX_ATTEMPTS - 1:
            time.sleep(backoff.current_backoff())
            continue
        if response.status_code == 200:
            return response.json()
        raise_paperspace_api_error(response)
    return {}


class PaperspaceCloudClient:
    """Wrapper functions for Paperspace and Machine Core API."""

    def __init__(self) -> None:
        self.credentials = os.path.expanduser(CREDENTIALS_PATH)
        assert os.path.exists(self.credentials), 'Credentials not found'
        with open(self.credentials, 'r', encoding='utf-8') as f:
            self._credentials = json.load(f)
        self.api_key = self._credentials['apiKey']
        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    def list_endpoint(self, endpoint: str,
                      **search_kwargs) -> List[Dict[str, Any]]:
        items = []
        response = _try_request_with_backoff('get',
                                             f'{API_ENDPOINT}/{endpoint}',
                                             headers=self.headers,
                                             data=search_kwargs)
        items.extend(response['items'])
        while response['hasMore']:
            response = _try_request_with_backoff(
                'get',
                f'{API_ENDPOINT}/{endpoint}',
                headers=self.headers,
                data={
                    'after': f'{response["nextPage"]}',
                    **search_kwargs
                })
            items.extend(response['items'])
        return items

    def list_startup_scripts(
            self, name: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.list_endpoint(
            endpoint='startup-scripts',
            name=name,
        )

    def get_sky_key_script(self) -> str:
        return self.list_startup_scripts(ADD_KEY_SCRIPT)[0]['id']

    def set_sky_key_script(self, public_key: str) -> None:
        script = (
            'if ! command -v docker &> /dev/null; then \n'
            'apt-get update \n'
            'apt-get install -y ca-certificates curl \n'
            'install -m 0755 -d /etc/apt/keyrings \n'
            'curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \n'  # pylint: disable=line-too-long
            'chmod a+r /etc/apt/keyrings/docker.asc \n'
            'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \\\n'  # pylint: disable=line-too-long
            '$(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \\\n'
            'tee /etc/apt/sources.list.d/docker.list > /dev/null \n'
            'apt-get update \n'
            'apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \n'  # pylint: disable=line-too-long
            'fi \n'
            # TODO(tian): Maybe remove this as well since we are now adding
            # users to docker group in the DockerInitializer. Need to test.
            'usermod -aG docker paperspace \n'
            f'echo "{public_key}" >> /home/paperspace/.ssh/authorized_keys \n')
        try:
            script_id = self.get_sky_key_script()
            _try_request_with_backoff(
                'put',
                f'{API_ENDPOINT}/startup-scripts/{script_id}',
                headers=self.headers,
                data=json.dumps({
                    'name': ADD_KEY_SCRIPT + f'-{common_utils.get_user_hash()}',
                    'script': script,
                    'isRunOnce': True,
                    'isEnabled': True
                }))
        except IndexError:
            _try_request_with_backoff('post',
                                      f'{API_ENDPOINT}/startup-scripts',
                                      headers=self.headers,
                                      data=json.dumps({
                                          'name': ADD_KEY_SCRIPT,
                                          'script': script,
                                          'isRunOnce': True,
                                      }))

    def get_network(self, network_name: str) -> Dict[str, Any]:
        return self.list_endpoint(
            endpoint='private-networks',
            name=network_name,
        )[0]

    def setup_network(self, cluster_name: str, region: str) -> Dict[str, Any]:
        """Attempts to find an existing network with a name matching to
        the cluster name otherwise create a new network.
        """
        try:
            network = self.get_network(network_name=cluster_name,)
        except IndexError:
            network = _try_request_with_backoff(
                'post',
                f'{API_ENDPOINT}/private-networks',
                headers=self.headers,
                data=json.dumps({
                    'name': cluster_name,
                    'region': region,
                }))
        return network

    def delete_network(self, network_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            'delete',
            f'{API_ENDPOINT}/private-networks/{network_id}',
            headers=self.headers,
        )

    def list_instances(self) -> List[Dict[str, Any]]:
        return self.list_endpoint(endpoint='machines')

    def launch(self, name: str, instance_type: str, network_id: str,
               region: str, disk_size: int) -> Dict[str, Any]:
        response = _try_request_with_backoff(
            'post',
            f'{API_ENDPOINT}/machines',
            headers=self.headers,
            data=json.dumps({
                'name': name,
                'machineType': instance_type,
                'networkId': network_id,
                'region': region,
                'diskSize': disk_size,
                'templateId':
                    constants.INSTANCE_TO_TEMPLATEID.get(instance_type),
                'publicIpType': 'dynamic',
                'startupScriptId': self.get_sky_key_script(),
                'enableNvlink': instance_type in constants.NVLINK_INSTANCES,
                'startOnCreate': True,
            }))
        return response

    def start(self, instance_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            'patch',
            f'{API_ENDPOINT}/machines/{instance_id}/start',
            headers={'Authorization': f'Bearer {self.api_key}'},
        )

    def stop(self, instance_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            'patch',
            f'{API_ENDPOINT}/machines/{instance_id}/stop',
            headers={'Authorization': f'Bearer {self.api_key}'},
        )

    def remove(self, instance_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            'delete',
            f'{API_ENDPOINT}/machines/{instance_id}',
            headers=self.headers,
        )

    def rename(self, instance_id: str, name: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            'put',
            f'{API_ENDPOINT}/machines/{instance_id}',
            headers=self.headers,
            data=json.dumps({
                'name': name,
            }))
