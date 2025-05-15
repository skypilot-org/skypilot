"""Hyperbolic API utilities."""
import os
import time
from typing import Any, Dict, List, Optional
import requests
from sky import sky_logging

CREDENTIALS_PATH = '~/.hyperbolic/api_key'
CASTLE_BASE_URL = 'http://localhost:8080'
GATEWAY_BASE_URL = 'http://localhost:8000'
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

logger = sky_logging.init_logger(__name__)

class HyperbolicError(Exception):
    """Base exception for Hyperbolic API errors."""
    pass

class HyperbolicClient:
    def __init__(self):
        cred_path = os.path.expanduser(CREDENTIALS_PATH)
        if not os.path.exists(cred_path):
            raise RuntimeError(f'API key not found at {cred_path}')
        with open(cred_path, 'r') as f:
            self.api_key = f.read().strip()
        self.headers = {'Authorization': f'Bearer {self.api_key}'}

    def _make_request(self, method: str, url: str, **kwargs) -> Dict[str, Any]:
        """Make an HTTP request with retries."""
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.request(method, url, headers=self.headers, **kwargs)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt == MAX_RETRIES - 1:
                    raise HyperbolicError(f'API request failed after {MAX_RETRIES} attempts: {str(e)}')
                logger.warning(f'Request failed (attempt {attempt + 1}/{MAX_RETRIES}): {str(e)}')
                time.sleep(RETRY_DELAY)
        raise HyperbolicError('Unexpected error in _make_request')

    def launch_instance(self, gpu_model: str, gpu_count: int, name: str) -> str:
        """Launch a new instance with the specified configuration."""
        data = {
            'gpuModel': gpu_model,
            'gpuCount': str(gpu_count),
            'name': name,
            'source': 'skypilot'  # Add source identifier
        }
        endpoint = '/v2/marketplace/instances/create-cheapest'
        url = f'{CASTLE_BASE_URL}{endpoint}'
        logger.debug(f'POST {url} with {data}')
        
        try:
            response = self._make_request('POST', url, json=data)
            instance_id = response.get('instanceId', response.get('id'))
            if not instance_id:
                raise HyperbolicError('No instance ID returned from API')
            return instance_id
        except Exception as e:
            raise HyperbolicError(f'Failed to launch instance: {str(e)}')

    def list_instances(self, status: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """List all instances, optionally filtered by status."""
        endpoint = '/v1/marketplace/instances'
        url = f'{GATEWAY_BASE_URL}{endpoint}'
        logger.debug(f'GET {url}')
        
        try:
            response = self._make_request('GET', url)
            instances = {}
            for instance in response.get('instances', []):
                if status and instance['status'] != status:
                    continue
                instances[instance['id']] = {
                    'status': instance['status'],
                    'name': instance['name'],
                    'internal_ip': instance.get('internalIp'),
                    'external_ip': instance.get('externalIp'),
                    'ssh_port': instance.get('sshPort', 22),
                    'gpu_model': instance.get('gpuModel'),
                    'gpu_count': instance.get('gpuCount'),
                    'created_at': instance.get('createdAt')
                }
            return instances
        except Exception as e:
            raise HyperbolicError(f'Failed to list instances: {str(e)}')

    def terminate_instance(self, instance_id: str) -> None:
        """Terminate an instance by ID."""
        endpoint = '/v1/marketplace/instances/terminate'
        url = f'{GATEWAY_BASE_URL}{endpoint}'
        data = {'id': instance_id}
        logger.debug(f'POST {url} with {data}')
        
        try:
            self._make_request('POST', url, json=data)
        except Exception as e:
            raise HyperbolicError(f'Failed to terminate instance {instance_id}: {str(e)}')

    def get_instance(self, instance_id: str) -> Dict[str, Any]:
        """Get detailed information about a specific instance."""
        endpoint = f'/v1/marketplace/instances/{instance_id}'
        url = f'{GATEWAY_BASE_URL}{endpoint}'
        logger.debug(f'GET {url}')
        
        try:
            data = self._make_request('GET', url)
            return {
                'status': data['status'],
                'name': data['name'],
                'internal_ip': data.get('internalIp'),
                'external_ip': data.get('externalIp'),
                'ssh_port': data.get('sshPort', 22),
                'gpu_model': data.get('gpuModel'),
                'gpu_count': data.get('gpuCount'),
                'created_at': data.get('createdAt')
            }
        except Exception as e:
            raise HyperbolicError(f'Failed to get instance {instance_id}: {str(e)}')

    def wait_for_instance(self, instance_id: str, target_status: str, timeout: int = 300) -> bool:
        """Wait for an instance to reach a specific status."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                instance = self.get_instance(instance_id)
                if instance['status'] == target_status:
                    return True
                time.sleep(5)
            except Exception as e:
                logger.warning(f'Error while waiting for instance {instance_id}: {str(e)}')
                time.sleep(5)
        return False

# Module-level singleton client
_client = None

def get_client() -> HyperbolicClient:
    global _client
    if _client is None:
        _client = HyperbolicClient()
    return _client

# Backward-compatible wrapper functions

def launch_instance(gpu_model: str, gpu_count: int, name: str) -> str:
    return get_client().launch_instance(gpu_model, gpu_count, name)

def list_instances(status: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    return get_client().list_instances(status)

def terminate_instance(instance_id: str) -> None:
    return get_client().terminate_instance(instance_id)

def get_instance(instance_id: str) -> Dict[str, Any]:
    return get_client().get_instance(instance_id)

def wait_for_instance(instance_id: str, target_status: str, timeout: int = 300) -> bool:
    return get_client().wait_for_instance(instance_id, target_status, timeout)
