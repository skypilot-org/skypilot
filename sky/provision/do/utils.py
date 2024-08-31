"""Digital Ocean Client for SkyPilot."""

import json
import os
import pydo
import uuid
from typing import Any, Dict, List, Optional, Union

from urllib.parse import urlparse
from urllib.parse import parse_qs

# Would be nice to not need azure branded imports.
from azure.core.exceptions import HttpResponseError

from sky import sky_logging
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

POSSIBLE_CREDENTIALS_PATHS = [
    os.path.expanduser('~/Library/Application Support/doctl/config.yaml'), # OS X
    os.path.expanduser(os.path.join(os.getenv('XDG_CONFIG_HOME', '~/.config/'), 'doctl/config.yaml')), # Linux
]
INITIAL_BACKOFF_SECONDS = 10
MAX_BACKOFF_FACTOR = 10
MAX_ATTEMPTS = 6
POLL_INTERVAL = 5
SSH_KEY_NAME = f'sky-key-{common_utils.get_user_hash()}'
IMAGE = ''

_client = None
_ssh_key_id = None


class DigitalOceanError(Exception):
    pass


def _init_client():
    global _client
    credential_path = None
    for path in POSSIBLE_CREDENTIALS_PATHS:
        if os.path.exists(path):
            credential_path = path
    if credential_path is None:
        raise DOCloudError(f'no credentials file found from the following paths {POSSIBLE_CREDENTIALS_PATHS}')

    try:
        api_token = common_utils.read_yaml(credential_path)['auth-contexts']['skypilot']
    except KeyError as e:
        raise KeyError('No valid token found for skypilot. Try setting your auth token to the skypilot context, `doctl auth init --context skypilot`')
    
    _client = pydo.Client(token=api_token)
    

def client():
    global _client
    if _client is None:
        _init_client()
    return _client


def ssh_key_id():
    global _ssh_key_id
    if _ssh_key_id is None:
        page = 1
        paginated = True
        while paginated:
            try:
                resp = client().ssh_keys.list(per_page=50, page=page)
                for ssh_key in resp["ssh_keys"]:
                    if ssh_key["name"] == SSH_KEY_NAME:
                        _ssh_key_id = ssh_key
                        return _ssh_key_id
            except HttpResponseError as err:
                DigitalOceanError(
                    "Error: {0} {1}: {2}".format(
                        err.status_code, err.reason, err.error.message
                    )
                )

            pages = resp.links.pages
            if "next" in pages.keys():
                # Having to parse the URL to find the next page is not very friendly.
                parsed_url = urlparse(pages["next"])
                page = parse_qs(parsed_url.query)["page"][0]
            else:
                paginated = False
    return _ssh_key_id


def _wait_for_action(id: str, wait: int = POLL_INTERVAL):
    """Waits for action to complete before throwing an error.

    Args:
        id (str): action id
        wait (int, optional): amount of time to wait before polling status of action
    """
    status = "in-progress"
    while status == "in-progress":
        try:
            resp = client().actions.get(id)
        except HttpResponseError as err:
            raise DigitalOceanError(
                "Error: {0} {1}: {2}".format(
                    err.status_code, err.reason, err.error.message
                )
            )
        else:
            status = resp["action"]["status"]
            if status == "in-progress":
                sleep(wait)
            elif status == "errored":
                raise Exception(
                    "{0} action {1} {2}".format(
                        resp["action"]["type"], resp["action"]["id"], status
                    )
                )



def _create_volume(request: Dict[str, Any]) -> Dict[str, Any]:
    try:
        resp = client().volumes.create(body=req)
        volume = resp["volume"]
    except HttpResponseError as err:
        DigitalOceanError(
            "Error: {0} {1}: {2}".format(
                err.status_code, err.reason, err.error.message
            )
        )
    else:
        return volume

def _create_droplet(request: Dict[str, Any]) -> Dict[str, Any]:
    try:
        resp = client().droplets.create(body=req)
        droplet_id = resp["droplet"]["id"]
        wait_for_action(resp["links"]["actions"][0]["id"])

        get_resp = client().droplets.get(droplet_id)
        droplet = get_resp["droplet"]
        ip_address = ""
        # Would be nice if we could surface the IP address somehow.
        # For example godo has the PublicIPv4 method:
        # https://github.com/digitalocean/godo/blob/a084002940af6a9b818e3c8fb31a4920356fbb75/droplets.go#L66-L79
        for net in droplet["networks"]["v4"]:
            if net["type"] == "public":
                ip_address = net["ip_address"]
    except HttpResponseError as err:
        DigitalOceanError(
            "Error: {0} {1}: {2}".format(
                err.status_code, err.reason, err.error.message
            )
        )
        return droplet


def create_instance(region: str, instance_name: str,
        config: common.ProvisionConfig) -> Dict[str, Any]:
    """Creates a instance and mounts the requested block storage

    Args:
        region (str): instance region
        instance_name (str): name of instance
        config (common.ProvisionConfig): provisioner configuration

    Returns:
        Dict[str, Any]: Dict of instance metadata
    """

    instance_request = {
        'name': instance_name,
        'region': region,
        'size': config.node_config['InstanceType'],
        'image': IMAGE,
        'ssh_keys': [ssh_key_id()['fingerprint']],
    }
    instance = _create_droplet(instance_request)

    volume_request = {
        "size_gigabytes": config.node_config['DiskSize'],
        "name": instance_name,
        "region": region,
        "filesystem_type": "ext4",
    }
    volume = _create_volume(volume_request)

    attach_request = {
        'type' : 'attach',
        'droplet_id' : droplet['id']
    }
    try:
        action_response = client().volume_actions.post_by_id(
            volume['id'], attach_request
        )
        _wait_for_action(action_response['action']['id'])
    except HttpResponseError as err:
        DigitalOceanError(
            "Error: {0} {1}: {2}".format(
                err.status_code, err.reason, err.error.message
            )
        )
    logger.debug(f'{instance_name} created')
    

