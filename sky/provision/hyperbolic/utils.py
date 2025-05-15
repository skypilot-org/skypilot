"""Hyperbolic API utilities."""
import os
from typing import Any, Dict, Optional

import requests

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# TODO: replace this with prod URL
BASE_URL = 'http://localhost:8080'


def _make_request(method: str,
                  endpoint: str,
                  data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Make a request to the Hyperbolic API."""
    url = f'{BASE_URL}{endpoint}'
    token = os.environ.get('HYPERBOLIC_API_KEY')
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        if method == 'GET':
            response = requests.get(url, headers=headers)
        elif method == 'POST':
            response = requests.post(url, json=data, headers=headers)
        elif method == 'DELETE':
            response = requests.delete(url, headers=headers)
        else:
            raise ValueError(f'Unsupported HTTP method: {method}')

        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f'API request failed: {e}')
        raise RuntimeError(
            f'Failed to communicate with Hyperbolic API: {e}') from e


def launch_instance(gpu_model: str,
                    gpu_count: int,
                    name: str,
                    use_cheapest: bool = True) -> str:
    """Launch a new instance with specified GPU configuration."""
    data = {'gpuModel': gpu_model, 'gpuCount': str(gpu_count), 'name': name}
    if use_cheapest:
        endpoint = '/v2/marketplace/instances/create/cheapest'
    else:
        endpoint = '/v1/marketplace/instances/create'
    response = _make_request('POST', endpoint, data)
    return response.get('instanceId', response.get('id'))


def list_instances() -> Dict[str, Dict[str, Any]]:
    """List all instances."""
    response = _make_request('GET', '/v1/marketplace/instances')
    instances = {}
    for instance in response.get('instances', []):
        instances[instance['id']] = {
            'status': instance['status'],
            'name': instance['name'],
            'internal_ip': instance.get('internalIp'),
            'external_ip': instance.get('externalIp'),
            'ssh_port': instance.get('sshPort', 22)
        }
    return instances


def terminate_instance(instance_id: str) -> None:
    """Terminate an instance."""
    data = {'id': instance_id}
    _make_request('POST', '/v1/marketplace/instances/terminate', data)


def get_instance(instance_id: str) -> Dict[str, Any]:
    """Get details of a specific instance."""
    response = _make_request('GET', f'/v1/marketplace/instances/{instance_id}')
    return {
        'status': response['status'],
        'name': response['name'],
        'internal_ip': response.get('internalIp'),
        'external_ip': response.get('externalIp'),
        'ssh_port': response.get('sshPort', 22)
    }
