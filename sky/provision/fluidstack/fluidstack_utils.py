"""FluidStack API client."""

import functools
import json
import os
from typing import Any, Dict, List, Optional
import uuid

import requests

from sky.clouds.service_catalog.data_fetchers import fetch_fluidstack


def get_key_suffix():
    return str(uuid.uuid4()).replace('-', '')[:8]


ENDPOINT = 'https://api.fluidstack.io/v1/'
FLUIDSTACK_API_KEY_PATH = '~/.fluidstack/api_key'
FLUIDSTACK_API_TOKEN_PATH = '~/.fluidstack/api_token'


def read_contents(path: str) -> str:
    with open(path, mode='r', encoding='utf-8') as f:
        return f.read().strip()


class FluidstackAPIError(Exception):

    def __init__(self, message: str, code: int = 400):
        self.code = code
        super().__init__(message)


def raise_fluidstack_error(response: requests.Response) -> None:
    """Raise FluidstackAPIError if appropriate."""
    status_code = response.status_code
    if response.ok:
        return
    try:
        resp_json = response.json()
        message = resp_json.get('error', response.text)
    except (KeyError, json.decoder.JSONDecodeError) as e:
        raise FluidstackAPIError(
            f'Unexpected error. Status code: {status_code} \n {response.text}'
            f'\n {str(e)}',
            code=status_code) from e
    raise FluidstackAPIError(f'{message}', status_code)


@functools.lru_cache()
def with_nvidia_drivers(region: str):
    if region in ['norway_4_eu', 'generic_1_canada']:
        return False
    client = FluidstackClient()
    plans = client.get_plans()
    for plan in plans:
        if region in [r['id'] for r in plan['regions']]:
            if 'Ubuntu 20.04 LTS (Nvidia)' in plan['os_options']:
                return True
    return False


