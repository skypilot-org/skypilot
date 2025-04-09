"""SimplePod API client."""

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional
import uuid

import requests

from sky.utils import annotations

ENDPOINT = 'https://api.simplepod.ai/'
SIMPLEPOD_API_KEY_PATH = '~/.simplepod/simplepod_keys'

class SimplePodError(Exception):
    """Raised when SimplePod API returns an error."""
    def __init__(self, message: str, code: int = 400):
        self.code = code
        super().__init__(message)

def raise_simplepod_error(response: 'requests.Response') -> None:
    """Raise SimplePodError if appropriate."""
    status_code = response.status_code
    if response.ok:
        return
    try:
        resp_json = response.json()
        message = resp_json.get('error', response.text)
    except (KeyError, json.decoder.JSONDecodeError) as e:
        raise SimplePodError(
            f'Unexpected error. Status code: {status_code} \n {response.text}'
            f'\n {str(e)}',
            code=status_code) from e
    raise SimplePodError(f'{message}', status_code)

def read_api_key(path: str) -> str:
    """Read API key from file."""
    try:
        with open(os.path.expanduser(path), mode='r', encoding='utf-8') as f:
            for line in f:
                if '=' in line:
                    key, value = line.strip().split('=', 1)
                    if key.strip() == 'api_key':
                        api_key = value.strip()
                        if not api_key:
                            raise SimplePodError('API key is empty')
                        return api_key
            raise SimplePodError('No api_key found in credentials file')
    except FileNotFoundError:
        raise SimplePodError(f'Credentials file not found at {path}')

class SimplePodClient:
    """SimplePod API Client"""

    def __init__(self, api_key_path: str = SIMPLEPOD_API_KEY_PATH):
        """Initialize client with API key.

        Args:
            api_key_path: Path to API key file. Defaults to ~/.simplepod/simplepod_keys

        Raises:
            SimplePodError: If API key file is invalid or missing
        """
        self.api_key = read_api_key(api_key_path)
        self.headers = {
            'X-AUTH-TOKEN': self.api_key,
            'Content-Type': 'application/json'
        }
        self._validate_auth()

    def _validate_auth(self) -> None:
        """Validate API key by making a test API call."""
        try:
            self.list_instances()
        except SimplePodError as e:
            if e.code == 401:
                raise SimplePodError('Invalid API key') from e
            raise

    def _make_request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Make HTTP request with error handling and retries.

        Args:
            method: HTTP method
            path: API path
            **kwargs: Additional arguments passed to requests

        Returns:
            Response object

        Raises:
            SimplePodError: If request fails
        """
        url = ENDPOINT.rstrip('/') + '/' + path.lstrip('/')
        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)
            raise_simplepod_error(response)
            return response
        except requests.exceptions.RequestException as e:
            raise SimplePodError(f'Request failed: {str(e)}') from e

    def list_instances(self) -> List[Dict[str, Any]]:
        """List all instances owned by the user."""
        response = self._make_request('GET', 'instances/list')
        return response.json()

    def list_available_instances(self) -> List[Dict[str, Any]]:
        """List available instances for rent."""
        response = self._make_request('GET', 'instances/market/list?rentalStatus=active')
        return response.json()

    def create_instance(
        self,
        instance_type: str,
        name: str,
        ssh_key: str,
        template_id: Optional[int] = None,
        env_variables: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Launch a new instance.

        Args:
            instance_type: Format like "gpu_1x_rtx a2000" or "A100:1"
            name: Instance name
            ssh_key: SSH key for access
            template_id: Optional template ID to use
            env_variables: Optional environment variables

        Returns:
            Instance ID

        Raises:
            SimplePodError: If no matching instances are available or API error occurs
        """
        # Handle instance type formats:
        # 1. gpu_1x_rtx a2000
        # 2. A100:1
        # 3. A100 1
        parts = instance_type.split()

        if len(parts) == 2 and parts[0].startswith('gpu_'):
            # Format: gpu_1x_rtx a2000
            gpu_type = parts[0].replace('gpu_1x_', '') + ' ' + parts[1]
            gpu_count = 1
        else:
            # Format: A100:1 or A100 1
            instance_str = ' '.join(parts)
            separator = ':' if ':' in instance_str else ' '
            try:
                gpu_type, count_str = instance_str.rsplit(separator, 1)
                gpu_count = int(count_str)
            except ValueError as e:
                raise SimplePodError(
                    f'Invalid instance type format. Expected "gpu_type:count", "gpu_type count", '
                    f'or "gpu_1x_type", got: {instance_type}') from e

        # First find an available instance matching requirements
        available = self.list_available_instances()
        matching = [i for i in available
                   if i['gpuModel'].lower() == gpu_type.lower() and i['gpuCount'] >= gpu_count]

        if not matching:
            raise SimplePodError(f'No available instances found matching {gpu_type} with {gpu_count} GPUs')

        # Use first matching instance
        market_instance = matching[0]
        print(f'Found available instance: {market_instance}')
        payload = {
            'gpuCount': gpu_count,
            'instanceMarket': f"/instances/market/{market_instance['id']}",
            'name': 'testHead',
        }

        if template_id:
            payload['instanceTemplate'] = f'/instances/templates/{template_id}'
        else:
            payload['instanceTemplate'] = f'/instances/templates/29'

        if env_variables:
            payload['envVariables'] = env_variables

        response = self._make_request('POST', 'instances', json=payload)
        return str(response.json()['id'])

    @annotations.lru_cache(scope='global')
    def list_regions(self) -> Dict[str, str]:
        """Get available regions.

        Returns:
            Dict mapping region IDs to region names
        """
        response = self._make_request('GET', 'regions')
        regions = {}
        for region in response.json()['regions']:
            regions[region['id']] = region['name']
        return regions

    def get_instance(self, instance_id: str) -> Dict[str, Any]:
        """Get instance details."""
        response = self._make_request('GET', f'instances/{instance_id}')
        return response.json()

    def delete_instance(self, instance_id: str) -> None:
        """Delete/terminate an instance permanently."""
        self._make_request('DELETE', f'instances/{instance_id}')

    def reboot_instances(self, instance_ids: List[str]) -> None:
        """Reboot one or more instances.

        Args:
            instance_ids: List of instance IDs to reboot

        Raises:
            SimplePodError: If reboot fails
        """
        payload = {'instanceIds': instance_ids}
        self._make_request(
            'PATCH',
            'instances/reboot',
            json=payload,
            headers=self.headers | {'Content-Type': 'application/merge-patch+json'}
        )

    def update_instance(
        self,
        instance_id: str,
        auto_renew: Optional[bool] = None,
        auto_renew_max_price: Optional[float] = None,
        name: Optional[str] = None,
        notes: Optional[str] = None,
        template_id: Optional[int] = None,
        env_variables: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        """Update instance settings."""
        payload = {}
        if auto_renew is not None:
            payload['isAutoRenewOn'] = auto_renew
        if auto_renew_max_price is not None:
            payload['priceAutoRenewMax'] = auto_renew_max_price
        if name is not None:
            payload['name'] = 'test-Head'
        if notes is not None:
            payload['notes'] = notes
        if template_id is not None:
            payload['instanceTemplate'] = f'/instances/templates/{template_id}'
        if env_variables is not None:
            payload['envVariables'] = env_variables

        if payload:
            self._make_request('PUT', f'instances/{instance_id}', json=payload)
