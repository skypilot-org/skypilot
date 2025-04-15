"""Hyperstack API client."""

import json
import os
from typing import Any, Dict, List, Optional
import uuid

import requests


def get_key_suffix():
    return str(uuid.uuid4()).replace('-', '')[:8]


ENDPOINT = 'https://infrahub-api.nexgencloud.com/v1'
HYPERSTACK_API_KEY_PATH = '~/.hyperstack/api_key'


def read_contents(path: str) -> str:
    with open(path, mode='r', encoding='utf-8') as f:
        return f.read().strip()


class HyperstackAPIError(Exception):

    def __init__(self, message: str, code: int = 400):
        self.code = code
        super().__init__(message)


def raise_hyperstack_error(response: 'requests.Response') -> None:
    """Raise HyperstackAPIError if appropriate."""
    status_code = response.status_code
    if response.ok:
        return
    try:
        resp_json = response.json()
        status = resp_json.get('status', response.text)
        if status is True:
            return
        message = resp_json.get('message', response.text)
    except (KeyError, json.decoder.JSONDecodeError) as e:
        raise HyperstackAPIError(
            f'Unexpected error. Status code: {status_code} \n {response.text}'
            f'\n {str(e)}',
            code=status_code) from e
    raise HyperstackAPIError(f'{message}', status_code)


class HyperstackClient:
    """Hyperstack API Client"""

    def __init__(self):
        self.api_key = read_contents(
            os.path.expanduser(HYPERSTACK_API_KEY_PATH)).strip()

    def _get_flavors(self) -> List[dict]:
        response = requests.get(f'{ENDPOINT}/core/flavors',
                                headers={'api_key': self.api_key})
        raise_hyperstack_error(response)
        response_json = response.json()
        flavor_groups = response_json['data']
        flavors = [
            flavor for group in flavor_groups for flavor in group['flavors']
        ]

        return flavors

    def list_instances(self) -> List[Dict[str, Any]]:
        response = requests.get(
            f'{ENDPOINT}/core/virtual-machines',
            headers={'api_key': self.api_key},
        )
        raise_hyperstack_error(response)
        response_json = response.json()
        instances = response_json['instances']
        for i in instances:
            i['id'] = str(i['id'])
        return instances

    def _security_rule(self, open_port: int) -> dict:
        return dict(
            direction='ingress',
            protocol='tcp',
            ethertype='IPv4',
            remote_ip_prefix='0.0.0.0/0',
            port_range_min=open_port,
            port_range_max=open_port,
        )

    def create_instance(
        self,
        instance_type: str,
        name: str,
        region: str,
        ssh_pub_key: str,
        ports: Optional[List[int]],
        count: int,
    ) -> List[str]:
        """Launch new instances."""
        flavors = self._get_flavors()

        flavors = [
            flavor for flavor in flavors
            if flavor['name'] == instance_type and region ==
            flavor['region_name'] and flavor['stock_available'] is True
        ]
        if not flavors:
            raise HyperstackAPIError(
                f'Plan {instance_type} out of stock in region {region}')

        default_operating_system = 'Ubuntu Server 22.04 LTS R535 CUDA 12.2'
        default_environment = f'default-{region}'

        ssh_key = self._get_or_add_ssh_key(default_environment, ssh_pub_key)
        security_rules = [self._security_rule(22)]
        if ports:
            for p in ports:
                security_rules.append(self._security_rule(p))
        body = dict(name=name,
                    environment_name=default_environment,
                    image_name=default_operating_system,
                    flavor_name=instance_type,
                    key_name=ssh_key['name'],
                    assign_floating_ip=True,
                    enable_port_randomization=True,
                    security_rules=security_rules,
                    count=count)

        response = requests.post(f'{ENDPOINT}/core/virtual-machines',
                                 headers={'api_key': self.api_key},
                                 json=body)
        raise_hyperstack_error(response)
        instances = response.json().get('instances')
        instance_ids = [str(inst['id']) for inst in instances]

        return instance_ids

    def _list_ssh_keys(self, environment_name: str) -> List[dict]:
        response = requests.get(f'{ENDPOINT}/core/keypairs',
                                headers={'api_key': self.api_key})
        raise_hyperstack_error(response)
        response_json = response.json()
        keys_in_env = [
            kp for kp in response_json['keypairs']
            if kp['environment']['name'] == environment_name
        ]
        return keys_in_env

    def _get_or_add_ssh_key(self,
                            environment_name: str,
                            ssh_pub_key: str = '') -> Dict[str, str]:
        """Add ssh key if not already added."""
        ssh_keys = self._list_ssh_keys(environment_name)
        for key in ssh_keys:
            if key['public_key'].strip().split()[:2] == ssh_pub_key.strip(
            ).split()[:2]:
                return {'name': key['name'], 'ssh_key': ssh_pub_key}
        ssh_key_name = 'skypilot-' + get_key_suffix()
        response = requests.post(
            f'{ENDPOINT}/core/keypairs',
            headers={'api_key': self.api_key},
            json=dict(
                name=ssh_key_name,
                environment_name=environment_name,
                public_key=ssh_pub_key,
            ),
        )
        raise_hyperstack_error(response)
        return {'name': ssh_key_name, 'ssh_key': ssh_pub_key}

    def open_ports(
        self,
        instance_id: str,
        ports: List[int],
    ) -> List[str]:
        """Open ports on instance."""
        rule_ids = []
        for p in ports:
            body = self._security_rule(p)
            response = requests.post(
                f'{ENDPOINT}/core/virtual-machines/{instance_id}/sg-rules',
                headers={'api_key': self.api_key},
                json=body)
            raise_hyperstack_error(response)
            security_rule = response.json().get('security_rule')
            rule_ids.append(str(security_rule['id']))

        return rule_ids

    def delete(self, instance_id: str):
        response = requests.delete(
            f'{ENDPOINT}/core/virtual-machines/{instance_id}',
            headers={'api_key': self.api_key})
        raise_hyperstack_error(response)
        return response.json()

    def stop(self, instance_id: str):
        response = requests.get(
            f'{ENDPOINT}/core/virtual-machines/{instance_id}/stop',
            headers={'api_key': self.api_key})
        raise_hyperstack_error(response)
        return response.json()

    def start(self, instance_id: str):
        response = requests.get(
            f'{ENDPOINT}/core/virtual-machines/{instance_id}/start',
            headers={'api_key': self.api_key})
        raise_hyperstack_error(response)
        return response.json()
