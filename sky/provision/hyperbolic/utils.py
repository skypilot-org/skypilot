"""Hyperbolic API utilities."""
import enum
import json
import os
import time
from typing import Any, Dict, Optional, Tuple

import requests

from sky import authentication
from sky import sky_logging
from sky.utils import status_lib

BASE_URL = 'https://api.hyperbolic.xyz'
API_KEY_PATH = '~/.hyperbolic/api_key'

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
TIMEOUT = 120

# Hyperbolic API Endpoints
API_ENDPOINTS = {
    'create': '/v2/marketplace/virtual-machine-rentals',
    'list': '/v2/marketplace/virtual-machine-rentals',
    'terminate': '/v2/marketplace/virtual-machine-rentals/terminate'
}

logger = sky_logging.init_logger(__name__)


class HyperbolicError(Exception):
    """Base exception for Hyperbolic API errors."""
    pass


class HyperbolicInstanceStatus(enum.Enum):
    """Statuses enum for Hyperbolic instances."""
    UNKNOWN = 'unknown'
    ONLINE = 'online'
    OFFLINE = 'offline'
    STARTING = 'starting'
    STOPPING = 'stopping'
    BUSY = 'busy'
    RESTARTING = 'restarting'
    CREATING = 'creating'
    FAILED = 'failed'
    ERROR = 'error'
    TERMINATED = 'terminated'

    # API endpoint statuses
    PENDING = 'pending'
    RUNNING = 'running'

    @classmethod
    def cluster_status_map(
        cls
    ) -> Dict['HyperbolicInstanceStatus', Optional[status_lib.ClusterStatus]]:
        return {
            cls.CREATING: status_lib.ClusterStatus.INIT,
            cls.STARTING: status_lib.ClusterStatus.INIT,
            cls.PENDING: status_lib.ClusterStatus.INIT,  # Map PENDING to INIT
            cls.ONLINE: status_lib.ClusterStatus.UP,
            cls.RUNNING: status_lib.ClusterStatus.UP,  # Map RUNNING to UP
            cls.FAILED: status_lib.ClusterStatus.INIT,
            cls.ERROR: status_lib.ClusterStatus.INIT,
            cls.RESTARTING: status_lib.ClusterStatus.INIT,
            cls.STOPPING: status_lib.ClusterStatus.INIT,
            cls.UNKNOWN: status_lib.ClusterStatus.INIT,
            cls.BUSY: status_lib.ClusterStatus.INIT,
            cls.OFFLINE: status_lib.ClusterStatus.INIT,
            cls.TERMINATED: None,
        }

    @classmethod
    def from_raw_status(cls, status: str) -> 'HyperbolicInstanceStatus':
        """Convert raw status string to HyperbolicInstanceStatus enum."""
        if not status:
            return cls.UNKNOWN

        status_lower = status.lower()
        status_mapping = {
            'failed': cls.FAILED,
            'pending': cls.PENDING,
            'running': cls.RUNNING,
            'terminated': cls.TERMINATED,
            'unknown': cls.UNKNOWN,
            'online': cls.ONLINE,
            'offline': cls.OFFLINE,
            'starting': cls.STARTING,
            'stopping': cls.STOPPING,
            'busy': cls.BUSY,
            'restarting': cls.RESTARTING,
            'creating': cls.CREATING,
            'error': cls.ERROR,
        }

        result = status_mapping.get(status_lower, cls.UNKNOWN)
        if result == cls.UNKNOWN:
            logger.warning(
                f'Unknown instance status: {status}, defaulting to UNKNOWN')
        return result

    def to_cluster_status(self) -> Optional[status_lib.ClusterStatus]:
        """Convert to SkyPilot cluster status."""
        return self.cluster_status_map().get(self)


