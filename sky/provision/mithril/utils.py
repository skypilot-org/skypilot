"""Mithril API utilities."""

import json
import os
import time
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import quote

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.utils import common_utils
from sky.utils import status_lib

# Mithril API status values
MithrilStatus = Literal['STATUS_NEW', 'STATUS_CONFIRMED', 'STATUS_SCHEDULED',
                        'STATUS_INITIALIZING', 'STATUS_STARTING',
                        'STATUS_RUNNING', 'STATUS_STOPPING', 'STATUS_STOPPED',
                        'STATUS_TERMINATED', 'STATUS_RELOCATING',
                        'STATUS_PREEMPTING', 'STATUS_PREEMPTED',
                        'STATUS_REPLACED', 'STATUS_PAUSED', 'STATUS_ERROR',]

# Lazy imports to avoid dependency issues
requests = adaptors_common.LazyImport('requests')
yaml = adaptors_common.LazyImport('yaml')

DEFAULT_API_URL = 'https://api.mithril.ai'

# Environment variable names for config overrides
ENV_API_KEY = 'MITHRIL_API_KEY'
ENV_PROJECT = 'MITHRIL_PROJECT'
ENV_API_URL = 'MITHRIL_API_URL'
ENV_PROFILE = 'MITHRIL_PROFILE'

TIMEOUT = 3600

# Retry configuration for API requests
INITIAL_BACKOFF_SECONDS = 5
MAX_BACKOFF_FACTOR = 10
MAX_ATTEMPTS = 6

logger = sky_logging.init_logger(__name__)


def get_credentials_path() -> str:
    """Get the path to the Mithril credentials file.

    Respects XDG_CONFIG_HOME, otherwise defaults to
    ~/.config/mithril/config.yaml
    """
    xdg_config_home = os.environ.get('XDG_CONFIG_HOME')
    if xdg_config_home:
        return os.path.join(xdg_config_home, 'mithril', 'config.yaml')
    return '~/.config/mithril/config.yaml'


class MithrilError(Exception):
    """Base exception for Mithril API errors."""

    pass


class MithrilHttpError(MithrilError):
    """HTTP error from Mithril API with status code."""

    def __init__(self, message: str, status_code: int):
        self.status_code = status_code
        super().__init__(message)


def to_cluster_status(
        raw_status: MithrilStatus) -> Optional[status_lib.ClusterStatus]:
    """Map Mithril API status to ClusterStatus.

    Returns None for terminated instances so they are filtered out when
    query_instances is called with non_terminated_only=True.
    """
    mapping: Dict[MithrilStatus, Optional[status_lib.ClusterStatus]] = {
        'STATUS_NEW': status_lib.ClusterStatus.INIT,
        'STATUS_CONFIRMED': status_lib.ClusterStatus.INIT,
        'STATUS_SCHEDULED': status_lib.ClusterStatus.INIT,
        'STATUS_INITIALIZING': status_lib.ClusterStatus.INIT,
        'STATUS_STARTING': status_lib.ClusterStatus.INIT,
        'STATUS_RUNNING': status_lib.ClusterStatus.UP,
        'STATUS_STOPPING': status_lib.ClusterStatus.UP,
        'STATUS_STOPPED': status_lib.ClusterStatus.STOPPED,
        'STATUS_TERMINATED': None,  # Fully terminated
        'STATUS_RELOCATING':
            status_lib.ClusterStatus.UP,  # Still running during notice period
        'STATUS_PREEMPTING':
            status_lib.ClusterStatus.UP,  # Still running during notice period
        'STATUS_PREEMPTED': status_lib.ClusterStatus.STOPPED,
        'STATUS_REPLACED': None,  # Replaced, treat as terminated
        'STATUS_PAUSED': status_lib.ClusterStatus.STOPPED,
        'STATUS_ERROR':
            status_lib.ClusterStatus.STOPPED,  # Error but can recover
    }
    return mapping.get(raw_status)


