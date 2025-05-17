"""Hyperbolic API utilities."""
import enum
import json
import os
import pprint
import time
from typing import Any, Dict, List, Optional

import requests

from sky import sky_logging
from sky.utils import status_lib

# Module-level constants
CREDENTIALS_PATH = '~/.hyperbolic/api_key'
CASTLE_BASE_URL = 'http://localhost:8080'
GATEWAY_BASE_URL = 'http://localhost:8000'
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
TIMEOUT = 180

logger = sky_logging.init_logger(__name__)


class HyperbolicError(Exception):
    """Base exception for Hyperbolic API errors."""
    pass


class HyperbolicInstanceStatus(enum.Enum):
    """Statuses enum for Hyperbolic instances."""
    CREATING = 'creating'
    STARTING = 'starting'
    RUNNING = 'online'
    FAILED = 'failed'
    ERROR = 'error'
    TERMINATED = 'terminated'

    @classmethod
    def cluster_status_map(
        cls
    ) -> Dict['HyperbolicInstanceStatus', Optional[status_lib.ClusterStatus]]:
        return {
            cls.CREATING: status_lib.ClusterStatus.INIT,
            cls.STARTING: status_lib.ClusterStatus.INIT,
            cls.RUNNING: status_lib.ClusterStatus.UP,
            cls.FAILED: status_lib.ClusterStatus.INIT,
            cls.ERROR: status_lib.ClusterStatus.INIT,
            cls.TERMINATED: None,
        }

    @classmethod
    def from_raw_status(cls, status: str) -> 'HyperbolicInstanceStatus':
        """Convert raw status string to HyperbolicInstanceStatus enum."""
        try:
            return cls(status.lower())
        except ValueError:
            raise HyperbolicError(f'Unknown instance status: {status}')

    def to_cluster_status(self) -> Optional[status_lib.ClusterStatus]:
        """Convert to SkyPilot cluster status."""
        return self.cluster_status_map().get(self)


