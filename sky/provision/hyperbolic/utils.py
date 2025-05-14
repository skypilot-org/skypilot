"""Hyperbolic API utilities."""
import json
from typing import Any, Dict, List, Optional

import requests

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# TODO: replace this with prod URL
BASE_URL = 'http://localhost:8080/v2'

def _make_request(method: str, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Make a request to the Hyperbolic API."""
    url = f'{BASE_URL}{endpoint}'
    try:
        if method == 'GET':
            response = requests.get(url)
        elif method == 'POST':
            response = requests.post(url, json=data)
        elif method == 'DELETE':
            response = requests.delete(url)
        else:
            raise ValueError(f'Unsupported HTTP method: {method}')
        
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f'API request failed: {e}')
        raise RuntimeError(f'Failed to communicate with Hyperbolic API: {e}')

def launch_instance(gpu_model: str, gpu_count: int, name: str) -> str:
    """Launch a new instance with specified GPU configuration."""
    data = {
        'gpuModel': gpu_model,
        'gpuCount': str(gpu_count),
        'name': name
    }
    response = _make_request('POST', '/marketplace/instances/create-cheapest', data)
    return response['instanceId']

def list_instances() -> Dict[str, Dict[str, Any]]:
    """List all instances."""
    response = _make_request('GET', '/marketplace/instances')
    instances = {}
    for instance in response['instances']:
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
    response = _make_request('GET', f'/marketplace/instances/{instance_id}')
    return {
        'status': response['status'],
        'name': response['name'],
        'internal_ip': response.get('internalIp'),
        'external_ip': response.get('externalIp'),
        'ssh_port': response.get('sshPort', 22)
    }