def _load_file_config() -> Optional[Dict[str, Any]]:
    """Load Mithril config file if present.

    Returns:
        Parsed config dict if file exists, None if file not found.
    """
    config_path = os.path.expanduser(get_credentials_path())
    if not os.path.exists(config_path):
        logger.debug(f'Mithril config file not found at {config_path}')
        return None
    logger.debug(f'Loading Mithril config from {config_path}')
    with open(config_path, 'r', encoding='utf-8') as f:
        try:
            # safe_load returns None for empty files; normalize to {}.
            return yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise MithrilError(
                f'Failed to parse Mithril config at {config_path}: '
                f'{e}') from e


def get_current_profile() -> Optional[str]:
    """Get the currently active profile.

    Returns the profile from MITHRIL_PROFILE env var or current_profile
    in the config file. Returns None if no profile is configured.
    """
    profile = os.environ.get(ENV_PROFILE)
    if profile:
        return profile
    file_config = _load_file_config()
    if file_config is not None:
        return file_config.get('current_profile')
    return None


def _build_config(api_key: Optional[str], project_id: Optional[str],
                  api_url: Optional[str]) -> Dict[str, str]:
    """Validate required fields and return a Mithril config dict.

    Raises MithrilError if api_key or project_id is missing. Falls back
    to DEFAULT_API_URL when api_url is not provided.
    """
    if not api_key:
        raise MithrilError(f'Mithril API key not found. '
                           f'Set {ENV_API_KEY} or run `sky check mithril` '
                           'for setup instructions.')
    if not project_id:
        raise MithrilError(f'Mithril project ID not found. '
                           f'Set {ENV_PROJECT} or run `sky check mithril` '
                           'for setup instructions.')
    return {
        'api_key': api_key,
        'project_id': project_id,
        'api_url': api_url or DEFAULT_API_URL,
    }


def resolve_current_config() -> Dict[str, str]:
    """Resolve Mithril config from environment variables and active profile.

    Environment variables take precedence over profile values. Works without
    a config file if all required env vars are set.
    """
    file_config = _load_file_config()
    profile = get_current_profile()
    profile_config: Dict[str, Any] = {}
    if profile and file_config is not None:
        profiles = file_config.get('profiles', {})
        if profile not in profiles:
            available = list(profiles.keys()) or '(none)'
            raise MithrilError(f'Mithril profile \'{profile}\' not found. '
                               f'Available profiles: {available}.')
        profile_config = profiles[profile]

    api_key = os.environ.get(ENV_API_KEY) or profile_config.get('api_key')
    project_id = os.environ.get(ENV_PROJECT) or profile_config.get('project_id')
    api_url = os.environ.get(ENV_API_URL) or profile_config.get('api_url')
    return _build_config(api_key, project_id, api_url)


def get_profile_config(profile: str) -> Dict[str, str]:
    """Get Mithril config for a named profile from the config file.

    Raises MithrilError if the config file or profile is not found.
    """
    file_config = _load_file_config()
    if file_config is None:
        raise MithrilError(
            f'Config file not found at {get_credentials_path()}. '
            f'Cannot resolve profile \'{profile}\'.')

    profiles = file_config.get('profiles', {})
    if profile not in profiles:
        available = list(profiles.keys()) or '(none)'
        raise MithrilError(f'Mithril profile \'{profile}\' not found. '
                           f'Available profiles: {available}.')
    profile_config = profiles[profile]
    return _build_config(profile_config.get('api_key'),
                         profile_config.get('project_id'),
                         profile_config.get('api_url'))


def _is_retryable_status(status_code: int) -> bool:
    """Check if the HTTP status code is retryable."""
    # Retry on rate limiting (429) and server errors (5xx)
    return status_code == 429 or 500 <= status_code < 600


