"""DigitalOcean API client wrapper for SkyPilot.

Example usage of `pydo` client library was mostly taken from here:
https://github.com/digitalocean/pydo/blob/main/examples/poc_droplets_volumes_sshkeys.py
"""

import copy
import os
from typing import Any, Dict, List, Optional
import urllib
import uuid

from sky import sky_logging
from sky.adaptors import do
from sky.provision import common
from sky.provision import constants as provision_constants
from sky.provision.do import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import yaml_utils

logger = sky_logging.init_logger(__name__)

POSSIBLE_CREDENTIALS_PATHS = [
    os.path.expanduser(
        '~/Library/Application Support/doctl/config.yaml'),  # OS X
    os.path.expanduser(
        os.path.join(os.getenv('XDG_CONFIG_HOME', '~/.config/'),
                     'doctl/config.yaml')),  # Linux
]
INITIAL_BACKOFF_SECONDS = 10
MAX_BACKOFF_FACTOR = 10
MAX_ATTEMPTS = 6
SSH_KEY_NAME_ON_DO_PREFIX = 'sky-key-'

_client = None
_ssh_key_id = None


class DigitalOceanError(Exception):
    pass


@annotations.lru_cache(scope='request')
def get_credentials_path():
    credentials_path = None
    credentials_found = 0
    for path in POSSIBLE_CREDENTIALS_PATHS:
        if os.path.exists(path):
            logger.debug(f'Digital Ocean credential path found at {path}')
            credentials_path = path
            credentials_found += 1
    if credentials_found > 1:
        logger.debug('More than 1 credential file found')
    return credentials_path


def _init_client():
    global _client
    assert _client is None
    # attempt default context
    if get_credentials_path() is None:
        raise DigitalOceanError(
            'No credentials found, please run `doctl auth init`')
    credentials = yaml_utils.read_yaml(get_credentials_path())
    default_token = credentials.get('access-token', None)
    if default_token is not None:
        try:
            test_client = do.pydo.Client(token=default_token)
            test_client.droplets.list()
            logger.debug('Trying `default` context')
            _client = test_client
            return _client
        except do.exceptions().HttpResponseError:
            pass

    auth_contexts = credentials.get('auth-contexts', None)
    if auth_contexts is not None:
        for context, api_token in auth_contexts.items():
            try:
                test_client = do.pydo.Client(token=api_token)
                test_client.droplets.list()
                logger.debug(f'Using "{context}" context')
                _client = test_client
                break
            except do.exceptions().HttpResponseError:
                continue
        else:
            raise DigitalOceanError(
                'no valid api tokens found try '
                'setting a new API token with `doctl auth init`')
    return _client


def client():
    global _client
    if _client is None:
        _client = _init_client()
    return _client


def ssh_key_id(public_key: str):
    global _ssh_key_id
    if _ssh_key_id is None:
        page = 1
        paginated = True
        while paginated:
            try:
                resp = client().ssh_keys.list(per_page=50, page=page)
                for ssh_key in resp['ssh_keys']:
                    if ssh_key['public_key'] == public_key:
                        _ssh_key_id = ssh_key
                        return _ssh_key_id
            except do.exceptions().HttpResponseError as err:
                raise DigitalOceanError(
                    f'Error: {err.status_code} {err.reason}: '
                    f'{err.error.message}') from err

            pages = resp['links']
            if 'pages' in pages and 'next' in pages['pages']:
                pages = pages['pages']
                parsed_url = urllib.parse.urlparse(pages['next'])
                page = int(urllib.parse.parse_qs(parsed_url.query)['page'][0])
            else:
                paginated = False

        request = {
            'public_key': public_key,
            'name': SSH_KEY_NAME_ON_DO_PREFIX + common_utils.get_user_hash(),
        }
        _ssh_key_id = client().ssh_keys.create(body=request)['ssh_key']
    return _ssh_key_id


def _create_volume(request: Dict[str, Any]) -> Dict[str, Any]:
    try:
        resp = client().volumes.create(body=request)
        volume = resp['volume']
    except do.exceptions().HttpResponseError as err:
        raise DigitalOceanError(
            f'Error: {err.status_code} {err.reason}: {err.error.message}'
        ) from err
    else:
        return volume


def _create_droplet(request: Dict[str, Any]) -> Dict[str, Any]:
    try:
        resp = client().droplets.create(body=request)
        droplet_id = resp['droplet']['id']

        get_resp = client().droplets.get(droplet_id)
        droplet = get_resp['droplet']
    except do.exceptions().HttpResponseError as err:
        raise DigitalOceanError(
            f'Error: {err.status_code} {err.reason}: {err.error.message}'
        ) from err
    return droplet


