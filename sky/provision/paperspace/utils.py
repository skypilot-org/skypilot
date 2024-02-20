"""Paperspace API client wrapper for SkyPilot."""
import json
import requests
import os
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Union

import sky.provision.paperspace.constants as constants
from sky import sky_logging
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

CREDENTIALS_PATH = "~/.paperspace/config.json"
API_ENDPOINT = "https://api.paperspace.com/v1"
INITIAL_BACKOFF_SECONDS = 10
MAX_BACKOFF_FACTOR = 10
MAX_ATTEMPTS = 6
ADD_KEY_SCRIPT = "sky-add-key"

class PaperspaceCloudError(Exception):
    pass


def raise_paperspace_api_error(response: requests.Response) -> None:
    """Raise LambdaCloudError if appropriate."""
    status_code = response.status_code
    if status_code == 200:
        return
    if status_code == 429:
        # https://docs.lambdalabs.com/cloud/rate-limiting/
        raise PaperspaceCloudError("Your API requests are being rate limited.")
    try:
        resp_json = response.json()
        code = resp_json.get("code")
        message = resp_json.get("message")
    except json.decoder.JSONDecodeError as e:
        raise PaperspaceCloudError(
            "Response cannot be parsed into JSON. Status "
            f"code: {status_code}; reason: {response.reason}; "
            f"content: {response.text}"
        ) from e
    raise PaperspaceCloudError(f"{code}: {message}")


def _try_request_with_backoff(
    method: str, url: str, headers: Dict[str, str], data: Optional[str] = None
):
    backoff = common_utils.Backoff(
        initial_backoff=INITIAL_BACKOFF_SECONDS, max_backoff_factor=MAX_BACKOFF_FACTOR
    )
    for i in range(MAX_ATTEMPTS):
        if method == "get":
            response = requests.get(url, headers=headers, params=data)
        elif method == "post":
            response = requests.post(url, headers=headers, data=data)
        elif method == "put":
            response = requests.put(url, headers=headers, data=data)
        elif method == "patch":
            response = requests.patch(url, headers=headers, data=data)
        elif method == "delete":
            response = requests.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported requests method: {method}")
        # If rate limited, wait and try again
        if response.status_code == 429 and i != MAX_ATTEMPTS - 1:
            time.sleep(backoff.current_backoff())
            continue
        if response.status_code == 200:
            return response
        raise_paperspace_api_error(response)


class PaperspaceCloudClient:
    """Wrapper functions for Paperspace and Machine Core API."""

    def __init__(self) -> None:
        self.credentials = os.path.expanduser(CREDENTIALS_PATH)
        assert os.path.exists(self.credentials), "Credentials not found"
        with open(self.credentials, "r") as f:
            self._credentials = json.load(f)
        self.api_key = self._credentials["apiKey"]
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def list_endpoint(self, endpoint: str, **search_kwargs) -> List[Dict[str, Any]]:
        items = []
        response = _try_request_with_backoff(
            "get",
            f"{API_ENDPOINT}/{endpoint}",
            headers=self.headers,
            data={**search_kwargs}
        ).json()
        items.extend(response["items"])
        while response["hasMore"]:
            reponse = _try_request_with_backoff(
                "get",
                f"{API_ENDPOINT}/{endpoint}",
                headers=self.headers,
                data=json.dumps({ 
                    "after" : f"{response['nextPage']}",
                    **search_kwargs
                })
            ).json()
            items.extend(response["items"])
        return items
    
    def list_startup_scripts(self, name: str=None) -> List[Dict[str, Any]]:
        return self.list_endpoint(
                endpoint='startup-scripts',
                name=name,
            )
        
    def get_sky_key_script(self) -> str:
        return self.list_startup_scripts(ADD_KEY_SCRIPT)[0]['id']

    def set_sky_key_script(self, public_key: str) -> None:
        script = f"echo '{public_key}' >> /home/paperspace/.ssh/authorized_keys"
        try:
            script_id = self.get_sky_key_script()
            _try_request_with_backoff(
                "put",
                f"{API_ENDPOINT}/startup-scripts/{script_id}",
                headers=self.headers,
                data=json.dumps({ 
                    "name" : ADD_KEY_SCRIPT,
                    "script" : script,
                    "isRunOnce" : True,
                    "isEnabled" : True
                })
            ).json()
        except IndexError:
            _try_request_with_backoff(
                "post",
                f"{API_ENDPOINT}/startup-scripts",
                headers=self.headers,
                data=json.dumps({ 
                    "name" : ADD_KEY_SCRIPT,
                    "script" : script,
                    "isRunOnce" : True,
                })
            ).json()
    
    def get_network(self, network_name: str) -> Dict[str, Any]:
        return self.list_endpoint(
            endpoint='private-networks',
            name=network_name,
        )[0]

    def setup_network(self, cluster_name: str, region: str) -> Dict[str, Any]:
        """
        Attempts to find an existing network with a name matching to 
        the cluster name otherwise create a new network.
        """
        try:
            network = self.get_network(
                network_name=cluster_name,
            )
        except IndexError:
            network = _try_request_with_backoff(
                "post",
                f"{API_ENDPOINT}/private-networks",
                headers=self.headers,
                data=json.dumps({ 
                    "name" : cluster_name,
                    "region" : region,
                })
            ).json()
        return network

    def delete_network(self, network_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            "delete",
            f"{API_ENDPOINT}/private-networks/{network_id}",
            headers=self.headers,
        ).json()

    def list_instances(self) -> List[Dict[str, Any]]:
        return self.list_endpoint(endpoint="machines")
    
    def launch(self, name: str, instance_type: str, network_id: str, region: str, disk_size: int) -> Dict[str, Any]:
        response = _try_request_with_backoff(
            "post",
            f"{API_ENDPOINT}/machines",
            headers=self.headers,
            data=json.dumps({
                'name': name,
                'machineType': instance_type,
                'networkId': network_id,
                'region': region,
                'diskSize': disk_size,
                'templateId': constants.INSTANCE_TO_TEMPLATEID.get(instance_type),
                'publicIpType': 'dynamic',
                'startupScriptId': self.get_sky_key_script(),
                'enableNvlink': instance_type in constants.NVLINK_INSTANCES,
                'startOnCreate': True,
            })
        ).json()
        return response['data']['id']

    def start(self, instance_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            "patch",
            f"{API_ENDPOINT}/machines/{instance_id}/start",
            headers={"Authorization": f"Bearer {self.api_key}"},
        ).json(),

    def stop(self, instance_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
            "patch",
            f"{API_ENDPOINT}/machines/{instance_id}/stop",
            headers={"Authorization": f"Bearer {self.api_key}"},
        ).json(),
    
    def remove(self, instance_id: str) -> Dict[str, Any]:
        return _try_request_with_backoff(
                "delete",
                f"{API_ENDPOINT}/machines/{instance_id}",
                headers=self.headers,
        ).json()