def _make_request(
    method: str,
    endpoint: str,
    payload: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, str]] = None,
) -> Any:
    """Make an API request to Mithril with retry and backoff."""
    if config is None:
        config = resolve_current_config()
    base_url = config['api_url']
    url = f'{base_url}{endpoint}'
    headers = {
        'Authorization': f'Bearer {config["api_key"]}',
        'Content-Type': 'application/json',
    }

    logger.debug(f'Making {method} request to {url}')
    if payload:
        logger.debug(f'Request payload: {json.dumps(payload, indent=2)}')
    if params:
        logger.debug(f'Request params: {json.dumps(params, indent=2)}')

    backoff = common_utils.Backoff(initial_backoff=INITIAL_BACKOFF_SECONDS,
                                   max_backoff_factor=MAX_BACKOFF_FACTOR)
    last_exception: Optional[Exception] = None

    for attempt in range(MAX_ATTEMPTS):
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
            elif method == 'PATCH':
                response = requests.patch(url,
                                          headers=headers,
                                          json=payload,
                                          params=params,
                                          timeout=120)
            else:
                raise MithrilError(f'Unsupported HTTP method: {method}')

            logger.debug(f'Response status code: {response.status_code}')

            # Check if we should retry based on status code
            if (_is_retryable_status(response.status_code) and
                    attempt < MAX_ATTEMPTS - 1):
                sleep_time = backoff.current_backoff()
                logger.debug(
                    f'Request failed with status {response.status_code}, '
                    f'retrying in {sleep_time:.1f}s '
                    f'(attempt {attempt + 1}/{MAX_ATTEMPTS})')
                time.sleep(sleep_time)
                continue

            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = {}

            if not response.ok:
                raise MithrilHttpError(
                    f'API request failed with status '
                    f'{response.status_code}: {response.text}',
                    status_code=response.status_code,
                )

            return response_data

        except requests.exceptions.RequestException as e:
            last_exception = e
            if attempt < MAX_ATTEMPTS - 1:
                sleep_time = backoff.current_backoff()
                logger.debug(
                    f'Request failed with {e}, retrying in {sleep_time:.1f}s '
                    f'(attempt {attempt + 1}/{MAX_ATTEMPTS})')
                time.sleep(sleep_time)
                continue
            raise MithrilError(f'Request failed: {str(e)}') from e

    # If we exhausted all retries due to retryable status codes
    raise MithrilError(f'Request failed after {MAX_ATTEMPTS} attempts. '
                       f'Last error: {last_exception}')


def get_or_add_ssh_key(
    public_key: str,
    config: Optional[Dict[str, str]] = None,
) -> str:
    """Get or create a Mithril SSH key and return its key ID."""
    if config is None:
        config = resolve_current_config()
    endpoint = '/v2/ssh-keys'
    params = {'project': config['project_id']}

    # Get existing SSH keys
    key_list: List[Dict[str, Any]] = _make_request('GET',
                                                   endpoint,
                                                   params=params)

    # Extract key type and key data (ignore comment if present)
    key_parts = public_key.strip().split()
    key_content = (f'{key_parts[0]} {key_parts[1]}'
                   if len(key_parts) >= 2 else public_key.strip())

    # Check if this key already exists
    for key in key_list:
        existing_key = key['public_key'].strip()
        if key_content in existing_key or existing_key in key_content:
            logger.debug(f'Found matching SSH key: {key["fid"]}')
            return key['fid']

    # Key doesn't exist, create it
    logger.debug('Uploading new SSH key to Mithril')

    # Include user name in the SSH key name
    user_name = common_utils.get_current_user_name()[:10]
    key_name = f'sky-{user_name}-{int(time.time())}'

    create_response = _make_request(
        'POST',
        endpoint,
        payload={
            'project': config['project_id'],
            'name': key_name,
            'public_key': public_key.strip(),
        },
    )
    logger.debug(f'Created new SSH key: {create_response["fid"]}')
    return create_response['fid']


