"""Novita API utilities."""

import os
from typing import Any, Dict

from sky.adaptors import common

# Lazy import to avoid dependency on external packages
requests = common.LazyImport('requests')

# Novita API configuration
NOVITA_API_BASE = 'https://api.novita.ai/gpu-instance/openapi/v1/gpu'
NOVITA_API_KEY_PATH = '~/.novita/api_key'


def get_api_key() -> str:
    """Get Novita API key from file."""
    api_key_path = os.path.expanduser(NOVITA_API_KEY_PATH)
    if not os.path.exists(api_key_path):
        raise FileNotFoundError(
            f'Novita API key not found at {api_key_path}. '
            'Please save your API key to this file.')

    with open(api_key_path, 'r', encoding='utf-8') as f:
        api_key = f.read().strip()

    if not api_key:
        raise ValueError(f'Novita API key is empty in {api_key_path}')

    return api_key


def make_request(method: str, endpoint: str, **kwargs) -> Any:
    """Make a request to the Novita API."""
    url = f'{NOVITA_API_BASE}/{endpoint.lstrip("/")}'
    headers = {
        'X-API-KEY': get_api_key(),
        'Content-Type': 'application/json',
    }

    response = requests.request(method, url, headers=headers, **kwargs)
    response.raise_for_status()

    # Some APIs (like delete) return empty responses with just 200 status
    if response.text.strip():
        return response.json()
    else:
        # Return empty dict for empty responses (e.g., delete operations)
        return {}


def get_instances() -> Dict[str, Any]:
    """Get all instances."""
    return make_request('GET', '/instances')


def get_instance_info(instance_id: str) -> Dict[str, Any]:
    """Get information about a specific instance."""
    return make_request('GET', f'/instances/{instance_id}')


def create_instance(config: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new instance."""
    return make_request('POST', '/instances/create', json=config)


def delete_instance(instance_id: str) -> Dict[str, Any]:
    """Delete an instance.

    Note: Novita delete API returns empty response with 200 status.
    """
    return make_request('POST', '/instances/delete', json={'instanceId': instance_id})


