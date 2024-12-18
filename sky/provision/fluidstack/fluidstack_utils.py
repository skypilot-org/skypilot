"""FluidStack API client."""

import functools
import json
import os
import time
from typing import Any, Dict, List
import uuid

import requests


def get_key_suffix():
    return str(uuid.uuid4()).replace('-', '')[:8]


ENDPOINT = 'https://platform.fluidstack.io/'
FLUIDSTACK_API_KEY_PATH = '~/.fluidstack/api_key'


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


class FluidstackClient:
    """FluidStack API Client"""

    def __init__(self):
        self.api_key = read_contents(
            os.path.expanduser(FLUIDSTACK_API_KEY_PATH)).strip()

    def get_plans(self):
        response = requests.get(ENDPOINT + 'list_available_configurations',
                                headers={'api-key': self.api_key})
        raise_fluidstack_error(response)
        plans = response.json()
        return plans

    def list_instances(self) -> List[Dict[str, Any]]:
        response = requests.get(
            ENDPOINT + 'instances',
            headers={'api-key': self.api_key},
        )
        raise_fluidstack_error(response)
        instances = response.json()
        return instances

    def create_instance(
        self,
        instance_type: str = '',
        name: str = '',
        region: str = '',
        ssh_pub_key: str = '',
        count: int = 1,
    ) -> List[str]:
        """Launch new instances."""

        plans = self.get_plans()
        regions = self.list_regions()
        gpu_type, gpu_count = instance_type.split('::')
        gpu_count = int(gpu_count)

        plans = [
            plan for plan in plans if plan['gpu_type'] == gpu_type and
            gpu_count in plan['gpu_counts'] and region in plan['regions']
        ]
        if not plans:
            raise FluidstackAPIError(
                f'Plan {instance_type} out of stock in region {region}')

        ssh_key = self.get_or_add_ssh_key(ssh_pub_key)
        default_operating_system = 'ubuntu_22_04_lts_nvidia'
        instance_ids = []
        for _ in range(count):
            body = dict(gpu_type=gpu_type,
                        gpu_count=gpu_count,
                        region=regions[region],
                        operating_system_label=default_operating_system,
                        name=name,
                        ssh_key=ssh_key['name'])

            response = requests.post(ENDPOINT + 'instances',
                                     headers={'api-key': self.api_key},
                                     json=body)
            raise_fluidstack_error(response)
            instance_id = response.json().get('id')
            instance_ids.append(instance_id)
            time.sleep(1)

        return instance_ids

    def list_ssh_keys(self):
        response = requests.get(ENDPOINT + 'ssh_keys',
                                headers={'api-key': self.api_key})
        raise_fluidstack_error(response)
        return response.json()

    def get_or_add_ssh_key(self, ssh_pub_key: str = '') -> Dict[str, str]:
        """Add ssh key if not already added."""
        ssh_keys = self.list_ssh_keys()
        for key in ssh_keys:
            if key['public_key'].strip().split()[:2] == ssh_pub_key.strip(
            ).split()[:2]:
                return {'name': key['name'], 'ssh_key': ssh_pub_key}
        ssh_key_name = 'skypilot-' + get_key_suffix()
        response = requests.post(
            ENDPOINT + 'ssh_keys',
            headers={'api-key': self.api_key},
            json=dict(name=ssh_key_name, public_key=ssh_pub_key),
        )
        raise_fluidstack_error(response)
        return {'name': ssh_key_name, 'ssh_key': ssh_pub_key}

    @functools.lru_cache()
    def list_regions(self):
        plans = self.get_plans()

        def get_regions(plans: List) -> dict:
            """Return a list of regions where the plan is available."""
            regions = {}
            for plan in plans:
                for region in plan.get('regions', []):
                    regions[region] = region
            return regions

        regions = get_regions(plans)
        return regions

    def delete(self, instance_id: str):
        response = requests.delete(ENDPOINT + 'instances/' + instance_id,
                                   headers={'api-key': self.api_key})
        raise_fluidstack_error(response)
        return response.json()

    def stop(self, instance_id: str):
        response = requests.put(ENDPOINT + 'instances/' + instance_id + '/stop',
                                headers={'api-key': self.api_key})
        raise_fluidstack_error(response)
        return response.json()

    def rename(self, instance_id: str, name: str) -> str:
        response = requests.put(
            ENDPOINT + f'instances/{instance_id}/rename',
            headers={'api-key': self.api_key},
            json=dict(new_instance_name=name),
        )
        raise_fluidstack_error(response)
        return response.json()