def _wait_for_bid_instances(
    bid_name: str,
    expected_count: int,
    project_id: str,
    timeout: int = TIMEOUT,
) -> List[str]:
    """Wait for a spot bid to create the expected number of instances.

    Args:
        bid_name: The name of the bid (cluster name on cloud).
        expected_count: The expected number of instances to be created.
        project_id: The project ID the bid belongs to.
        timeout: Maximum time to wait in seconds.

    Returns:
        List of instance FIDs created by the bid.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            params = {'project': project_id, 'name': bid_name}
            response = _make_request('GET', '/v2/spot/bids', params=params)
            bids = response.get('data', [])

            if not bids:
                logger.debug(f'No bids found with name {bid_name}')
                time.sleep(5)
                continue

            # Get instances from the first matching bid
            bid = bids[0]
            instance_ids = bid['instances']

            if len(instance_ids) >= expected_count:
                logger.debug(
                    f'Found {len(instance_ids)} instances for bid {bid_name}')
                return instance_ids

            logger.debug(f'Waiting for instances: found '
                         f'{len(instance_ids)}/{expected_count}')
        except MithrilError as e:
            logger.debug(f'Error querying bid {bid_name}: {e}')

        time.sleep(5)
    return []


def wait_for_bid(
    bid_name: str,
    expected_count: int,
    project_id: str,
    bid_timeout: int = TIMEOUT,
    ssh_ip_timeout: int = TIMEOUT,
) -> List[str]:
    """Wait for a bid to create instances and for them to have SSH destinations.

    Args:
        bid_name: The name of the bid (unique cluster name).
        expected_count: The expected number of instances.
        project_id: The project ID the bid belongs to.
        bid_timeout: Timeout for waiting for bid to create instances.
        ssh_ip_timeout: Timeout for waiting for each instance to get SSH IP.

    Returns:
        List of instance IDs that are ready for SSH.

    Raises:
        MithrilError: If bid doesn't create enough instances or instances
            fail to get SSH destinations.
    """
    # Wait for the bid to create all instances
    instance_ids = _wait_for_bid_instances(bid_name,
                                           expected_count,
                                           project_id=project_id,
                                           timeout=bid_timeout)

    if len(instance_ids) < expected_count:
        raise MithrilError(
            f'Bid {bid_name} only created {len(instance_ids)} instances, '
            f'expected {expected_count}')

    # Wait for all instances to have SSH destination (IP address)
    for instance_id in instance_ids:
        if not wait_for_ssh_ip(instance_id, timeout=ssh_ip_timeout):
            raise MithrilError(
                f'Instance {instance_id} failed to get SSH destination')

    logger.debug(f'All {len(instance_ids)} instances have SSH destinations: '
                 f'{instance_ids}')
    return instance_ids


def list_instances(
    status: Optional[str] = None,
    config: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """List all instances, optionally filtered by status.

    Handles pagination using next_cursor if present in API response.

    Args:
        status: If provided, only return instances with this raw API status.
        config: Optional pre-resolved config dict with api_key, project_id,
            and api_url. If not provided, uses resolve_current_config().
    """
    if config is None:
        config = resolve_current_config()
    endpoint = '/v2/instances'
    base_params: Dict[str, Any] = ({
        'project': quote(config['project_id'])
    } if config['project_id'] else {})
    instances: Dict[str, Dict[str, Any]] = {}
    cursor: Optional[str] = None

    try:
        while True:
            params = base_params.copy()
            if cursor:
                params['next_cursor'] = cursor

            response = _make_request('GET',
                                     endpoint,
                                     params=params,
                                     config=config)
            logger.debug(f'Raw API response: {json.dumps(response, indent=2)}')

            instance_list = response.get('data', [])
            for instance in instance_list:
                instance_id = instance['fid']
                raw_status = instance['status']
                logger.debug(f'Instance {instance_id} status: {raw_status}')

                # Filter by status if provided (matches raw API status)
                if status and raw_status != status:
                    continue

                instances[instance_id] = {
                    'fid': instance_id,
                    'name': instance['name'],
                    'status': raw_status,
                    'ssh_destination': instance['ssh_destination'],
                    'instance_type': instance['instance_type'],
                    'created_at': instance['created_at'],
                    'bid': instance['bid'],
                    'private_ip': instance['private_ip'],
                    'region': instance['region'],
                }

            # Check for next page
            cursor = response.get('next_cursor')
            if not cursor:
                break

        return instances
    except Exception as e:
        raise MithrilError(f'Failed to list instances: {str(e)}') from e


def get_spot_availability() -> List[Dict[str, Any]]:
    """Get spot availability for all instance types.

    Returns:
        List of availability records. Each contains:
        fid, instance_type (FID), region, capacity,
        last_instance_price, lowest_allocated_price, etc.
    """
    return _make_request('GET', '/v2/spot/availability')


def get_instance_types() -> Dict[str, Dict[str, Any]]:
    """Get all instance types.

    Returns:
        Dict mapping instance type FID to instance type record.
        Each record contains: fid, name, num_cpus, gpu_type, num_gpus, etc.
        Note: The same name can appear with different FIDs (one per region).
    """
    instance_types: List[Dict[str,
                              Any]] = _make_request('GET', '/v2/instance-types')
    return {it['fid']: it for it in instance_types}


def launch_instances(
    instance_type: str,
    name: str,
    region: Optional[str],
    ssh_keys: List[str],
    instance_quantity: int = 1,
) -> Tuple[str, List[str]]:
    """Launch instances by creating a spot bid and waiting for them to be ready.

    Returns:
        Tuple of (bid_id, list of instance_ids)
    """
    config = resolve_current_config()

    # Look up instance type FIDs by name and find available regions
    instance_types = get_instance_types()
    availability = get_spot_availability()

    # Build region -> FID mapping for the requested instance type name
    region_to_fid = {
        rec['region']:
        rec['instance_type'] for rec in availability if instance_types.get(
            rec['instance_type'], {}).get('name') == instance_type
    }

    if not region_to_fid:
        raise MithrilError(
            f'Instance type not found or not available: {instance_type}')

    logger.debug(
        f'Available regions for {instance_type}: {list(region_to_fid.keys())}')

    # Use provided region or select from available regions
    if region is None or region == 'default':
        region = next(iter(region_to_fid))
        logger.debug(f'Auto-selected region {region} for {instance_type}')
    elif region not in region_to_fid:
        raise MithrilError(
            f'Instance type \'{instance_type}\' is not available in '
            f'region \'{region}\'. '
            f'Available regions: {list(region_to_fid.keys())}')

    instance_type_fid = region_to_fid[region]
    logger.debug(
        f'Using {instance_type} (FID: {instance_type_fid}) in {region}')

    bid_payload = {
        'project': config['project_id'],
        'region': region,
        'instance_type': instance_type_fid,
        # TODO(oliviert): Support configurable limit price
        'limit_price': '$32.00',
        'instance_quantity': instance_quantity,
        'name': name,
        'launch_specification': {
            'ssh_keys': ssh_keys,
            # TODO(oliviert): Support volumes
            'volumes': [],
        },
    }

    endpoint = '/v2/spot/bids'
    try:
        logger.debug(
            f'Creating spot bid for {instance_type} ({instance_type_fid})')
        response = _make_request('POST', endpoint, payload=bid_payload)
        logger.debug(f'Spot bid response: {json.dumps(response, indent=2)}')

        bid_id = response['fid']

        logger.debug(f'Successfully created spot bid {bid_id}, '
                     f'waiting for {instance_quantity} instance(s)...')

        # Wait for bid to create instances and for them to have SSH destinations
        instance_ids = wait_for_bid(name,
                                    instance_quantity,
                                    project_id=config['project_id'])
        return bid_id, instance_ids

    except MithrilError as e:
        logger.error(f'Failed to launch instance: {str(e)}')
        raise


def get_bid(
    bid_name: str,
    config: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Get a bid by its exact name.

    Args:
        bid_name: The exact name of the bid to retrieve.
        config: Optional pre-resolved config dict. If not provided,
            uses resolve_current_config().

    Returns:
        Bid dictionary with fid, name, instances, etc., or None if not found.
    """
    if config is None:
        config = resolve_current_config()
    endpoint = '/v2/spot/bids'
    params = {
        'project': config['project_id'],
        'name': bid_name,
    }

    response = _make_request('GET', endpoint, params=params, config=config)
    bids = response.get('data', [])
    if not bids:
        logger.debug(f'No bid found with name {bid_name}')
        return None
    logger.debug(f'Found bid {bids[0].get("fid")} with name {bid_name}')
    return bids[0]


