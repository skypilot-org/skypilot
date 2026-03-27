"""Spheron API utilities."""

import functools
import json
import os
from typing import Any, Dict, List, Optional

from sky.adaptors import common

# Lazy import to avoid dependency on external packages
requests = common.LazyImport('requests')

# Spheron API configuration
SPHERON_API_BASE = 'https://app.spheron.ai'
SPHERON_API_KEY_PATH = '~/.spheron/api_key'


def get_api_key() -> str:
    """Get Spheron API key from file."""
    api_key_path = os.path.expanduser(SPHERON_API_KEY_PATH)
    if not os.path.exists(api_key_path):
        raise FileNotFoundError(f'Spheron API key not found at {api_key_path}. '
                                'Please save your API key to this file.')

    with open(api_key_path, 'r', encoding='utf-8') as f:
        api_key = f.read().strip()

    if not api_key:
        raise ValueError(f'Spheron API key is empty in {api_key_path}')

    return api_key


def make_request(method: str, endpoint: str, **kwargs) -> Any:
    """Make a request to the Spheron API."""
    url = f'{SPHERON_API_BASE}/{endpoint.lstrip("/")}'
    headers = {
        'Authorization': f'Bearer {get_api_key()}',
        'Content-Type': 'application/json',
    }

    response = requests.request(method,
                                url,
                                headers=headers,
                                timeout=30,
                                **kwargs)
    response.raise_for_status()

    # Some APIs (like delete) return empty responses with just 200 status
    if response.text.strip():
        return response.json()
    else:
        # Return empty dict for empty responses (e.g., delete operations)
        return {}


def get_teams() -> Any:
    """Get all teams for the authenticated user."""
    return make_request('GET', '/api/teams')


@functools.lru_cache(maxsize=None)
def get_team_id() -> str:
    """Get the first team ID for the authenticated user."""
    response = get_teams()
    # Handle both bare list and wrapped dict responses from the API
    if isinstance(response, list):
        teams = response
    else:
        teams = response.get('teams', response.get('data', []))
    if not teams:
        raise ValueError('No teams found for Spheron account.')
    return teams[0]['id']


def get_deployments() -> List[Any]:
    """Get all deployments (paginated)."""
    all_deployments: List[Any] = []
    page = 1
    limit = 100

    while True:
        response = make_request('GET',
                                '/api/deployments',
                                params={
                                    'page': page,
                                    'limit': limit
                                })
        if isinstance(response, list):
            batch = response
            total_pages = 1
        else:
            batch = response.get('deployments', response.get('data', []))
            total_pages = response.get('totalPages', 1)

        all_deployments.extend(batch)

        if not batch or page >= total_pages:
            break
        page += 1

    return all_deployments


def get_deployment(deployment_id: str) -> Dict[str, Any]:
    """Get information about a specific deployment."""
    return make_request('GET', f'/api/deployments/{deployment_id}')


def create_deployment(config: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new deployment."""
    return make_request('POST', '/api/deployments', json=config)


def delete_deployment(deployment_id: str) -> Dict[str, Any]:
    """Terminate a deployment.

    Note: Spheron uses HTTP DELETE method for termination.

    Raises:
        SpheronMinimumRuntimeError: if the instance has not yet met its minimum
            runtime requirement and cannot be terminated yet.
    """
    url = f'{SPHERON_API_BASE}/api/deployments/{deployment_id}'
    headers = {
        'Authorization': f'Bearer {get_api_key()}',
        'Content-Type': 'application/json',
    }
    response = requests.request('DELETE', url, headers=headers, timeout=30)

    # Check for minimum runtime constraint before raising on status
    if response.status_code == 400 and response.text.strip():
        try:
            result = response.json()
        except json.JSONDecodeError:
            response.raise_for_status()
            return {}
        if not result.get('canTerminate', True):
            # If the instance is already in a terminal state, treat as success
            current_status = result.get('currentStatus', '')
            if current_status in ('terminated', 'terminated-provider'):
                return {}
            time_remaining = result.get('timeRemaining', '?')
            minimum_runtime = result.get('minimumRuntime', '?')
            raise SpheronMinimumRuntimeError(
                f'Cannot terminate instance {deployment_id}: it must run for '
                f'at least {minimum_runtime} minutes. '
                f'Please try again in {time_remaining} minute(s).')

    response.raise_for_status()
    return response.json() if response.text.strip() else {}


class SpheronMinimumRuntimeError(RuntimeError):
    """Raised when an instance cannot be terminated due to minimum runtime."""


def get_ssh_keys() -> Any:
    """Get all SSH keys."""
    return make_request('GET', '/api/ssh-keys')


def add_ssh_key(name: str,
                public_key: str,
                team_id: Optional[str] = None) -> Dict[str, Any]:
    """Add a new SSH key."""
    config: Dict[str, Any] = {'name': name, 'publicKey': public_key}
    if team_id is not None:
        config['teamId'] = team_id
    return make_request('POST', '/api/ssh-keys', json=config)


def delete_ssh_key(key_id: str) -> Dict[str, Any]:
    """Delete an SSH key."""
    return make_request('DELETE', f'/api/ssh-keys/{key_id}')


def get_gpu_offers(page: int = 1, limit: int = 100) -> Dict[str, Any]:
    """Get available GPU offers."""
    params = {'page': page, 'limit': limit}
    return make_request('GET', '/api/gpu-offers', params=params)