class FluidstackClient:
    """FluidStack API Client"""

    def __init__(self):
        self.api_key = read_contents(
            os.path.expanduser(FLUIDSTACK_API_KEY_PATH))
        self.api_token = read_contents(
            os.path.expanduser(FLUIDSTACK_API_TOKEN_PATH))

    def get_plans(self):
        response = requests.get(ENDPOINT + 'plans')
        raise_fluidstack_error(response)
        plans = response.json()
        plans = [
            plan for plan in plans
            if plan['minimum_commitment'] == 'hourly' and plan['type'] in
            ['preconfigured', 'custom'] and plan['gpu_type'] != 'NO GPU'
        ]
        return plans

    def list_instances(
            self,
            tag_filters: Optional[Dict[str,
                                       str]] = None) -> List[Dict[str, Any]]:
        response = requests.get(
            ENDPOINT + 'servers',
            auth=(self.api_key, self.api_token),
        )
        raise_fluidstack_error(response)
        instances = response.json()
        filtered_instances = []

        for instance in instances:
            if isinstance(instance['tags'], str):
                instance['tags'] = json.loads(instance['tags'])
            if not instance['tags']:
                instance['tags'] = {}
            if tag_filters:
                for key in tag_filters:
                    if instance['tags'].get(key, None) != tag_filters[key]:
                        break
                else:
                    filtered_instances.append(instance)
            else:
                filtered_instances.append(instance)

        return filtered_instances

    def create_instance(
        self,
        instance_type: str = '',
        hostname: str = '',
        region: str = '',
        ssh_pub_key: str = '',
        count: int = 1,
    ) -> List[str]:
        """Launch new instances."""

        config = {}
        plans = self.get_plans()
        if 'custom' in instance_type:
            values = instance_type.split(':')
            index = values[1]
            instance_type = values[2]
            config = fetch_fluidstack.CUSTOM_PLANS_CONFIG[int(index)]
            plan = [plan for plan in plans if plan['plan_id'] == instance_type
                   ][0]
            config['gpu_model'] = plan['gpu_type']

        regions = self.list_regions()
        plans = [
            plan for plan in plans if plan['plan_id'] == instance_type and
            region in [r['id'] for r in plan['regions']]
        ]
        if not plans:
            raise FluidstackAPIError(
                f'Plan {instance_type} out of stock in region {region}')

        ssh_key = self.get_or_add_ssh_key(ssh_pub_key)
        os_id = 'Ubuntu 20.04 LTS'
        body = dict(plan=None if config else instance_type,
                    region=regions[region],
                    os=os_id,
                    hostname=hostname,
                    ssh_keys=[ssh_key['id']],
                    multiplicity=count,
                    config=config)

        response = requests.post(ENDPOINT + 'server',
                                 auth=(self.api_key, self.api_token),
                                 json=body)
        raise_fluidstack_error(response)
        instance_ids = response.json().get('multiple')
        assert all(id is not None for id in instance_ids), instance_ids
        return instance_ids

    def list_ssh_keys(self):
        response = requests.get(ENDPOINT + 'ssh',
                                auth=(self.api_key, self.api_token))
        raise_fluidstack_error(response)
        return response.json()

    def get_or_add_ssh_key(self, ssh_pub_key: str = '') -> Dict[str, str]:
        """Add ssh key if not already added."""
        ssh_keys = self.list_ssh_keys()
        for key in ssh_keys:
            if key['public_key'].strip() == ssh_pub_key.strip():
                return {
                    'id': key['id'],
                    'name': key['name'],
                    'ssh_key': ssh_pub_key
                }
        ssh_key_name = 'skypilot-' + get_key_suffix()
        response = requests.post(
            ENDPOINT + 'ssh',
            auth=(self.api_key, self.api_token),
            json=dict(name=ssh_key_name, public_key=ssh_pub_key),
        )
        raise_fluidstack_error(response)
        key_id = response.json()['id']
        return {'id': key_id, 'name': ssh_key_name, 'ssh_key': ssh_pub_key}

    @functools.lru_cache()
    def list_regions(self):
        response = requests.get(ENDPOINT + 'plans')
        raise_fluidstack_error(response)
        plans = response.json()
        plans = [
            plan for plan in plans
            if plan['minimum_commitment'] == 'hourly' and plan['type'] in
            ['preconfigured', 'custom'] and plan['gpu_type'] != 'NO GPU'
        ]

        def get_regions(plans: List) -> dict:
            """Return a list of regions where the plan is available."""
            regions = {}
            for plan in plans:
                for region in plan.get('regions', []):
                    regions[region['id']] = region['id']
            return regions

        regions = get_regions(plans)
        return regions

    def delete(self, instance_id: str):
        response = requests.delete(ENDPOINT + 'server/' + instance_id,
                                   auth=(self.api_key, self.api_token))
        raise_fluidstack_error(response)
        return response.json()

    def stop(self, instance_id: str):
        response = requests.put(ENDPOINT + 'server/' + instance_id + '/stop',
                                auth=(self.api_key, self.api_token))
        raise_fluidstack_error(response)
        return response.json()

    def restart(self, instance_id: str):
        response = requests.post(ENDPOINT + 'server/' + instance_id + '/reboot',
                                 auth=(self.api_key, self.api_token))
        raise_fluidstack_error(response)
        return response.json()

    def info(self, instance_id: str):
        response = requests.get(ENDPOINT + f'server/{instance_id}',
                                auth=(self.api_key, self.api_token))
        raise_fluidstack_error(response)
        return response.json()

    def status(self, instance_id: str):
        response = self.info(instance_id)
        return response['status']

    def add_tags(self, instance_id: str, tags: Dict[str, str]) -> str:
        response = requests.patch(
            ENDPOINT + f'server/{instance_id}/tag',
            auth=(self.api_key, self.api_token),
            json=dict(tags=json.dumps(tags)),
        )
        raise_fluidstack_error(response)
        return response.json()

    def rename(self, instance_id: str, hostname: str) -> str:
        response = requests.patch(
            ENDPOINT + f'server/{instance_id}/rename',
            auth=(self.api_key, self.api_token),
            json=dict(name=hostname),
        )
        raise_fluidstack_error(response)
        return response.json()
