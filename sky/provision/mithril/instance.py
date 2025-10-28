"""Mithril instance provisioning."""
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.mithril import utils
from sky.utils import status_lib

PROVIDER_NAME = 'mithril'
POLL_INTERVAL = 5
QUERY_PORTS_TIMEOUT_SECONDS = 30
TIMEOUT = 600  # 10 minutes - Mithril can be slow to provision spot instances

logger = sky_logging.init_logger(__name__)


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]],
                      head_only: bool = False) -> Dict[str, Dict[str, Any]]:
    logger.debug(f'Filtering instances: cluster={cluster_name_on_cloud}, '
                 f'status={status_filters}')
    _ = head_only  # Mark as intentionally unused

    # Mithril doesn't support user metadata, so we filter by instance name
    # Instance names have format: {cluster_name_on_cloud}-{timestamp}-1
    instances = utils.list_instances()

    # Normalize status filters to lowercase
    if status_filters is not None:
        status_filters = [s.lower() for s in status_filters]

    filtered_instances: Dict[str, Dict[str, Any]] = {}
    for instance_id, instance in instances.items():
        try:
            # Filter by instance name (since Mithril doesn't support metadata)
            instance_name = instance.get('name', '')
            # Instance names start with cluster_name_on_cloud
            if not instance_name.startswith(cluster_name_on_cloud):
                logger.debug(f'Skipping instance {instance_id} - name '
                             f'{instance_name} does not start with '
                             f'{cluster_name_on_cloud}')
                continue

            # Check status filter
            instance_status = instance.get('status', '').lower()
            if (status_filters is not None and
                    instance_status not in status_filters):
                logger.debug(
                    f'Skipping instance {instance_id} '
                    f'- status {instance_status} not in {status_filters}')
                continue

            filtered_instances[instance_id] = instance
            logger.debug(f'Including instance {instance_id} '
                         f'with status {instance_status}')

        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Error processing instance {instance_id}: {str(e)}')
            continue

    logger.info(f'Found {len(filtered_instances)} instances matching filters')
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    """Get the instance ID from the instances dict."""
    if not instances:
        return None
    return next(iter(instances.keys()))


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    del cluster_name  # unused
    logger.info(f'Starting run_instances with region={region}, '
                f'cluster={cluster_name_on_cloud}')
    logger.debug(f'Config: {config}')
    start_time = time.time()

    # Define pending statuses for Mithril
    pending_status = [
        utils.MithrilInstanceStatus.CREATING.value,
        utils.MithrilInstanceStatus.STARTING.value,
        utils.MithrilInstanceStatus.PENDING.value
    ]
    logger.debug(
        f'Looking for instances with pending statuses: {pending_status}')

    # Wait for any pending instances to be ready
    while True:
        if time.time() - start_time > TIMEOUT:
            logger.error(
                f'Timed out after {TIMEOUT}s waiting for instances to be ready')
            raise TimeoutError(
                f'Timed out after {TIMEOUT}s waiting for instances to be ready')

        instances = _filter_instances(cluster_name_on_cloud, pending_status)
        logger.debug(f'Found {len(instances)} instances with pending status')
        if not instances:
            break
        logger.info(
            f'Waiting for instance to be ready. Current instances: {instances}')
        time.sleep(POLL_INTERVAL)

    # Check existing running instance
    logger.info('Checking for existing running instances')
    exist_instances = _filter_instances(
        cluster_name_on_cloud, [utils.MithrilInstanceStatus.RUNNING.value])
    logger.debug(
        f'Found {len(exist_instances)} running instances: {exist_instances}')
    instance_id = _get_head_instance_id(exist_instances)
    logger.debug(f'Head instance ID: {instance_id}')

    # Calculate if we need to start a new instance
    to_start_count = 1 - len(exist_instances)  # Always 1 for single node
    logger.info(f'Need to start {to_start_count} new instances')
    if to_start_count < 0:
        logger.error(
            f'Cluster {cluster_name_on_cloud} already has an instance running')
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has an instance running.')
    if to_start_count == 0:
        if instance_id is None:
            logger.error(
                f'Cluster {cluster_name_on_cloud} has no running instance')
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no running instance.')
        logger.info(
            f'Cluster {cluster_name_on_cloud} already has a running instance')
        return common.ProvisionRecord(provider_name=PROVIDER_NAME,
                                      cluster_name=cluster_name_on_cloud,
                                      region='default',
                                      zone=None,
                                      head_instance_id=instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    try:
        # Get instance type from node_config
        instance_type = config.node_config.get('InstanceType')
        logger.debug(f'Instance type from config: {instance_type}')
        if not instance_type:
            logger.error('InstanceType is not set in node_config')
            raise RuntimeError('InstanceType is not set in node_config. '
                               'Please specify an instance type for Mithril.')

        # Launch instance with SkyPilot's public SSH key
        public_key_path = config.authentication_config.get('ssh_public_key')
        public_key = None
        if public_key_path:
            try:
                with open(public_key_path, 'r', encoding='utf-8') as f:
                    public_key = f.read().strip()
                logger.debug(f'Using SSH public key from: {public_key_path}')
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    f'Failed to read public key from {public_key_path}: {e}')

        instance_id, ssh_command = utils.launch_instance(
            instance_type, cluster_name_on_cloud, region, public_key)
        logger.info(f'Launched instance {instance_id} with SSH command: '
                    f'{ssh_command}')
        created_instance_ids = [instance_id]

        # Wait for instance to have an IP address (means it's ready for SSH)
        if not utils.wait_for_instance(
                instance_id, utils.MithrilInstanceStatus.RUNNING.value):
            raise RuntimeError(f'Instance {instance_id} failed to become ready')

        logger.info(f'Instance {instance_id} is ready with IP address')

    except Exception as e:
        logger.error(f'Unexpected error: {e}')
        raise

    logger.info(f'Returning ProvisionRecord for instance {instance_id}')
    return common.ProvisionRecord(provider_name=PROVIDER_NAME,
                                  cluster_name=cluster_name_on_cloud,
                                  region='default',
                                  zone=None,
                                  head_instance_id=instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    worker_only: bool = False,
) -> None:
    """Terminate all instances in the cluster."""
    del provider_config, worker_only  # unused
    logger.info(
        f'Terminating all instances for cluster {cluster_name_on_cloud}')

    # First check if instances exis
    instances = _filter_instances(cluster_name_on_cloud, None)
    if not instances:
        logger.info(f'No instances found for cluster {cluster_name_on_cloud}')
        return

    # Terminate each instance
    for instance_id in instances:
        try:
            utils.terminate_instance(instance_id)
            logger.info(f'Terminated instance {instance_id}')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to terminate instance {instance_id}: {e}')
            continue

    # Wait for instances to be terminated
    # We check for instances that are NOT terminated (running, starting, etc.)
    start_time = time.time()
    while True:
        if time.time() - start_time > TIMEOUT:
            logger.error(
                f'Timed out after {TIMEOUT}s waiting for instances to terminate'
            )
            break

        # Check for instances that are still active (not terminated)
        active_instances = _filter_instances(cluster_name_on_cloud, [
            utils.MithrilInstanceStatus.RUNNING.value,
            utils.MithrilInstanceStatus.STARTING.value,
            utils.MithrilInstanceStatus.PENDING.value
        ])
        if not active_instances:
            logger.info('All instances terminated successfully')
            break

        logger.info(
            f'Waiting for {len(active_instances)} instances to terminate...')
        time.sleep(POLL_INTERVAL)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """Returns information about the cluster."""
    del region  # unused
    running_instances = _filter_instances(
        cluster_name_on_cloud, [utils.MithrilInstanceStatus.RUNNING.value])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    ssh_user = 'ubuntu'  # Default SSH user for Mithril

    for instance_id, instance_info in running_instances.items():
        # Extract IP address and port
        ip_address = instance_info.get('ip_address')
        ssh_port = instance_info.get('ssh_port', 22)

        if not ip_address:
            logger.warning(f'No IP address for instance {instance_id}')
            continue

        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=ip_address,
                external_ip=ip_address,
                ssh_port=ssh_port,
                tags={},
            )
        ]
        if head_instance_id is None:
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name=PROVIDER_NAME,
        provider_config=provider_config,
        ssh_user=ssh_user,
    )


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """Returns the status of the specified instances for Mithril."""
    del cluster_name, provider_config  # unused
    # Fetch all instances for this cluster
    instances = utils.list_instances(
        metadata={'skypilot': {
            'cluster_name': cluster_name_on_cloud
        }})
    if not instances:
        # No instances found: return empty dict to indicate fully deleted
        return {}

    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for instance_id, instance in instances.items():
        try:
            raw_status = instance.get('status', 'unknown').lower()
            mithril_status = utils.MithrilInstanceStatus.from_raw_status(
                raw_status)
            status = mithril_status.to_cluster_status()
            if non_terminated_only and status is None:
                continue
            statuses[instance_id] = (status, None)
        except utils.MithrilError as e:
            logger.warning(
                f'Failed to parse status for instance {instance_id}: {e}')
            continue
    return statuses


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """Wait for instances to reach the desired state."""
    del region  # unused
    if state == status_lib.ClusterStatus.UP:
        # Check if any instances are in RUNNING state
        instances = _filter_instances(
            cluster_name_on_cloud, [utils.MithrilInstanceStatus.RUNNING.value])
        if not instances:
            # Check if any instances are in a failed state
            failed_instances = _filter_instances(cluster_name_on_cloud, [
                utils.MithrilInstanceStatus.FAILED.value,
                utils.MithrilInstanceStatus.ERROR.value
            ])
            if failed_instances:
                raise RuntimeError(
                    f'Cluster {cluster_name_on_cloud} has failed instances: '
                    f'{failed_instances}')
            raise RuntimeError(f'No running instances found for cluster '
                               f'{cluster_name_on_cloud}')
        # Note: We don't check for terminated instances here because:
        # 1. Old terminated instances from previous launches may still be in
        #    the API
        # 2. The important check is that we have at least one running instance
        # 3. If the current instance gets terminated, it won't be in the
        #    RUNNING filter above
    elif state == status_lib.ClusterStatus.STOPPED:
        # Check if any instances are in TERMINATED state
        instances = _filter_instances(
            cluster_name_on_cloud,
            [utils.MithrilInstanceStatus.TERMINATED.value])
        if not instances:
            # Check if any instances are in a failed state
            failed_instances = _filter_instances(cluster_name_on_cloud, [
                utils.MithrilInstanceStatus.FAILED.value,
                utils.MithrilInstanceStatus.ERROR.value
            ])
            if failed_instances:
                raise RuntimeError(
                    f'Cluster {cluster_name_on_cloud} has failed instances: '
                    f'{failed_instances}')
            raise RuntimeError(f'No terminated instances found for cluster '
                               f'{cluster_name_on_cloud}')
        # Check if any instances are in RUNNING state
        running_instances = _filter_instances(
            cluster_name_on_cloud, [utils.MithrilInstanceStatus.RUNNING.value])
        if running_instances:
            error_msg = (
                f'Cluster {cluster_name_on_cloud} is in STOPPED state, '
                f'but {len(running_instances)} instances are running.')
            raise RuntimeError(error_msg)
    else:
        raise RuntimeError(f'Unsupported state: {state}')


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Stop running instances. Not supported for Mithril."""
    raise NotImplementedError('stop_instances is not supported for Mithril')


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    ports: Optional[list] = None,
) -> None:
    """Cleanup ports. Not supported for Mithril."""
    raise NotImplementedError('cleanup_ports is not supported for Mithril')


def cleanup_custom_multi_network(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    failover: bool = False,
) -> None:
    """Cleanup custom multi-network. Not supported for Mithril."""
    raise NotImplementedError(
        'cleanup_custom_multi_network is not supported for Mithril')


def open_ports(
    cluster_name_on_cloud: str,
    ports: list,
    provider_config: Optional[dict] = None,
) -> None:
    """Open ports. Not supported for Mithril."""
    raise NotImplementedError('open_ports is not supported for Mithril')
