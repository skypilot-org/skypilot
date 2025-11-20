"""Prime Intellect instance provisioning."""
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import exceptions
from sky import sky_logging
from sky.provision import common
from sky.provision.primeintellect import utils
from sky.utils import common_utils
from sky.utils import status_lib
from sky.utils import ux_utils

# The maximum number of times to poll for the status of an operation.
POLL_INTERVAL = 5
MAX_POLLS = 60 // POLL_INTERVAL
# Terminating instances can take several minutes, so we increase the timeout
MAX_POLLS_FOR_UP_OR_TERMINATE = MAX_POLLS * 16

# status filters
# PROVISIONING, PENDING, ACTIVE, STOPPED, ERROR, DELETING, TERMINATED

logger = sky_logging.init_logger(__name__)

# SSH connection readiness polling constants
SSH_CONN_MAX_RETRIES = 6
SSH_CONN_RETRY_INTERVAL_SECONDS = 10


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]]) -> Dict[str, Any]:
    client = utils.PrimeIntellectAPIClient()
    instances = client.list_instances()
    # TODO: verify names are we using it?
    possible_names = [
        f'{cluster_name_on_cloud}-head',
        f'{cluster_name_on_cloud}-worker',
    ]

    filtered_instances = {}
    for instance in instances:
        instance_id = instance['id']
        if (status_filters is not None and
                instance['status'] not in status_filters):
            continue
        instance_name = instance.get('name')
        if instance_name and instance_name in possible_names:
            filtered_instances[instance_id] = instance
    return filtered_instances


def _get_instance_info(instance_id: str) -> Dict[str, Any]:
    client = utils.PrimeIntellectAPIClient()
    return client.get_instance_details(instance_id)


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    head_instance_id = None
    for inst_id, inst in instances.items():
        if inst['name'].endswith('-head'):
            head_instance_id = inst_id
            break
    return head_instance_id


