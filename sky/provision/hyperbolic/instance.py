"""Hyperbolic instance provisioning."""
import time
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky.provision import common
from sky.provision.hyperbolic import utils
from sky.utils import common_utils
from sky.utils import status_lib
from sky.utils import ux_utils

POLL_INTERVAL = 5
QUERY_PORTS_TIMEOUT_SECONDS = 30

logger = sky_logging.init_logger(__name__)


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]],
                      head_only: bool = False) -> Dict[str, Any]:
    """Filter instances by cluster name and status."""
    print(
        f'DEBUG: _filter_instances called with cluster_name_on_cloud={cluster_name_on_cloud}, status_filters={status_filters}, head_only={head_only}'
    )
    instances = utils.list_instances()
    possible_names = [f'{cluster_name_on_cloud}-head']
    if not head_only:
        possible_names.append(f'{cluster_name_on_cloud}-worker')

    filtered_instances = {}
    for instance_id, instance in instances.items():
        if (status_filters is not None and
                instance['status'] not in status_filters):
            continue
        if instance.get('name') in possible_names:
            filtered_instances[instance_id] = instance
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    """Get the head instance ID from the instances dict."""
    head_instance_id = None
    for inst_id, inst in instances.items():
        if inst['name'].endswith('-head'):
            head_instance_id = inst_id
            break
    return head_instance_id


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    print('DEBUG: ENTERED run_instances')
    print(
        f'DEBUG: region={region}, cluster_name_on_cloud={cluster_name_on_cloud}'
    )
    print(f'DEBUG: config={config}')
    # Define pending statuses for Hyperbolic
    pending_status = ['CREATING', 'STARTING']

    # Wait for any pending instances to be ready
    while True:
        instances = _filter_instances(cluster_name_on_cloud, pending_status)
        if not instances:
            break
        logger.info(f'Waiting for {len(instances)} instances to be ready.')
        time.sleep(POLL_INTERVAL)

    # Check existing running instances
    exist_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    head_instance_id = _get_head_instance_id(exist_instances)

    # Calculate how many new instances to start
    to_start_count = config.count - len(exist_instances)
    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')
    if to_start_count == 0:
        if head_instance_id is None:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no head node.')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(exist_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(provider_name='hyperbolic',
                                      cluster_name=cluster_name_on_cloud,
                                      region='default',
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    # Launch new instances
    created_instance_ids = []
    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            # Get instance type from node_config
            instance_type = config.node_config.get('InstanceType')
            if not instance_type:
                raise RuntimeError(
                    'InstanceType is not set in node_config. '
                    'Please specify an instance type for Hyperbolic.')

            # Extract GPU configuration from instance type
            # Format: 1x-T4-4-17 -> gpu_count=1, gpu_model=Tesla-T4
            try:
                parts = instance_type.split('-')
                if len(parts) != 4:
                    raise ValueError(
                        f'Invalid instance type format: {instance_type}. '
                        'Expected format: <gpu_count>x-<gpu_model>-<cpu>-<memory>')
                
                gpu_count = int(parts[0].replace('x', ''))
                gpu_model = parts[1].upper()
                
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
                else:
                    # If not in map, assume it's already in the correct format
                    logger.warning(f'GPU model {gpu_model} not found in mapping, using as-is')
                
                # Validate CPU and memory values
                cpu_count = int(parts[2])
                memory_gb = int(parts[3])
                
                if cpu_count < 1 or memory_gb < 1:
                    raise ValueError(
                        f'Invalid CPU count ({cpu_count}) or memory ({memory_gb}GB). '
                        'Both must be positive integers.')
                
            except ValueError as e:
                raise RuntimeError(
                    f'Failed to parse instance type {instance_type}: {str(e)}')

            # Launch instance with GPU configuration
            instance_id = utils.launch_instance(
                gpu_model=gpu_model,
                gpu_count=gpu_count,
                name=f'{cluster_name_on_cloud}-{node_type}')
            logger.info(f'Launched instance {instance_id}.')
            created_instance_ids.append(instance_id)
            if head_instance_id is None:
                head_instance_id = instance_id
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'run_instances error: {e}')
            raise

    # Wait for instances to be ready
    while True:
        instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
        ready_instance_cnt = 0
        for instance_id, instance in instances.items():
            # Wait for each instance to be fully ready
            if utils.wait_for_instance(instance_id, 'RUNNING', timeout=300):
                if instance.get('ssh_port') is not None:
                    ready_instance_cnt += 1
        logger.info('Waiting for instances to be ready: '
                    f'({ready_instance_cnt}/{config.count}).')
        if ready_instance_cnt == config.count:
            break

        time.sleep(POLL_INTERVAL)

    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(provider_name='hyperbolic',
                                  cluster_name=cluster_name_on_cloud,
                                  region='default',
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Terminates the specified instances."""
    del provider_config  # unused
    instances = _filter_instances(cluster_name_on_cloud, None)
    for inst_id, inst in instances.items():
        logger.debug(f'Terminating instance {inst_id}: {inst}')
        if worker_only and inst['name'].endswith('-head'):
            continue
        try:
            utils.terminate_instance(inst_id)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate instance {inst_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """Returns information about the cluster."""
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=instance_info['internal_ip'],
                external_ip=instance_info['external_ip'],
                ssh_port=instance_info['ssh_port'],
                tags={},
            )
        ]
        if instance_info['name'].endswith('-head'):
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='hyperbolic',
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
