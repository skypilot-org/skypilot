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

# Hyperbolic SPOT Endpoints
SPOT_ENDPOINTS = {
    'create': '/v2/marketplace/instances/create-cheapest',
    'list': '/v1/marketplace/instances',
    'terminate': '/v1/marketplace/instances/terminate'
}
# Hyperbolic ON-DEMAND Endpoints
ONDEMAND_ENDPOINTS = {
    'create': '/v2/marketplace/virtual-machine-rentals',
    'list': '/v2/marketplace/virtual-machine-rentals',
    'terminate': '/v2/marketplace/virtual-machine-rentals/terminate'
}

# GPU types that currently use on-demand endpoints
# Note: In future, this will expand as more GPUs transition to on-demand
ONDEMAND_GPU_TYPES = {'H100'}

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

    # New on-demand endpoint statuses
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


def is_ondemand_gpu(gpu_model: str) -> bool:
    """Check if a GPU model currently uses on-demand endpoints."""
    return bool(gpu_model and any(gpu_model.upper().startswith(gpu_type)
                                  for gpu_type in ONDEMAND_GPU_TYPES))


def get_endpoints(gpu_model: str) -> Dict[str, str]:
    """Get the appropriate endpoints based on GPU model."""
    return ONDEMAND_ENDPOINTS if is_ondemand_gpu(gpu_model) else SPOT_ENDPOINTS


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

        if is_ondemand_gpu(gpu_model):
            return {'gpuCount': gpu_count, 'userMetadata': skypilot_metadata}
        else:
            config = {
                'gpuModel': gpu_model,
                'gpuCount': str(gpu_count),
                'userMetadata': skypilot_metadata
            }
            return authentication.setup_hyperbolic_authentication(config)

    def launch_instance(self, gpu_model: str, gpu_count: int,
                        name: str) -> Tuple[str, str]:
        """Launch a new instance with the specified configuration."""
        endpoints = get_endpoints(gpu_model)
        config = self._create_payload(gpu_model, gpu_count, name)

        logger.info(
            f'Using {"on-demand" if is_ondemand_gpu(gpu_model) else "SPOT"} '
            f'endpoints for {gpu_model} instance')

        try:
            response = self._make_request('POST',
                                          endpoints['create'],
                                          payload=config)
            logger.debug(f'Launch response: {json.dumps(response, indent=2)}')

            if is_ondemand_gpu(gpu_model):
                instance_id = response.get('id')
                if not instance_id:
                    raise HyperbolicError(
                        'No instance ID returned from on-demand API')

                ssh_command = response.get('meta', {}).get('ssh_command')
                if ssh_command:
                    logger.info(f'Launched on-demand instance {instance_id} '
                                f'with SSH command')
                    return str(instance_id), ssh_command
                else:
                    raise HyperbolicError(
                        'No SSH command available in on-demand response')
            else:
                instance_id = response.get('instanceName')
                if not instance_id:
                    raise HyperbolicError(
                        'No instance ID returned from SPOT API')

                logger.info(f'Successfully launched instance {instance_id}, '
                            f'waiting for it to be ready...')
                target_status = HyperbolicInstanceStatus.ONLINE.value
                if not self.wait_for_instance(instance_id, target_status):
                    raise HyperbolicError(
                        f'Instance {instance_id} failed to reach '
                        f'{target_status.upper()} state')

                instances = self.list_instances(
                    metadata={'skypilot': {
                        'cluster_name': name
                    }})
                instance = instances.get(instance_id)
                if not instance:
                    raise HyperbolicError(
                        f'Instance {instance_id} not found after launch')

                ssh_command = instance.get('sshCommand')
                if not ssh_command:
                    raise HyperbolicError(
                        'No SSH command available for instance')

                logger.info(f'Instance {instance_id} is ready with SSH command')
                return instance_id, ssh_command

        except Exception as e:
            logger.error(f'Failed to launch instance: {str(e)}')
            raise HyperbolicError(f'Failed to launch instance: {str(e)}') from e

    def _parse_instance(self,
                        instance: Dict[str, Any],
                        is_ondemand: bool = False) -> Optional[Dict[str, Any]]:
        """Parse an instance from the API response."""
        if is_ondemand:
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
                'status': HyperbolicInstanceStatus.from_raw_status(
                    current_status).value,
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
        else:
            # SPOT instance format
            instance_info = instance.get('instance', {})
            current_status = instance_info.get('status')
            gpu_model = instance_info.get('gpu_model', '')

            try:
                instance_status = HyperbolicInstanceStatus.from_raw_status(
                    current_status)
            except HyperbolicError:
                return None

            hardware = instance_info.get('hardware', {})
            return {
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
                'metadata': instance.get('userMetadata', {}),
                'gpu_model': gpu_model,
                'is_ondemand': False
            }

    def _filter_by_metadata(self, instance: Dict[str, Any],
                            metadata: Optional[Dict[str, Dict[str, str]]],
                            is_ondemand: bool) -> bool:
        """Filter instances based on metadata."""
        if not metadata:
            return True

        cluster_name = metadata.get('skypilot', {}).get('cluster_name', '')
        if is_ondemand:
            instance_skypilot = instance.get('meta',
                                             {}).get('userMetadata',
                                                     {}).get('skypilot', {})
        else:
            instance_skypilot = instance.get('userMetadata',
                                             {}).get('skypilot', {})

        return instance_skypilot.get('cluster_name',
                                     '').startswith(cluster_name)

    def _get_instances_from_endpoint(
        self,
        endpoint: str,
        is_ondemand: bool,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Dict[str, str]]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get instances from a specific endpoint."""
        instances = {}
        try:
            response = self._make_request('GET', endpoint)
            instances_list = response if is_ondemand else response.get(
                'instances', [])
            for instance in instances_list:
                if not isinstance(instance, dict):
                    continue
                parsed = self._parse_instance(instance, is_ondemand=is_ondemand)
                if parsed and self._filter_by_metadata(
                        instance, metadata, is_ondemand=is_ondemand):
                    if not status or parsed['status'] == status.lower():
                        instances[parsed['id']] = parsed
        except HyperbolicError as e:
            endpoint_type = 'on-demand' if is_ondemand else 'SPOT'
            logger.warning(f'Failed to get {endpoint_type} instances: {str(e)}')
        return instances

    def list_instances(
        self,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Dict[str, str]]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """List all instances, optionally filtered by status and metadata."""
        instances = {}
        instances.update(
            self._get_instances_from_endpoint(SPOT_ENDPOINTS['list'], False,
                                              status, metadata))
        instances.update(
            self._get_instances_from_endpoint(ONDEMAND_ENDPOINTS['list'], True,
                                              status, metadata))
        return instances

    def terminate_instance(self, instance_id: str) -> None:
        """Terminate an instance by ID."""
        try:
            instances = self.list_instances()
            instance = instances.get(instance_id)
            gpu_model = instance.get('gpu_model', '') if instance else ''
            endpoints = get_endpoints(gpu_model)

            endpoint_type = 'on-demand' if is_ondemand_gpu(
                gpu_model) else 'SPOT'
            logger.info(
                f'Using {endpoint_type} terminate endpoint for {gpu_model}')

            data = {
                'rentalId' if is_ondemand_gpu(gpu_model) else 'id': instance_id
            }
            self._make_request('POST', endpoints['terminate'], payload=data)
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
