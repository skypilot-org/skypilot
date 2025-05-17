"""Hyperbolic instance provisioning."""
import time
from typing import Any, Dict, List, Optional
import uuid

from sky import sky_logging
from sky.provision import common
from sky.provision.hyperbolic import utils
from sky.utils import common_utils
from sky.utils import status_lib
from sky.utils import ux_utils

PROVIDER_NAME = 'hyperbolic'
POLL_INTERVAL = 5
QUERY_PORTS_TIMEOUT_SECONDS = 30
TIMEOUT = 180

logger = sky_logging.init_logger(__name__)


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]],
                      head_only: bool = False) -> Dict[str, Any]:
    logger.debug(
        f'Filtering instances: cluster={cluster_name_on_cloud}, status={status_filters}'
    )

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
            if status_filters is not None and instance_status not in status_filters:
                logger.debug(
                    f'Skipping instance {instance_id} - status {instance_status} not in {status_filters}'
                )
                continue

            filtered_instances[instance_id] = instance
            logger.debug(
                f'Including instance {instance_id} with status {instance_status}'
            )

        except Exception as e:
            logger.warning(f'Error processing instance {instance_id}: {str(e)}')
            continue

    logger.info(f'Found {len(filtered_instances)} instances matching filters')
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    """Get the instance ID from the instances dict.
    
    Since Hyperbolic only supports single node clusters, this is just the first instance.
    """
    if not instances:
        return None
    return next(iter(instances.keys()))


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    logger.info(
        f'Starting run_instances with region={region}, cluster={cluster_name_on_cloud}'
    )
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

            if gpu_model in gpu_model_map:
                gpu_model = gpu_model_map[gpu_model]
                logger.debug(f'Mapped GPU model to {gpu_model}')
            else:
                # If not in map, assume it's already in the correct format
                logger.warning(
                    f'GPU model {gpu_model} not found in mapping, using as-is')

            # Validate CPU and memory values
            cpu_count = int(parts[2])
            memory_gb = int(parts[3])
            logger.debug(
                f'Parsed CPU count: {cpu_count}, memory: {memory_gb}GB')

            if cpu_count < 1 or memory_gb < 1:
                raise ValueError(
                    f'Invalid CPU count ({cpu_count}) or memory ({memory_gb}GB). '
                    'Both must be positive integers.')

        except ValueError as e:
            logger.error(
                f'Failed to parse instance type {instance_type}: {str(e)}')
            raise RuntimeError(
                f'Failed to parse instance type {instance_type}: {str(e)}')

        # Launch instance with GPU configuration and metadata
        logger.info(
            f'Launching instance with GPU model {gpu_model}, count {gpu_count}')
        instance_id = utils.launch_instance(gpu_model=gpu_model,
                                            gpu_count=gpu_count,
                                            name=cluster_name_on_cloud)
        logger.info(f'Successfully launched instance {instance_id}')
        created_instance_ids = [instance_id]
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Error in run_instances: {str(e)}')
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
    del provider_config, worker_only  # unused
    logger.info(
        f'Terminating all instances for cluster {cluster_name_on_cloud}')
    instances = _filter_instances(cluster_name_on_cloud, None)
    if not instances:
        logger.info(f'No instances found for cluster {cluster_name_on_cloud}')
        return
    for instance_id in instances:
        try:
            utils.terminate_instance(instance_id)
            logger.info(f'Terminated instance {instance_id}')
        except Exception as e:
            logger.warning(f'Failed to terminate instance {instance_id}: {e}')


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
            # Format: ssh ubuntu@hostname -p port
            parts = ssh_command.split()
            if len(parts) >= 4:
                hostname = parts[1].split('@')[1]
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
                   provider_config: dict, desired_status: str,
                   timeout: int) -> None:
    """Waits for instances to reach the desired status. Minimal stub."""
    del region, cluster_name_on_cloud, provider_config, desired_status, timeout  # unused
    time.sleep(1)
    return


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
