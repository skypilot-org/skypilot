"""Verda Cloud (formerly DataCrunch) instance provisioning."""

import time
from typing import Any, Dict, List, Optional, Tuple

from sky import exceptions
from sky import sky_logging
from sky.provision import common
from sky.provision.verda.utils import Instance
from sky.provision.verda.utils import InstanceStatus
from sky.provision.verda.utils import VerdaClient
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

verda = VerdaClient()


def _filter_instances(
        cluster_name_on_cloud: str,
        status_filters: Optional[List[str]] = None) -> Dict[str, Instance]:
    instances = verda.instances_get()
    filtered_instances = {}
    for instance in instances:
        instance_id = instance.instance_id
        instance_name = instance.hostname
        # Filter by cluster name
        if cluster_name_on_cloud and cluster_name_on_cloud not in instance_name:
            continue
        # Filter by status if status_filters is provided
        if status_filters is not None and instance.status not in status_filters:
            continue
        filtered_instances[instance_id] = instance
    return filtered_instances


def _get_instance_info(instance_id: str) -> Instance:
    return verda.instance_get(instance_id)


def _get_head_instance_id(instances: Dict[str, Instance]) -> Optional[str]:
    head_instance_id = None
    for inst_id, inst in instances.items():
        if inst.hostname.endswith('-head'):
            head_instance_id = inst_id
            break
    return head_instance_id


def find_ssh_key_id(public_key: str):
    ssh_keys = verda.ssh_keys_get()
    for ssh_key in ssh_keys:
        if ssh_key.public_key == public_key:
            return ssh_key.id
    raise Exception(
        f'SSH key {public_key} not found in your Verda Cloud account')