def create_instance(region: str, cluster_name_on_cloud: str, instance_type: str,
                    config: common.ProvisionConfig) -> Dict[str, Any]:
    """Creates a instance and mounts the requested block storage

    Args:
        region (str): instance region
        instance_name (str): name of instance
        config (common.ProvisionConfig): provisioner configuration

    Returns:
        Dict[str, Any]: instance metadata
    """
    # sort tags by key to support deterministic unit test stubbing
    tags = dict(sorted(copy.deepcopy(config.tags).items()))
    tags = {
        'Name': cluster_name_on_cloud,
        provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
        provision_constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud,
        **tags
    }
    tags = [f'{key}:{value}' for key, value in tags.items()]
    default_image = constants.GPU_IMAGES.get(
        config.node_config['InstanceType'],
        'gpu-h100x1-base',
    )
    image_id = config.node_config['ImageId']
    image_id = image_id if image_id is not None else default_image
    instance_name = (f'{cluster_name_on_cloud}-'
                     f'{uuid.uuid4().hex[:4]}-{instance_type}')
    instance_request = {
        'name': instance_name,
        'region': region,
        'size': config.node_config['InstanceType'],
        'image': image_id,
        'ssh_keys': [
            ssh_key_id(
                config.authentication_config['ssh_public_key'])['fingerprint']
        ],
        'tags': tags,
    }
    instance = _create_droplet(instance_request)

    volume_request = {
        'size_gigabytes': config.node_config['DiskSize'],
        'name': instance_name,
        'region': region,
        'filesystem_type': 'ext4',
        'tags': tags
    }
    volume = _create_volume(volume_request)

    attach_request = {'type': 'attach', 'droplet_id': instance['id']}
    try:
        client().volume_actions.post_by_id(volume['id'], attach_request)
    except do.exceptions().HttpResponseError as err:
        raise DigitalOceanError(
            f'Error: {err.status_code} {err.reason}: {err.error.message}'
        ) from err
    logger.debug(f'{instance_name} created')
    return instance


def start_instance(instance: Dict[str, Any]):
    try:
        client().droplet_actions.post(droplet_id=instance['id'],
                                      body={'type': 'power_on'})
    except do.exceptions().HttpResponseError as err:
        raise DigitalOceanError(
            f'Error: {err.status_code} {err.reason}: {err.error.message}'
        ) from err


def stop_instance(instance: Dict[str, Any]):
    try:
        client().droplet_actions.post(
            droplet_id=instance['id'],
            body={'type': 'shutdown'},
        )
    except do.exceptions().HttpResponseError as err:
        raise DigitalOceanError(
            f'Error: {err.status_code} {err.reason}: {err.error.message}'
        ) from err


def down_instance(instance: Dict[str, Any]):
    # We use dangerous destroy to atomically delete
    # block storage and instance for autodown
    try:
        client().droplets.destroy_with_associated_resources_dangerous(
            droplet_id=instance['id'], x_dangerous=True)
    except do.exceptions().HttpResponseError as err:
        if 'a destroy is already in progress' in err.error.message:
            return
        raise DigitalOceanError(
            f'Error: {err.status_code} {err.reason}: {err.error.message}'
        ) from err


def rename_instance(instance: Dict[str, Any], new_name: str):
    try:
        client().droplet_actions.rename(droplet=instance['id'],
                                        body={
                                            'type': 'rename',
                                            'name': new_name
                                        })
    except do.exceptions().HttpResponseError as err:
        raise DigitalOceanError(
            f'Error: {err.status_code} {err.reason}: {err.error.message}'
        ) from err


def filter_instances(
        cluster_name_on_cloud: str,
        status_filters: Optional[List[str]] = None) -> Dict[str, Any]:
    """Returns Dict mapping instance name
    to instance metadata filtered by status
    """

    filtered_instances: Dict[str, Any] = {}
    page = 1
    paginated = True
    while paginated:
        try:
            resp = client().droplets.list(
                tag_name=f'{provision_constants.TAG_SKYPILOT_CLUSTER_NAME}:'
                f'{cluster_name_on_cloud}',
                per_page=50,
                page=page)
            for instance in resp['droplets']:
                if status_filters is None or instance[
                        'status'] in status_filters:
                    filtered_instances[instance['name']] = instance
        except do.exceptions().HttpResponseError as err:
            raise DigitalOceanError(
                f'Error: {err.status_code} {err.reason}: {err.error.message}'
            ) from err

        pages = resp['links']
        if 'pages' in pages and 'next' in pages['pages']:
            pages = pages['pages']
            parsed_url = urllib.parse.urlparse(pages['next'])
            page = int(urllib.parse.parse_qs(parsed_url.query)['page'][0])
        else:
            paginated = False
    return filtered_instances