class HyperbolicClient:

    def __init__(self):
        """Initialize the Hyperbolic client with API credentials."""
        cred_path = os.path.expanduser(CREDENTIALS_PATH)
        if not os.path.exists(cred_path):
            raise RuntimeError(f'API key not found at {cred_path}')
        with open(cred_path, 'r') as f:
            self.api_key = f.read().strip()
        self.headers = {'Authorization': f'Bearer {self.api_key}'}
        self.api_url = GATEWAY_BASE_URL

    def _make_request(
            self,
            method: str,
            endpoint: str,
            payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make an API request to Hyperbolic."""
        # Select base URL based on endpoint version
        if endpoint.startswith('/v2/'):
            base_url = CASTLE_BASE_URL
        else:
            base_url = GATEWAY_BASE_URL

        url = f'{base_url}{endpoint}'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        # Debug logging for request
        logger.debug(f'Making {method} request to {url}')
        if payload:
            logger.debug(f'Request payload: {json.dumps(payload, indent=2)}')

        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=120)
            elif method == 'POST':
                response = requests.post(url,
                                         headers=headers,
                                         json=payload,
                                         timeout=120)
            else:
                raise HyperbolicError(f'Unsupported HTTP method: {method}')

            # Debug logging for response
            logger.debug(f'Response status code: {response.status_code}')
            logger.debug(f'Response headers: {dict(response.headers)}')
            try:
                response_data = response.json()
                logger.debug(
                    f'Response body: {json.dumps(response_data, indent=2)}')
            except json.JSONDecodeError:
                logger.debug(f'Response body (raw): {response.text}')

            if not response.ok:
                error_msg = response_data.get(
                    'error', response_data.get('message', response.text))
                raise HyperbolicError(f'API request failed: {error_msg}')

            return response_data
        except requests.exceptions.RequestException as e:
            raise HyperbolicError(f'Request failed: {str(e)}')

    def launch_instance(self, gpu_model: str, gpu_count: int, name: str) -> str:
        """Launch a new instance with the specified configuration."""
        payload = {
            'gpuModel': gpu_model,
            'gpuCount': str(gpu_count),
            'userMetadata': {
                'skypilot': {
                    'cluster_name': name,
                    'launch_time': str(int(time.time()))
                }
            }
        }
        endpoint = '/v2/marketplace/instances/create-cheapest'
        try:
            response = self._make_request('POST', endpoint, payload=payload)
            instance_id = response.get('instanceName')
            if not instance_id:
                raise HyperbolicError('No instance ID returned from API')
            return instance_id
        except Exception as e:
            raise HyperbolicError(f'Failed to launch instance: {str(e)}')

    def list_instances(
        self,
        status: Optional[str] = None,
        user_metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """List all instances, optionally filtered by status and metadata.
        
        Args:
            status: Optional status to filter instances by
            metadata: Optional dictionary of metadata to filter instances by
        """
        endpoint = '/v1/marketplace/instances'
        try:
            response = self._make_request('GET', endpoint)
            logger.debug(f'Raw API response: {json.dumps(response, indent=2)}')
            instances = {}
            for instance in response.get('instances', []):
                instance_info = instance.get('instance', {})
                current_status = instance_info.get('status')
                logger.debug(
                    f'Instance {instance.get("id")} status: {current_status}')

                # Convert raw status to enum
                try:
                    instance_status = HyperbolicInstanceStatus.from_raw_status(
                        current_status)
                except HyperbolicError as e:
                    logger.warning(
                        f'Failed to parse status for instance {instance.get("id")}: {e}'
                    )
                    continue

                if status and instance_status.value != status.lower():
                    continue

                # Filter by user_metadata if specified
                skypilot = instance.get('userMetadata', {}).get('skypilot', {})
                if user_metadata:
                    cluster_name = user_metadata.get('skypilot_cluster_name',
                                                     '')
                    if not skypilot.get('cluster_name',
                                        '').startswith(cluster_name):
                        logger.debug(
                            f'Skipping instance {instance.get("id")} - skypilot metadata {skypilot} does not match {user_metadata}'
                        )
                        continue
                    logger.debug(
                        f'Including instance {instance.get("id")} - skypilot metadata matches'
                    )

                hardware = instance_info.get('hardware', {})
                instances[instance.get('id')] = {
                    'id': instance.get('id'),
                    'created': instance.get('created'),
                    'sshCommand': instance.get('sshCommand'),
                    'status': instance_status.value,
                    'gpu_count': instance_info.get('gpu_count'),
                    'gpus_total': instance_info.get('gpus_total'),
                    'owner': instance_info.get('owner'),
                    'cpus': hardware.get('cpus'),
                    'gpus': hardware.get('gpus'),
                    'ram': hardware.get('ram'),
                    'storage': hardware.get('storage'),
                    'pricing': instance_info.get('pricing'),
                    'metadata': instance.get('userMetadata', {})
                }
            return instances
        except Exception as e:
            raise HyperbolicError(f'Failed to list instances: {str(e)}')

    def terminate_instance(self, instance_id: str) -> None:
        """Terminate an instance by ID."""
        endpoint = '/v1/marketplace/instances/terminate'
        data = {'id': instance_id}
        try:
            self._make_request('POST', endpoint, payload=data)
        except Exception as e:
            raise HyperbolicError(
                f'Failed to terminate instance {instance_id}: {str(e)}')

    def wait_for_instance(self,
                          instance_id: str,
                          target_status: str,
                          timeout: int = TIMEOUT) -> bool:
        """Wait for an instance to reach a specific status and have SSH command available."""
        start_time = time.time()
        target_status_enum = HyperbolicInstanceStatus.from_raw_status(
            target_status)
        logger.info(
            f'Waiting for instance {instance_id} to reach status {target_status_enum.value} and have SSH command'
        )

        while True:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                logger.error(
                    f'Timeout after {int(elapsed)}s waiting for instance {instance_id}'
                )
                return False

            try:
                instances = self.list_instances()
                instance = instances.get(instance_id)

                if not instance:
                    logger.warning(f'Instance {instance_id} not found')
                    time.sleep(5)
                    continue

                current_status = instance.get('status', '').lower()
                ssh_command = instance.get('sshCommand')
                logger.debug(
                    f'Current status: {current_status}, Target status: {target_status_enum.value}, SSH command: {ssh_command}'
                )

                if current_status == target_status_enum.value and ssh_command:
                    logger.info(
                        f'Instance {instance_id} reached target status {target_status_enum.value} and has SSH command after {int(elapsed)}s'
                    )
                    return True

                if current_status in ['failed', 'error', 'terminated']:
                    logger.error(
                        f'Instance {instance_id} reached terminal status: {current_status} after {int(elapsed)}s'
                    )
                    return False

                time.sleep(5)
            except Exception as e:
                logger.warning(
                    f'Error while waiting for instance {instance_id}: {str(e)}')
                time.sleep(5)


# Module-level singleton client
_client = None


def get_client() -> HyperbolicClient:
    """Get or create the Hyperbolic client singleton."""
    global _client
    if _client is None:
        _client = HyperbolicClient()
    return _client


# Backward-compatible wrapper functions


def launch_instance(gpu_model: str, gpu_count: int, name: str) -> str:
    return get_client().launch_instance(gpu_model, gpu_count, name)


def list_instances(
        status: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None) -> Dict[str, Dict[str, Any]]:
    return get_client().list_instances(status, metadata)


def terminate_instance(instance_id: str) -> None:
    return get_client().terminate_instance(instance_id)


def wait_for_instance(instance_id: str,
                      target_status: str,
                      timeout: int = TIMEOUT) -> bool:
    return get_client().wait_for_instance(instance_id, target_status, timeout)