def run_instances(
    region: str,
    cluster_name: str,
    cluster_name_on_cloud: str,
    config: common.ProvisionConfig,
) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    del cluster_name  # unused
    pending_statuses = [
        InstanceStatus.PROVISIONING,
        InstanceStatus.ORDERED,
    ]
    newly_started_instances = _filter_instances(cluster_name_on_cloud,
                                                pending_statuses)
    while True:
        instances = _filter_instances(cluster_name_on_cloud, pending_statuses)
        if not instances:
            break
        instance_statuses = [instance.status for instance in instances.values()]
        logger.info(f'Waiting for {len(instances)} instances to be ready: '
                    f'{instance_statuses}')
        time.sleep(POLL_INTERVAL)

    exist_instances = _filter_instances(cluster_name_on_cloud, pending_statuses)
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
        assert head_instance_id is not None, (
            'head_instance_id should not be None')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(exist_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(
            provider_name='verda',
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
            # Format: instance_type__vcpus__memory[__SPOT]
            instance_type = config.node_config['InstanceType']
            disk_size = config.node_config.get(
                'DiskSize', 50)  # Verda Cloud default disk size is 50GB
            # Preemptible - fancy way to call it a spot instance
            is_spot = config.node_config.get('Preemptible', None)

            # Get image from node_config (populated from template)
            image = config.node_config.get(
                'ImageId', 'ubuntu-24.04-cuda-12.8-open-docker')

            ssh_public_key = config.node_config['PublicKey']
            if ssh_public_key is None:
                raise ValueError('SSH public key is not set in the node config')
            ssh_key_id = find_ssh_key_id(ssh_public_key)

            instance_data = {
                'instance_type': instance_type,
                'hostname': f'{cluster_name_on_cloud}-{node_type}',
                'location_code': region,
                'is_spot': is_spot if is_spot is not None else False,
                'contract': 'PAY_AS_YOU_GO' if not is_spot else 'SPOT',
                'image': image,
                'description': 'Created by SkyPilot',
                'ssh_key_ids': [ssh_key_id],
                'os_volume': {
                    'name': f'{cluster_name_on_cloud}-{node_type}',
                    'size': disk_size,
                }
            }
            response = verda.instance_create(instance_data)
            instance_id = response.instance_id
        except Exception as e:  # pylint: disable=broad-except
            # API errors - provide specific message
            instance_type = config.node_config['InstanceType']
            region_str = (f' in region {region}'
                          if region != 'PLACEHOLDER' else '')
            # Check if it's a resource unavailability error
            error_str = str(e).lower()
            if any(keyword in error_str for keyword in [
                    'no capacity',
                    'capacity',
                    'unavailable',
                    'out of stock',
                    'insufficient',
                    'not available',
                    'quota exceeded',
                    'limit exceeded',
            ]):
                error_msg = (
                    f'Resources are currently unavailable on Verda. '
                    f'No {instance_type} instances are available{region_str}. '
                    f'Please try again later or consider using a different '
                    f'instance type or region. Details: {str(e)}')
            else:
                error_msg = (
                    f'Failed to launch {instance_type} instance on Verda'
                    f'{region_str}. Details: {str(e)}')
            logger.warning(f'API error during instance launch: {e}')
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(error_msg) from e
        logger.info(f'Launched instance {instance_id}.')
        created_instance_ids.append(instance_id)
        if head_instance_id is None:
            head_instance_id = instance_id

    # Wait for instances to be ready.
    for _ in range(MAX_POLLS_FOR_UP_OR_TERMINATE):
        instances = _filter_instances(cluster_name_on_cloud,
                                      [InstanceStatus.RUNNING])
        logger.info('Waiting for instances to be ready: '
                    f'({len(instances)}/{config.count}).')
        if len(instances) == config.count:
            break

        time.sleep(POLL_INTERVAL)
    else:
        # Failed to launch config.count of instances after max retries
        # Provide more specific error message
        instance_type = config.node_config['InstanceType']
        region_str = f' in region {region}' if region != 'PLACEHOLDER' else ''
        active_instances = len(
            _filter_instances(cluster_name_on_cloud, [InstanceStatus.RUNNING]))
        error_msg = (
            f'Timed out waiting for {instance_type} instances to become '
            f'ready on Verda Cloud{region_str}. Only {active_instances} '
            f'out of {config.count} instances became active. This may '
            f'indicate capacity issues or slow provisioning. Please try '
            f'again later or consider using a different instance type or '
            f'region.')
        logger.warning(error_msg)
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ResourcesUnavailableError(error_msg)
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(
        provider_name='verda',
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=head_instance_id,
        resumed_instance_ids=[],
        created_instance_ids=created_instance_ids,
    )


def wait_instances(
    region: str,
    cluster_name_on_cloud: str,
    state: Optional[status_lib.ClusterStatus],
) -> None:
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
    instances = _filter_instances(cluster_name_on_cloud, None)

    # Log if no instances found
    if not instances:
        logger.info(f'No instances found for cluster {cluster_name_on_cloud}')
        return

    # Filter out already terminated instances
    non_terminated_instances = {
        inst_id: inst
        for inst_id, inst in instances.items()
        if inst.status not in [InstanceStatus.OFFLINE]
    }

    if not non_terminated_instances:
        logger.info(
            f'All instances for cluster {cluster_name_on_cloud} are already '
            f'terminated or being deleted')
        return

    # Log what we're about to terminate
    instance_names = [
        inst.hostname for inst in non_terminated_instances.values()
    ]
    logger.info(
        f'Terminating {len(non_terminated_instances)} instances for cluster '
        f'{cluster_name_on_cloud}: {instance_names}')

    # Terminate each instance
    terminated_instances = []
    for instance_id, inst in non_terminated_instances.items():
        status = inst.status
        logger.debug(f'Terminating instance {instance_id} (status: {status})')
        if worker_only and inst.hostname.endswith('-head'):
            continue
        try:
            verda.instance_action(instance_id=instance_id, action='delete')
            terminated_instances.append(instance_id)
            name = inst.hostname
            logger.info(
                f'Successfully initiated termination of instance {instance_id}'
                f' ({name})')
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate instance {instance_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e

    # Wait for instances to be terminated
    if not terminated_instances:
        logger.info(
            'No instances were terminated (worker_only=True and only head '
            'node found)')
        return

    logger.info(
        f'Waiting for {len(terminated_instances)} instances to be terminated...'
    )
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
        remaining_statuses = [(inst_id, remaining_instances[inst_id].status)
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
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud,
                                          [InstanceStatus.RUNNING])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance in running_instances.items():
        running_instances[instance_id] = _get_instance_info(instance_id)
        external_ip = instance.ip
        if isinstance(external_ip, list):
            external_ip = external_ip[0]

        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip='NOT_SUPPORTED',
                external_ip=external_ip,
                ssh_port=22,
                tags={'provider': 'verda'},
            )
        ]
        if instance.hostname.endswith('-head'):
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='verda',
        provider_config=provider_config,
        ssh_user='root',
    )


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    del retry_if_missing  # unused
    instances = _filter_instances(cluster_name_on_cloud, None)

    status_map = {
        InstanceStatus.PROVISIONING: status_lib.ClusterStatus.INIT,
        InstanceStatus.ERROR: status_lib.ClusterStatus.INIT,
        InstanceStatus.RUNNING: status_lib.ClusterStatus.UP,
        InstanceStatus.OFFLINE: status_lib.ClusterStatus.STOPPED,
        'deleted': None,  # Being deleted - should be filtered out
        'discontinued': None,  # Already terminated - should be filtered out
    }
    statuses: Dict[str, Tuple[Optional[status_lib.ClusterStatus],
                              Optional[str]]] = {}
    for inst_id, inst in instances.items():
        status = status_map[inst.status]
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