def cancel_bid(
    bid_id: str,
    config: Optional[Dict[str, str]] = None,
) -> bool:
    """Cancel a spot bid by its FID.

    Args:
        bid_id: The FID of the bid to cancel.
        config: Optional pre-resolved config dict. If not provided,
            uses resolve_current_config().

    Returns:
        True if the bid was successfully canceled or didn't exist (404),
        False otherwise.
    """
    if config is None:
        config = resolve_current_config()
    endpoint = f'/v2/spot/bids/{bid_id}'
    params = {'project': config['project_id']}

    try:
        logger.debug(f'Canceling bid {bid_id}')
        _make_request('DELETE', endpoint, params=params, config=config)
        logger.debug(f'Successfully canceled bid {bid_id}')
        return True
    except MithrilHttpError as e:
        if e.status_code == 404:
            logger.debug(f'Bid {bid_id} not found')
            return True
        raise


def update_bid(
    bid_id: str,
    paused: bool,
    config: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Update a spot bid to pause or unpause it.

    Args:
        bid_id: The FID of the bid to update.
        paused: True to pause the bid (stop instances), False to unpause
            (resume).
        config: Optional pre-resolved config dict. If not provided,
            uses resolve_current_config().

    Returns:
        The updated bid dictionary.

    Raises:
        MithrilError: If the update fails.
    """
    if config is None:
        config = resolve_current_config()
    endpoint = f'/v2/spot/bids/{bid_id}'
    params = {'project': config['project_id']}
    payload = {'paused': paused}

    logger.debug(f'Updating bid {bid_id}, paused: {paused}')
    response = _make_request('PATCH',
                             endpoint,
                             payload=payload,
                             params=params,
                             config=config)
    return response


def wait_for_ssh_ip(instance_id: str, timeout: int = TIMEOUT) -> bool:
    """Wait for an instance to have an SSH IP address (ssh_destination).

    Returns True once the instance has an IP address assigned, meaning it's
    ready for SSH connection attempts. The actual SSH readiness will be
    verified by wait_for_ssh in provisioner.py.
    """
    start_time = time.time()
    logger.debug(f'Waiting for instance {instance_id} to have SSH IP '
                 f'(timeout: {timeout}s)')

    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout:
            logger.error(f'Timeout after {int(elapsed)}s '
                         f'waiting for instance {instance_id} to get SSH IP')
            return False

        try:
            instances = list_instances()
            instance = instances.get(instance_id)

            if not instance:
                logger.debug(f'Instance {instance_id} not found, '
                             'may still be initializing...')
                time.sleep(5)
                continue

            ssh_destination = instance.get('ssh_destination')

            if ssh_destination:
                logger.debug(f'Instance {instance_id} has SSH destination '
                             f'{ssh_destination} after {int(elapsed)}s')
                return True

            if int(elapsed) % 30 == 0:
                logger.debug(f'Still waiting for SSH IP... '
                             f'(elapsed: {int(elapsed)}s/{timeout}s)')

            time.sleep(5)
        except MithrilError as e:
            logger.warning(
                f'Error while waiting for instance {instance_id}: {str(e)}')
            time.sleep(5)
