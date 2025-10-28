"""Mithril API utilities."""
import enum
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
import yaml

# Unused: from sky import authentication
from sky import sky_logging
from sky.utils import status_lib

BASE_URL = 'https://api.mithril.ai'
FLOW_CONFIG_PATH = '~/.flow/config.yaml'
FLOW_METADATA_PATH = '~/.flow/keys/metadata.json'

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
TIMEOUT = 600  # 10 minutes - Mithril can be slow to provision spot instances

logger = sky_logging.init_logger(__name__)


class MithrilError(Exception):
    """Base exception for Mithril API errors."""
    pass


class MithrilInstanceStatus(enum.Enum):
    """Statuses enum for Mithril instances."""
    UNKNOWN = 'unknown'
    RUNNING = 'running'
    STOPPED = 'stopped'
    STARTING = 'starting'
    STOPPING = 'stopping'
    PENDING = 'pending'
    CREATING = 'creating'
    FAILED = 'failed'
    ERROR = 'error'
    TERMINATED = 'terminated'

    @classmethod
    def cluster_status_map(
        cls
    ) -> Dict['MithrilInstanceStatus', Optional[status_lib.ClusterStatus]]:
        return {
            cls.CREATING: status_lib.ClusterStatus.INIT,
            cls.STARTING: status_lib.ClusterStatus.INIT,
            cls.PENDING: status_lib.ClusterStatus.INIT,
            cls.RUNNING: status_lib.ClusterStatus.UP,
            cls.FAILED: status_lib.ClusterStatus.INIT,
            cls.ERROR: status_lib.ClusterStatus.INIT,
            cls.STOPPING: status_lib.ClusterStatus.INIT,
            cls.UNKNOWN: status_lib.ClusterStatus.INIT,
            cls.STOPPED: None,
            cls.TERMINATED: None,
        }

    @classmethod
    def from_raw_status(cls, status: str) -> 'MithrilInstanceStatus':
        """Convert raw status string to MithrilInstanceStatus enum."""
        # Map Mithril API statuses (with STATUS_ prefix) to enum values
        status_map = {
            'status_running': cls.RUNNING,
            'status_stopped': cls.STOPPED,
            'status_confirmed': cls.PENDING,  # Bid confirmed
            'status_initializing': cls.STARTING,  # Instance initializing
            'status_starting': cls.STARTING,  # Instance starting
            'status_terminating': cls.STOPPING,
            'status_terminated': cls.TERMINATED,
            'status_pending': cls.PENDING,
            'status_creating': cls.CREATING,
            'status_failed': cls.FAILED,
            'status_error': cls.ERROR,
        }

        status_lower = status.lower()
        if status_lower in status_map:
            return status_map[status_lower]

        # Try direct enum match for legacy formats
        try:
            return cls(status_lower)
        except ValueError:
            logger.warning(
                f'Unknown instance status: {status}, treating as UNKNOWN')
            return cls.UNKNOWN

    def to_cluster_status(self) -> Optional[status_lib.ClusterStatus]:
        """Convert to SkyPilot cluster status."""
        return self.cluster_status_map().get(self)