class HyperbolicClient:
    """Client for interacting with the Hyperbolic API."""

    def __init__(self):
        """Initialize the Hyperbolic client with API credentials."""
        cred_path = os.path.expanduser(API_KEY_PATH)
        if not os.path.exists(cred_path):
            raise RuntimeError(f'API key not found at {cred_path}')
        with open(cred_path, 'r', encoding='utf-8') as f:
            self.api_key = f.read().strip()
        self.headers = {'Authorization': f'Bearer {self.api_key}'}
        self.api_url = BASE_URL

    def _make_request(
            self,
            method: str,
            endpoint: str,
            payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make an API request to Hyperbolic."""
        url = f'{BASE_URL}{endpoint}'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        logger.debug(f'Making {method} request to {url}')
        if payload:
            logger.debug(f'Request payload: {json.dumps(payload, indent=2)}')

        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=TIMEOUT)
            elif method == 'POST':
                response = requests.post(url,
                                         headers=headers,
                                         json=payload,
                                         timeout=TIMEOUT)
            else:
                raise HyperbolicError(f'Unsupported HTTP method: {method}')

            logger.debug(f'Response status code: {response.status_code}')

            try:
                response_data = response.json()
                logger.debug(
                    f'Response body: {json.dumps(response_data, indent=2)}')
            except json.JSONDecodeError as exc:
                response_text = response.text
                logger.debug(f'Response body (raw): {response_text}')
                if not response.ok:
                    raise HyperbolicError(
                        f'API request failed with status '
                        f'{response.status_code}: {response_text}') from exc
                return {}

            if not response.ok:
                error_msg = response_data.get(
                    'error', response_data.get('message', response.text))
                raise HyperbolicError(f'API request failed with status '
                                      f'{response.status_code}: {error_msg}')

            return response_data
        except requests.exceptions.RequestException as e:
            raise HyperbolicError(f'Request failed: {str(e)}') from e
        except Exception as e:
            raise HyperbolicError(
                f'Unexpected error during API request: {str(e)}') from e

    def _create_payload(self, gpu_model: str, gpu_count: int,
                        name: str) -> Dict[str, Any]:
        """Create a payload for launching an instance."""
        skypilot_metadata = {
            'skypilot': {
                'cluster_name': name,
                'launch_time': str(int(time.time()))
            }
        }

        return {'gpuCount': gpu_count, 'userMetadata': skypilot_metadata}

    def launch_instance(self, gpu_model: str, gpu_count: int,
                        name: str) -> Tuple[str, str]:
        """Launch a new instance with the specified configuration."""
        config = self._create_payload(gpu_model, gpu_count, name)

        logger.info(f'Using Hyperbolic API endpoints for {gpu_model} instance')

        try:
            response = self._make_request('POST',
                                          API_ENDPOINTS['create'],
                                          payload=config)
            logger.debug(f'Launch response: {json.dumps(response, indent=2)}')

            instance_id = response.get('id')
            if not instance_id:
                raise HyperbolicError(
                    'No instance ID returned from Hyperbolic API')

            ssh_command = response.get('meta', {}).get('ssh_command')
            if ssh_command:
                logger.info(f'Launched on-demand instance {instance_id} '
                            f'with SSH command')
                return str(instance_id), ssh_command
            else:
                raise HyperbolicError(
                    'No SSH command available in API response')

        except Exception as e:
            logger.error(f'Failed to launch instance: {str(e)}')
            raise HyperbolicError(f'Failed to launch instance: {str(e)}') from e

    def _parse_instance(self, instance: Dict[str,
                                             Any]) -> Optional[Dict[str, Any]]:
        """Parse an instance from the API response."""
        instance_id = str(instance.get('id'))
        meta = instance.get('meta', {})
        current_status = instance.get('status') or (
            'terminated' if instance.get('terminatedAt') else 'running')

        resources = meta.get('resources', {})
        gpus = resources.get('gpus', {})
        gpu_model = list(gpus.keys())[0] if gpus else ''
        gpu_count = gpus.get(gpu_model, {}).get('count', 0) if gpus else 0

        return {
            'id': instance_id,
            'created': instance.get('createdAt'),
            'sshCommand': meta.get('ssh_command'),
            'status':
                HyperbolicInstanceStatus.from_raw_status(current_status).value,
            'gpu_count': gpu_count,
            'gpus_total': gpu_count,
            'owner': instance.get('userId'),
            'cpus': resources.get('vcpu_count'),
            'gpus': gpu_count,
            'ram': f'{resources.get("ram_gb")}GB'
                   if resources.get('ram_gb') else None,
            'storage': f'{resources.get("storage_gb")}GB'
                       if resources.get('storage_gb') else None,
            'pricing': {
                'costPerHour': instance.get('costPerHour')
            },
            'metadata': meta,
            'gpu_model': gpu_model,
            'is_ondemand': True,
            'public_ip': meta.get('public_ip'),
            'internal_ip': meta.get('internal_ip')
        }

    def _filter_by_metadata(
            self, instance: Dict[str, Any],
            metadata: Optional[Dict[str, Dict[str, str]]]) -> bool:
        """Filter instances based on metadata."""
        if not metadata:
            return True

        cluster_name = metadata.get('skypilot', {}).get('cluster_name', '')
        instance_skypilot = instance.get('meta',
                                         {}).get('userMetadata',
                                                 {}).get('skypilot', {})

        return instance_skypilot.get('cluster_name',
                                     '').startswith(cluster_name)

    def _get_instances_from_endpoint(
        self,
        endpoint: str,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Dict[str, str]]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get instances from the Hyperbolic API endpoint."""
        instances = {}
        try:
            response = self._make_request('GET', endpoint)
            instances_list = response
            for instance in instances_list:
                if not isinstance(instance, dict):
                    continue
                parsed = self._parse_instance(instance)
                if parsed and self._filter_by_metadata(instance, metadata):
                    if not status or parsed['status'] == status.lower():
                        instances[parsed['id']] = parsed
        except HyperbolicError as e:
            logger.warning(
                f'Failed to get instances from Hyperbolic API: {str(e)}')
        return instances

    def list_instances(
        self,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Dict[str, str]]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """List all instances, optionally filtered by status and metadata."""
        return self._get_instances_from_endpoint(API_ENDPOINTS['list'], status,
                                                 metadata)

    def terminate_instance(self, instance_id: str) -> None:
        """Terminate an instance by ID."""
        try:
            logger.info('Using Hyperbolic API terminate endpoint')

            data = {'rentalId': instance_id}
            self._make_request('POST', API_ENDPOINTS['terminate'], payload=data)
        except Exception as e:
            raise HyperbolicError(
                f'Failed to terminate instance {instance_id}: {str(e)}') from e

    def wait_for_instance(self,
                          instance_id: str,
                          target_status: str,
                          timeout: int = TIMEOUT) -> bool:
        """Wait for an instance to reach a specific status."""
        start_time = time.time()
        target_status_enum = HyperbolicInstanceStatus.from_raw_status(
            target_status)
        logger.info(f'Waiting for instance {instance_id} to reach status '
                    f'{target_status_enum.value} and have SSH command')

        while True:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                logger.error(f'Timeout after {int(elapsed)}s waiting for '
                             f'instance {instance_id}')
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
                    f'Current status: {current_status}, Target status: '
                    f'{target_status_enum.value}, SSH command: {ssh_command}')

                if current_status == target_status_enum.value and ssh_command:
                    logger.info(
                        f'Instance {instance_id} reached target status '
                        f'{target_status_enum.value} and has SSH command '
                        f'after {int(elapsed)}s')
                    return True

                # Check for terminal statuses
                if current_status in ['failed', 'error', 'terminated']:
                    logger.error(
                        f'Instance {instance_id} reached terminal status: '
                        f'{current_status} after {int(elapsed)}s')
                    return False

                time.sleep(5)
            except HyperbolicError as e:
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
def launch_instance(gpu_model: str, gpu_count: int,
                    name: str) -> Tuple[str, str]:
    """Launch a new instance with the specified configuration."""
    return get_client().launch_instance(gpu_model, gpu_count, name)


def list_instances(
    status: Optional[str] = None,
    metadata: Optional[Dict[str, Dict[str, str]]] = None
) -> Dict[str, Dict[str, Any]]:
    """List all instances, optionally filtered by status and metadata."""
    return get_client().list_instances(status=status, metadata=metadata)


def terminate_instance(instance_id: str) -> None:
    """Terminate an instance by ID."""
    return get_client().terminate_instance(instance_id)


def wait_for_instance(instance_id: str,
                      target_status: str,
                      timeout: int = TIMEOUT) -> bool:
    """Wait for an instance to reach a specific status."""
    return get_client().wait_for_instance(instance_id, target_status, timeout)
