"""Hyperbolic instance provisioning."""
import time
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky.provision import common
from sky.provision.hyperbolic import utils
from sky.utils import status_lib

PROVIDER_NAME = 'hyperbolic'
POLL_INTERVAL = 5
QUERY_PORTS_TIMEOUT_SECONDS = 30
TIMEOUT = 300

logger = sky_logging.init_logger(__name__)


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]],
                      head_only: bool = False) -> Dict[str, Any]:
    logger.debug(f'Filtering instances: cluster={cluster_name_on_cloud}, '
                 f'status={status_filters}')
    _ = head_only  # Mark as intentionally unused

    # Filter by cluster name using metadata
    instances = utils.list_instances(
        metadata={'skypilot_cluster_name': cluster_name_on_cloud})

    # Normalize status filters to lowercase
    if status_filters is not None:
        status_filters = [s.lower() for s in status_filters]

    filtered_instances = {}
    for instance_id, instance in instances.items():
        try:
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


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    logger.info(f'Starting run_instances with region={region}, '
                f'cluster={cluster_name_on_cloud}')
    logger.debug(f'Config: {config}')
    start_time = time.time()

    # Define pending statuses for Hyperbolic
    pending_status = [
        utils.HyperbolicInstanceStatus.CREATING.value,
        utils.HyperbolicInstanceStatus.STARTING.value
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
        cluster_name_on_cloud, [utils.HyperbolicInstanceStatus.RUNNING.value])
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

    # Launch new instance
    try:
        # Get instance type from node_config
        instance_type = config.node_config.get('InstanceType')
        logger.debug(f'Instance type from config: {instance_type}')
        if not instance_type:
            logger.error('InstanceType is not set in node_config')
            raise RuntimeError(
                'InstanceType is not set in node_config. '
                'Please specify an instance type for Hyperbolic.')

        # Extract GPU configuration from instance type
        # Format: 1x-T4-4-17 -> gpu_count=1, gpu_model=Tesla-T4
        try:
            parts = instance_type.split('-')
            logger.debug(f'Parsing instance type parts: {parts}')
            if len(parts) != 4:
                raise ValueError(
                    f'Invalid instance type format: {instance_type}. '
                    'Expected format: <gpu_count>x-<gpu_model>-<cpu>-<memory>')

            gpu_count = int(parts[0].replace('x', ''))
            gpu_model = parts[1].upper()
            logger.info(
                f'Parsed GPU config: count={gpu_count}, model={gpu_model}')

            # Map GPU model to Hyperbolic's expected format
            gpu_model_map = {
                'T4': 'Tesla-T4',
                'A100': 'NVIDIA-A100',
                'V100': 'Tesla-V100',
                'P100': 'Tesla-P100',
                'K80': 'Tesla-K80',
                'RTX3090': 'RTX-3090',
                'RTX4090': 'RTX-4090',
                'RTX4080': 'RTX-4080',
                'RTX4070': 'RTX-4070',
                'RTX4060': 'RTX-4060',
                'RTX3080': 'RTX-3080',
                'RTX3070': 'RTX-3070',
                'RTX3060': 'RTX-3060',
            }
            gpu_model = gpu_model_map.get(gpu_model, gpu_model)
            logger.info(f'Mapped GPU model to {gpu_model}')

            # Launch instance
            instance_id, ssh_command = utils.launch_instance(
                gpu_model, gpu_count, cluster_name_on_cloud)
            logger.info(f'Launched instance {instance_id} with SSH command: '
                        f'{ssh_command}')
            created_instance_ids = [instance_id]

            # Wait for instance to be ready
            if not utils.wait_for_instance(
                    instance_id, utils.HyperbolicInstanceStatus.RUNNING.value):
                raise RuntimeError(
                    f'Instance {instance_id} failed to reach RUNNING state')

        except ValueError as e:
            logger.error(f'Failed to parse instance type: {e}')
            raise RuntimeError(str(e)) from e
        except Exception as e:
            logger.error(f'Failed to launch instance: {e}')
            raise RuntimeError(str(e)) from e

    except Exception as e:
        logger.error(f'Unexpected error: {e}')
        raise

    # Wait for instance to be ready
    logger.info(f'Waiting for instance {instance_id} to be ready')
    while True:
        instances = _filter_instances(
            cluster_name_on_cloud,
            [utils.HyperbolicInstanceStatus.RUNNING.value])
        logger.debug(f'Current instances: {instances}')
        if len(instances) == 1:
            logger.info(f'Instance {instance_id} is ready')
            break
        if time.time() - start_time > TIMEOUT:
            logger.error(
                f'Timed out after {TIMEOUT}s waiting for instance to be ready')
            raise TimeoutError(
                f'Timed out after {TIMEOUT}s waiting for instance to be ready')
        logger.info('Waiting for instance to be ready...')
        time.sleep(POLL_INTERVAL)

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

    # First check if instances exist
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
    start_time = time.time()
    while True:
        if time.time() - start_time > TIMEOUT:
            logger.error(
                f'Timed out after {TIMEOUT}s waiting for instances to terminate'
            )
            break

        instances = _filter_instances(
            cluster_name_on_cloud,
            [utils.HyperbolicInstanceStatus.TERMINATED.value])
        if not instances:
            logger.info('All instances terminated successfully')
            break

        logger.info('Waiting for instances to terminate...')
        time.sleep(POLL_INTERVAL)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """Returns information about the cluster."""
    del region  # unused
    running_instances = _filter_instances(
        cluster_name_on_cloud, [utils.HyperbolicInstanceStatus.RUNNING.value])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None

    for instance_id, instance_info in running_instances.items():
        # Extract hostname and port from sshCommand
        ssh_command = instance_info.get('sshCommand', '')
        if ssh_command:
            # Format: ssh user@hostname -p port
            parts = ssh_command.split()
            if len(parts) >= 4:
                user_host = parts[1]  # user@hostname
                if '@' in user_host:
                    ssh_user = user_host.split('@')[0]
                    hostname = user_host.split('@')[1]
                else:
                    hostname = user_host
                port = int(parts[3])
            else:
                hostname = instance_id
                port = 22
        else:
            hostname = instance_id
            port = 22

        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=hostname,
                external_ip=hostname,
                ssh_port=port,
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
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[str]]:
    """Returns the status of the specified instances for Hyperbolic."""
    del provider_config, non_terminated_only  # unused
    statuses: Dict[str, Optional[str]] = {}
    if cluster_name_on_cloud is not None:
        statuses[
            f'{cluster_name_on_cloud}-head'] = status_lib.ClusterStatus.UP.value
    return statuses


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """Wait for instances to reach the desired state."""
    del region  # unused
    if state == status_lib.ClusterStatus.UP:
        # Check if any instances are in RUNNING state
        instances = _filter_instances(
            cluster_name_on_cloud,
            [utils.HyperbolicInstanceStatus.RUNNING.value])
        if not instances:
            # Check if any instances are in a failed state
            failed_instances = _filter_instances(cluster_name_on_cloud, [
                utils.HyperbolicInstanceStatus.FAILED.value,
                utils.HyperbolicInstanceStatus.ERROR.value
            ])
            if failed_instances:
                raise RuntimeError(
                    f'Cluster {cluster_name_on_cloud} has failed instances: '
                    f'{failed_instances}')
            raise RuntimeError(f'No running instances found for cluster '
                               f'{cluster_name_on_cloud}')
        # Check if any instances are in TERMINATED state
        terminated_instances = _filter_instances(
            cluster_name_on_cloud,
            [utils.HyperbolicInstanceStatus.TERMINATED.value])
        if terminated_instances:
            error_msg = (
                f'Cluster {cluster_name_on_cloud} is in UP state, but '
                f'{len(terminated_instances)} instances are terminated.')
            raise RuntimeError(error_msg)
    elif state == status_lib.ClusterStatus.STOPPED:
        # Check if any instances are in TERMINATED state
        instances = _filter_instances(
            cluster_name_on_cloud,
            [utils.HyperbolicInstanceStatus.TERMINATED.value])
        if not instances:
            # Check if any instances are in a failed state
            failed_instances = _filter_instances(cluster_name_on_cloud, [
                utils.HyperbolicInstanceStatus.FAILED.value,
                utils.HyperbolicInstanceStatus.ERROR.value
            ])
            if failed_instances:
                raise RuntimeError(
                    f'Cluster {cluster_name_on_cloud} has failed instances: '
                    f'{failed_instances}')
            raise RuntimeError(f'No terminated instances found for cluster '
                               f'{cluster_name_on_cloud}')
        # Check if any instances are in RUNNING state
        running_instances = _filter_instances(
            cluster_name_on_cloud,
            [utils.HyperbolicInstanceStatus.RUNNING.value])
        if running_instances:
            error_msg = (
                f'Cluster {cluster_name_on_cloud} is in STOPPED state, but '
                f'{len(running_instances)} instances are running.')
            raise RuntimeError(error_msg)
    else:
        raise RuntimeError(f'Unsupported state: {state}')


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Stop running instances. Not supported for Hyperbolic."""
    raise NotImplementedError('stop_instances is not supported for Hyperbolic')


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    ports: Optional[list] = None,
) -> None:
    """Cleanup ports. Not supported for Hyperbolic."""
    raise NotImplementedError('cleanup_ports is not supported for Hyperbolic')


def open_ports(
    cluster_name_on_cloud: str,
    ports: list,
    provider_config: Optional[dict] = None,
) -> None:
    """Open ports. Not supported for Hyperbolic."""
    raise NotImplementedError('open_ports is not supported for Hyperbolic')