# Helper is available as utils.parse_ssh_connection.


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    del cluster_name  # unused
    pending_status = [
        'PROVISIONING',
        'PENDING',
    ]
    newly_started_instances = _filter_instances(cluster_name_on_cloud,
                                                pending_status)
    client = utils.PrimeIntellectAPIClient()

    while True:
        instances = _filter_instances(cluster_name_on_cloud, pending_status)
        if not instances:
            break
        instance_statuses = [
            instance['status'] for instance in instances.values()
        ]
        logger.info(f'Waiting for {len(instances)} instances to be ready: '
                    f'{instance_statuses}')
        time.sleep(POLL_INTERVAL)

    exist_instances = _filter_instances(cluster_name_on_cloud,
                                        status_filters=pending_status)
    if len(exist_instances) > config.count:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')

    exist_instances = _filter_instances(cluster_name_on_cloud,
                                        status_filters=['ACTIVE'])
    head_instance_id = _get_head_instance_id(exist_instances)
    to_start_count = config.count - len(exist_instances)
    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')
    if to_start_count == 0:
        if head_instance_id is None:
            head_instance_id = list(exist_instances.keys())[0]
            # TODO: implement rename pod
            # client.rename(
            #     instance_id=head_instance_id,
            #     name=f'{cluster_name_on_cloud}-head',
            # )
        assert head_instance_id is not None, (
            'head_instance_id should not be None')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(exist_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(
            provider_name='primeintellect',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=config.provider_config['zones'],
            head_instance_id=head_instance_id,
            resumed_instance_ids=list(newly_started_instances.keys()),
            created_instance_ids=[],
        )

    created_instance_ids = []
    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            # Extract vCPUs and memory from instance type
            # Format: provider__gpu_prefix_base_type__vcpus__memory[_SPOT]
            instance_type = config.node_config['InstanceType']
            disk_size = config.node_config.get('DiskSize')
            vcpus = -1
            memory = -1
            try:
                # Split by '__'
                parts = instance_type.split('__')

                # Format: provider__gpu_info__vcpus__memory[_SPOT]
                # For: primecompute__8xH100_80GB__104__752_SPOT
                # parts[0] = primecompute, parts[1] = 8xH100_80GB,
                # parts[2] = 104, parts[3] = 752, parts[4] = SPOT
                if len(parts) >= 4:
                    vcpu_str = parts[2]
                    memory_str = parts[3]
                    vcpus = int(vcpu_str)
                    memory = int(memory_str)
            except (ValueError, IndexError) as e:
                # If parsing fails, try to get from catalog
                logger.warning(
                    f'Failed to parse vCPUs/memory from instance type '
                    f'{instance_type}: {e}')

            params = {
                'name': f'{cluster_name_on_cloud}-{node_type}',
                'instance_type': config.node_config['InstanceType'],
                'region': region,
                'availability_zone': config.provider_config['zones'],
                'disk_size': disk_size,
                'vcpus': vcpus,
                'memory': memory,
            }

            response = client.launch(**params)
            instance_id = response['id']
        except utils.PrimeintellectResourcesUnavailableError as e:
            # Resource unavailability error - provide specific message
            instance_type = config.node_config['InstanceType']
            region_str = (f' in region {region}'
                          if region != 'PLACEHOLDER' else '')
            error_msg = (
                f'Resources are currently unavailable on Prime Intellect. '
                f'No {instance_type} instances are available{region_str}. '
                f'Please try again later or consider using a different '
                f'instance type or region. Details: {str(e)}')
            logger.warning(f'Resource unavailability error: {e}')
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(error_msg) from e
        except utils.PrimeintellectAPIError as e:
            # Other API errors - provide specific message
            instance_type = config.node_config['InstanceType']
            region_str = (f' in region {region}'
                          if region != 'PLACEHOLDER' else '')
            error_msg = (f'Failed to launch {instance_type} instance on Prime '
                         f'Intellect{region_str}. Details: {str(e)}')
            logger.warning(f'API error during instance launch: {e}')
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(error_msg) from e
        except Exception as e:  # pylint: disable=broad-except
            # Generic error handling for unexpected errors
            instance_type = config.node_config['InstanceType']
            region_str = (f' in region {region}'
                          if region != 'PLACEHOLDER' else '')
            error_msg = (
                f'Unexpected error while launching {instance_type} instance '
                f'on Prime Intellect{region_str}. Details: '
                f'{common_utils.format_exception(e, use_bracket=False)}')
            logger.warning(f'Unexpected error during instance launch: {e}')
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(error_msg) from e
        logger.info(f'Launched instance {instance_id}.')
        created_instance_ids.append(instance_id)
        if head_instance_id is None:
            head_instance_id = instance_id

    # Wait for instances to be ready.
    for _ in range(MAX_POLLS_FOR_UP_OR_TERMINATE):
        instances = _filter_instances(cluster_name_on_cloud, ['ACTIVE'])
        logger.info('Waiting for instances to be ready: '
                    f'({len(instances)}/{config.count}).')
        if len(instances) == config.count:
            break

        time.sleep(POLL_INTERVAL)
    else:
        # Failed to launch config.count of instances after max retries
        # Provide more specific error message
        instance_type = config.node_config['InstanceType']
        region_str = (f' in region {region}' if region != 'PLACEHOLDER' else '')
        active_instances = len(
            _filter_instances(cluster_name_on_cloud, ['ACTIVE']))
        error_msg = (
            f'Timed out waiting for {instance_type} instances to become '
            f'ready on Prime Intellect{region_str}. Only {active_instances} '
            f'out of {config.count} instances became active. This may '
            f'indicate capacity issues or slow provisioning. Please try '
            f'again later or consider using a different instance type or '
            f'region.')
        logger.warning(error_msg)
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ResourcesUnavailableError(error_msg)
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(
        provider_name='primeintellect',
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=config.provider_config['zones'],
        head_instance_id=head_instance_id,
        resumed_instance_ids=[],
        created_instance_ids=created_instance_ids,
    )


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    raise NotImplementedError()


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config  # unused
    client = utils.PrimeIntellectAPIClient()
    instances = _filter_instances(cluster_name_on_cloud, None)

    # Log if no instances found
    if not instances:
        logger.info(f'No instances found for cluster {cluster_name_on_cloud}')
        return

    # Filter out already terminated instances
    non_terminated_instances = {
        inst_id: inst
        for inst_id, inst in instances.items()
        if inst['status'] not in ['TERMINATED', 'DELETING']
    }

    if not non_terminated_instances:
        logger.info(
            f'All instances for cluster {cluster_name_on_cloud} are already '
            f'terminated or being deleted')
        return

    # Log what we're about to terminate
    instance_names = [
        inst['name'] for inst in non_terminated_instances.values()
    ]
    logger.info(
        f'Terminating {len(non_terminated_instances)} instances for cluster '
        f'{cluster_name_on_cloud}: {instance_names}')

    # Terminate each instance
    terminated_instances = []
    for inst_id, inst in non_terminated_instances.items():
        status = inst['status']
        logger.debug(f'Terminating instance {inst_id} (status: {status})')
        if worker_only and inst['name'].endswith('-head'):
            continue
        try:
            client.remove(inst_id)
            terminated_instances.append(inst_id)
            name = inst['name']
            logger.info(
                f'Successfully initiated termination of instance {inst_id} '
                f'({name})')
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate instance {inst_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e

    # Wait for instances to be terminated
    if not terminated_instances:
        logger.info(
            'No instances were terminated (worker_only=True and only head '
            'node found)')
        return

    logger.info(f'Waiting for {len(terminated_instances)} instances to be '
                f'terminated...')
    for _ in range(MAX_POLLS_FOR_UP_OR_TERMINATE):
        remaining_instances = _filter_instances(cluster_name_on_cloud, None)

        # Check if all terminated instances are gone
        still_exist = [
            inst_id for inst_id in terminated_instances
            if inst_id in remaining_instances
        ]
        if not still_exist:
            logger.info('All instances have been successfully terminated')
            break

        # Log status of remaining instances
        remaining_statuses = [(inst_id, remaining_instances[inst_id]['status'])
                              for inst_id in still_exist]
        logger.info(
            f'Waiting for termination... {len(still_exist)} instances still '
            f'exist: {remaining_statuses}')
        time.sleep(POLL_INTERVAL)
    else:
        # Timeout reached
        remaining_instances = _filter_instances(cluster_name_on_cloud, None)
        still_exist = [
            inst_id for inst_id in terminated_instances
            if inst_id in remaining_instances
        ]
        if still_exist:
            logger.warning(
                f'Timeout reached. {len(still_exist)} instances may still be '
                f'terminating: {still_exist}')
        else:
            logger.info('All instances have been successfully terminated')


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud, ['ACTIVE'])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    head_ssh_user = None
    for instance_id, instance in running_instances.items():
        retry_count = 0
        max_retries = SSH_CONN_MAX_RETRIES
        while (instance.get('sshConnection') is None and
               retry_count < max_retries):
            name = instance.get('name')
            print(f'SSH connection to {name} is not ready, waiting '
                  f'{SSH_CONN_RETRY_INTERVAL_SECONDS} seconds... '
                  f'(attempt {retry_count + 1}/{max_retries})')
            time.sleep(SSH_CONN_RETRY_INTERVAL_SECONDS)
            retry_count += 1
            running_instances[instance_id] = _get_instance_info(instance_id)

        if instance.get('sshConnection') is not None:
            print('SSH connection is ready!')
        else:
            raise Exception(
                f'Failed to establish SSH connection after {max_retries} '
                f'attempts')

        assert instance.get(
            'sshConnection'), 'sshConnection cannot be null anymore'

        ssh_connection = instance['sshConnection']
        _, ssh_port = utils.parse_ssh_connection(ssh_connection)

        external_ip = instance['ip']
        if isinstance(external_ip, list):
            external_ip = external_ip[0]

        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip='NOT_SUPPORTED',
                external_ip=external_ip,
                ssh_port=ssh_port,
                tags={'provider': instance['providerType']},
            )
        ]
        if instance['name'].endswith('-head'):
            head_instance_id = instance_id
            parsed_user_for_user, _ = utils.parse_ssh_connection(ssh_connection)
            head_ssh_user = parsed_user_for_user or 'ubuntu'

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='primeintellect',
        provider_config=provider_config,
        ssh_user=head_ssh_user,
    )


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud, None)

    status_map = {
        'PENDING': status_lib.ClusterStatus.INIT,
        'ERROR': status_lib.ClusterStatus.INIT,
        'ACTIVE': status_lib.ClusterStatus.UP,
        'STOPPED': status_lib.ClusterStatus.STOPPED,
        'DELETING': None,  # Being deleted - should be filtered out
        'TERMINATED': None,  # Already terminated - should be filtered out
    }
    statuses: Dict[str, Tuple[Optional[status_lib.ClusterStatus],
                              Optional[str]]] = {}
    for inst_id, inst in instances.items():
        status = status_map[inst['status']]
        if non_terminated_only and status is None:
            continue
        statuses[inst_id] = (status, None)
    return statuses


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del cluster_name_on_cloud, ports, provider_config  # Unused.