class MithrilClient:
    """Client for interacting with the Mithril API."""

    def __init__(self):
        """Initialize the Mithril client with API credentials from ~/.flow/."""
        config_path = os.path.expanduser(FLOW_CONFIG_PATH)
        if not os.path.exists(config_path):
            raise RuntimeError(f'Mithril config not found at {config_path}. '
                               f'Please run: flow setup')

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # Get API key
        self.api_key = config.get('api_key')
        if not self.api_key:
            raise RuntimeError(f'API key not found in {config_path}. '
                               f'Please run: flow setup')

        # Get project ID from metadata.json (more reliable than project name)
        metadata_path = os.path.expanduser(FLOW_METADATA_PATH)
        if os.path.exists(metadata_path):
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            # Get project ID from the first SSH key entry
            for key_data in metadata.values():
                project_id = key_data.get('project')
                if project_id:
                    self.project = project_id
                    break
            else:
                # Fallback to project name from config
                mithril_config = config.get('mithril', {})
                self.project = mithril_config.get('project')
        else:
            # Fallback to project name from config
            mithril_config = config.get('mithril', {})
            self.project = mithril_config.get('project')

        if not self.project:
            raise RuntimeError(
                f'Project not found in {config_path} or {metadata_path}. '
                f'Please run: flow setup')

        self.headers = {'Authorization': f'Bearer {self.api_key}'}
        self.api_url = BASE_URL

        logger.info(f'Initialized Mithril client for project: {self.project}')

    def _make_request(
            self,
            method: str,
            endpoint: str,
            payload: Optional[Dict[str, Any]] = None,
            params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make an API request to Mithril."""
        url = f'{BASE_URL}{endpoint}'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        # Debug logging for reques
        logger.debug(f'Making {method} request to {url}')
        if payload:
            logger.debug(f'Request payload: {json.dumps(payload, indent=2)}')
        if params:
            logger.debug(f'Request params: {json.dumps(params, indent=2)}')

        try:
            if method == 'GET':
                response = requests.get(url,
                                        headers=headers,
                                        params=params,
                                        timeout=120)
            elif method == 'POST':
                response = requests.post(url,
                                         headers=headers,
                                         json=payload,
                                         params=params,
                                         timeout=120)
            elif method == 'DELETE':
                response = requests.delete(url,
                                           headers=headers,
                                           json=payload,
                                           params=params,
                                           timeout=120)
            else:
                raise MithrilError(f'Unsupported HTTP method: {method}')

            # Debug logging for response
            logger.debug(f'Response status code: {response.status_code}')
            logger.debug(f'Response headers: {dict(response.headers)}')

            # Try to parse response as JSON
            try:
                response_data = response.json()
                logger.debug(
                    f'Response body: {json.dumps(response_data, indent=2)}')
            except json.JSONDecodeError as exc:
                # If response is not JSON, use the raw text
                response_text = response.text
                logger.debug(f'Response body (raw): {response_text}')
                if not response.ok:
                    raise MithrilError(f'API request failed with status '
                                       f'{response.status_code}: '
                                       f'{response_text}') from exc
                # If response is OK but not JSON, return empty dic
                return {}

            if not response.ok:
                error_msg = response_data.get(
                    'error', response_data.get('message', response.text))
                raise MithrilError(
                    f'API request failed with status {response.status_code}: '
                    f'{error_msg}')

            return response_data
        except requests.exceptions.RequestException as e:
            raise MithrilError(f'Request failed: {str(e)}') from e
        except Exception as e:
            raise MithrilError(
                f'Unexpected error during API request: {str(e)}') from e

    def _get_instance_type_fid(self, instance_type_name: str) -> str:
        """Get the FID for an instance type by name.

        Converts SkyPilot-style names (e.g., 4xa100) to Mithril names
        (e.g., a100-80gb.sxm.4x).
        """
        # Convert SkyPilot format to Mithril forma
        # e.g., 4xa100 -> a100-80gb.sxm.4x
        mithril_name = instance_type_name
        if 'xa100' in instance_type_name.lower():
            count = instance_type_name.lower().replace('xa100', '')
            mithril_name = f'a100-80gb.sxm.{count}x'
        elif 'xh100' in instance_type_name.lower():
            count = instance_type_name.lower().replace('xh100', '')
            mithril_name = f'h100-80gb.sxm.{count}x'

        endpoint = '/v2/instance-types'
        try:
            response = self._make_request('GET', endpoint)
            # Response can be a list or dict with 'data' key
            if isinstance(response, dict):
                instance_types = response.get('data', [])
            else:
                instance_types = response

            for it in instance_types:
                if isinstance(it, dict) and it.get('name') == mithril_name:
                    fid = it.get('fid')
                    if fid:
                        logger.info(
                            f'Found instance type: {mithril_name} -> {fid}')
                        return str(fid)
            raise MithrilError(f'Instance type {mithril_name} (from '
                               f'{instance_type_name}) not found')
        except Exception as e:
            raise MithrilError(
                f'Failed to get instance type FID: {str(e)}') from e

    def _get_or_create_ssh_key(self,
                               public_key: Optional[str] = None) -> List[str]:
        """Get or create SSH key for instances.

        Args:
            public_key: The public SSH key content to upload. If not provided,
                       will use the first existing key.
        """
        endpoint = '/v2/ssh-keys'
        params = {'project': self.project}
        try:
            # Get existing SSH keys
            response = self._make_request('GET', endpoint, params=params)
            # Normalize response to lis
            if isinstance(response, dict):
                key_list = response.get('data', [])
            else:
                key_list = response

            # If a specific public key is requested, check if it exists
            if public_key:
                # Extract key content (remove 'ssh-rsa ' prefix and
                # user@host suffix if present)
                key_parts = public_key.strip().split()
                if len(key_parts) >= 2:
                    key_content = f'{key_parts[0]} {key_parts[1]}'
                else:
                    key_content = public_key.strip()

                # Check if this key already exists
                for key in key_list:
                    if not isinstance(key, dict):
                        continue
                    existing_key = str(key.get('public_key', '')).strip()
                    if (key_content in existing_key or
                            existing_key in key_content):
                        ssh_key_fid = key.get('fid')
                        if ssh_key_fid:
                            logger.info(
                                f'Found matching SSH key: {ssh_key_fid}')
                            return [str(ssh_key_fid)]

                # Key doesn't exist, create i
                logger.info('Uploading new SSH key to Mithril')
                create_payload = {
                    'project': self.project,
                    'name': f'skypilot-{int(time.time())}',
                    'public_key': public_key.strip()
                }
                create_response = self._make_request('POST',
                                                     endpoint,
                                                     payload=create_payload)
                ssh_key_fid = create_response.get('fid')
                if not ssh_key_fid:
                    raise MithrilError(
                        'Failed to create SSH key - no FID returned')
                logger.info(f'Created new SSH key: {ssh_key_fid}')
                return [str(ssh_key_fid)]

            # No specific key requested, use first existing key
            if key_list and len(key_list) > 0:
                first_key = key_list[0]
                if isinstance(first_key, dict):
                    ssh_key_fid = first_key.get('fid')
                    if ssh_key_fid:
                        logger.info(f'Using existing SSH key: {ssh_key_fid}')
                        return [str(ssh_key_fid)]

            # No keys exist at all
            raise MithrilError('No SSH keys found in Mithril account. '
                               'Please create one using: flow ssh-key create')
        except MithrilError:
            raise
        except Exception as e:
            raise MithrilError(
                f'Failed to get/create SSH keys: {str(e)}') from e

    def _wait_for_bid_instance(self,
                               bid_id: str,
                               timeout: int = 300) -> Optional[str]:
        """Wait for a spot bid to create an instance."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            instances = self.list_instances()
            for instance_id, instance in instances.items():
                if instance.get('bid') == bid_id:
                    logger.info(
                        f'Found instance {instance_id} for bid {bid_id}')
                    return instance_id
            time.sleep(5)
        return None

    def launch_instance(self,
                        instance_type: str,
                        name: str,
                        region: Optional[str] = None,
                        public_key: Optional[str] = None) -> Tuple[str, str]:
        """Launch a new instance by creating a spot bid.

        Args:
            instance_type: The instance type to launch
            name: The name for the instance
            region: The region to launch in (defaults to us-central1-b)
            public_key: The SSH public key to upload/use for SSH access
        """
        # Get instance type FID from name
        instance_type_fid = self._get_instance_type_fid(instance_type)

        # Get or create SSH key for the instance
        ssh_keys = self._get_or_create_ssh_key(public_key)

        # Add timestamp to name to ensure uniqueness
        unique_name = f'{name}-{int(time.time())}'

        # Use provided region or select a default from availability API for
        # this instance type
        if region is None or region == 'default':
            try:
                availability = self._make_request('GET',
                                                  '/v2/spot/availability')
                # Normalize into a mapping and pick the first available region
                # for the instance_type
                records = availability.get('data', availability)
                candidate_region: Optional[str] = None
                if isinstance(records, list):
                    for rec in records:
                        inst = rec.get('instance_type') or rec.get(
                            'name') or rec.get('type')
                        if inst == instance_type and rec.get('region'):
                            candidate_region = rec['region']
                            break
                elif isinstance(records, dict):
                    per_region = records.get(instance_type)
                    if isinstance(per_region, dict) and per_region:
                        candidate_region = next(iter(per_region.keys()))
                if candidate_region:
                    region = candidate_region
                else:
                    # Fallback: keep previous default if nothing found
                    region = 'us-central1-b'
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    'Falling back to default region due to availability '
                    'lookup failure: %s', e)
                region = 'us-central1-b'

        # Create spot bid payload according to Mithril API
        startup_script = '#!/bin/bash\necho "SkyPilot instance started"'
        bid_payload = {
            'project': self.project,
            'region': region,
            'instance_type': instance_type_fid,
            'limit_price': '$8.00',  # Default bid price limi
            'instance_quantity': 1,
            'name': unique_name,
            'launch_specification': {
                'ssh_keys': ssh_keys,
                'volumes': [],  # No volumes for now
                'startup_script': startup_script
            }
        }

        endpoint = '/v2/spot/bids'
        try:
            logger.info(f'Creating spot bid for {instance_type} '
                        f'({instance_type_fid})')
            response = self._make_request('POST', endpoint, payload=bid_payload)
            logger.debug(f'Spot bid response: {json.dumps(response, indent=2)}')

            bid_id = response.get('fid')
            if not bid_id:
                logger.error(f'No bid ID in response: {response}')
                raise MithrilError('No bid ID returned from API')

            logger.info(f'Successfully created spot bid {bid_id}, '
                        f'waiting for instance...')

            # Wait for the bid to create an instance
            instance_id = self._wait_for_bid_instance(bid_id, timeout=300)

            if not instance_id:
                raise MithrilError(f'Bid {bid_id} did not create an instance')

            # Wait for instance to be ready
            if not self.wait_for_instance(instance_id,
                                          MithrilInstanceStatus.RUNNING.value):
                raise MithrilError(
                    f'Instance {instance_id} failed to reach RUNNING state')

            # Get instance details to get connection info
            instances = self.list_instances()
            instance = instances.get(instance_id)
            if not instance:
                raise MithrilError(
                    f'Instance {instance_id} not found after launch')

            # Get SSH connection info (ip_address is set from
            # ssh_destination in list_instances)
            ip_address = instance.get('ip_address')
            ssh_port = instance.get('ssh_port', 22)

            if not ip_address:
                logger.error(
                    f'No IP address available for instance {instance_id}')
                raise MithrilError('No IP address available for instance')

            ssh_command = f'ssh ubuntu@{ip_address} -p {ssh_port}'
            logger.info(
                f'Instance {instance_id} is ready with SSH: {ssh_command}')
            return instance_id, ssh_command

        except Exception as e:
            logger.error(f'Failed to launch instance: {str(e)}')
            raise MithrilError(f'Failed to launch instance: {str(e)}') from e

    def list_instances(
        self,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Dict[str, str]]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """List all instances, optionally filtered by status.

        Note: Mithril doesn't support user metadata, so metadata filtering
        is ignored.
        Filtering by cluster name should be done by instance name instead.
        """
        endpoint = '/v2/instances'
        # Try with project parameter - URL encode in case of spaces
        params = {'project': quote(self.project)} if self.project else {}
        try:
            response = self._make_request('GET', endpoint, params=params)
            logger.debug(f'Raw API response: {json.dumps(response, indent=2)}')
            instances = {}

            # Metadata filtering is ignored for Mithril - it's handled in
            # instance.py
            # by filtering on instance name instead
            if metadata:
                logger.debug(
                    'Note: Mithril does not support metadata filtering. '
                    'Filtering by instance name should be done by caller.')

            # Mithril API returns instances in a 'data' key
            instance_list = response.get('data', [])
            for instance in instance_list:
                instance_id = instance.get('fid')
                current_status = instance.get('status')
                logger.debug(f'Instance {instance_id} status: {current_status}')

                # Convert raw status to enum
                try:
                    instance_status = MithrilInstanceStatus.from_raw_status(
                        current_status)
                except MithrilError as e:
                    logger.warning(f'Failed to parse status for instance '
                                   f'{instance_id}: {e}')
                    continue

                if status and instance_status.value != status.lower():
                    continue

                # Get IP address from Mithril's ssh_destination field
                ssh_dest = instance.get('ssh_destination')

                instances[instance_id] = {
                    'id': instance_id,
                    'name': instance.get('name'),  # Include name for filtering
                    'status': instance_status.value,
                    'ip_address': ssh_dest,  # Public IP from ssh_destination
                    'ssh_port': 22,
                    'instance_type': instance.get('instance_type'),
                    'created_at': instance.get('created_at'),
                    'bid':
                        instance.get('bid'),  # Include bid ID for termination
                    'private_ip': instance.get('private_ip')
                }
            return instances
        except Exception as e:
            raise MithrilError(f'Failed to list instances: {str(e)}') from e

    def terminate_instance(self, instance_id: str) -> None:
        """Terminate an instance by canceling its spot bid."""
        # First, get the instance to find its bid ID
        try:
            instances = self.list_instances()
            instance = instances.get(instance_id)
            if not instance:
                logger.warning(
                    f'Instance {instance_id} not found, may already be '
                    f'terminated')
                return

            bid_id = instance.get('bid')
            if not bid_id:
                raise MithrilError(
                    f'No bid ID found for instance {instance_id}')

            # Cancel the spot bid to terminate the instance
            endpoint = f'/v2/spot/bids/{bid_id}'
            params = {'project': self.project}
            logger.info(f'Canceling bid {bid_id} for instance {instance_id}')
            self._make_request('DELETE', endpoint, params=params)
            logger.info(f'Successfully canceled bid {bid_id}')
        except MithrilError:
            raise
        except Exception as e:
            raise MithrilError(
                f'Failed to terminate instance {instance_id}: {str(e)}') from e

    def wait_for_instance(self,
                          instance_id: str,
                          target_status: str,
                          timeout: int = TIMEOUT) -> bool:
        """Wait for an instance to reach a specific status.

        Mithril instances go through several states:
        - STATUS_CONFIRMED: Bid accepted, instance allocation confirmed
        - STATUS_INITIALIZING: Instance is being set up
        - STATUS_STARTING: Instance is starting
        - STATUS_RUNNING: Instance is ready
        """
        start_time = time.time()
        target_status_enum = MithrilInstanceStatus.from_raw_status(
            target_status)
        logger.info(f'Waiting for instance {instance_id} '
                    f'to reach status {target_status_enum.value} '
                    f'(timeout: {timeout}s)')

        # Track if we've seen any progress
        last_status = None

        while True:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                logger.error(f'Timeout after {int(elapsed)}s '
                             f'waiting for instance {instance_id}. '
                             f'Last status: {last_status}')
                return False

            try:
                instances = self.list_instances()
                instance = instances.get(instance_id)

                if not instance:
                    logger.warning(f'Instance {instance_id} not found, '
                                   f'may still be initializing...')
                    time.sleep(5)
                    continue

                current_status = instance.get('status', '').lower()
                ip_address = instance.get('ip_address')

                # Track status changes to show progress
                if current_status != last_status:
                    logger.info(
                        f'Instance {instance_id} status: {last_status} -> '
                        f'{current_status} (elapsed: {int(elapsed)}s)')
                    last_status = current_status
                else:
                    # Log periodically even if status hasn't changed
                    if int(elapsed) % 30 == 0:
                        logger.info(f'Still waiting... Current status: '
                                    f'{current_status} '
                                    f'(elapsed: {int(elapsed)}s/{timeout}s)')

                # For Mithril, once we have an IP address, the instance is
                # ready for SSH. We don't need to wait for STATUS_RUNNING -
                # having the ssh_destination
                # means the instance is allocated and networking is configured
                if ip_address:
                    logger.info(
                        f'✓ Instance {instance_id} has IP {ip_address} '
                        f'(status: {current_status}) after {int(elapsed)}s - '
                        f'ready for SSH')
                    return True

                # If no IP yet, check if we've reached the target status anyway
                if current_status == target_status_enum.value:
                    logger.debug(
                        f'Instance reached {target_status_enum.value} but no '
                        f'IP yet, continuing to wait...')
                    # Continue waiting for IP address

                # Check for terminal failure states
                if current_status in ['failed', 'error', 'terminated']:
                    logger.error(f'✗ Instance {instance_id} reached '
                                 f'terminal status: {current_status} '
                                 f'after {int(elapsed)}s')
                    return False

                # Valid intermediate states (keep waiting)
                valid_pending = [
                    'pending', 'creating', 'starting', 'confirmed',
                    'initializing'
                ]
                if (current_status not in valid_pending and
                        current_status != target_status_enum.value):
                    logger.warning(
                        f'Instance in unexpected status: {current_status}')

                time.sleep(5)
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    f'Error while waiting for instance {instance_id}: '
                    f'{str(e)}')
                time.sleep(5)


# Module-level singleton client
_client = None


def get_client() -> MithrilClient:
    """Get or create the Mithril client singleton."""
    global _client
    if _client is None:
        _client = MithrilClient()
    return _client


# Backward-compatible wrapper functions
def launch_instance(instance_type: str,
                    name: str,
                    region: Optional[str] = None,
                    public_key: Optional[str] = None) -> Tuple[str, str]:
    """Launch a new instance with the specified configuration."""
    return get_client().launch_instance(instance_type, name, region, public_key)


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
