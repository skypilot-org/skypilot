"""CloudRift instance provisioning."""

import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sky import sky_logging
from sky.provision import common
from sky.provision.cloudrift import constants
from sky.provision.cloudrift import utils
from sky.utils import status_lib

# The maximum number of times to poll for the status of an operation
MAX_POLLS = 60 // constants.POLL_INTERVAL
# Stopping instances can take several minutes, so we increase the timeout
MAX_POLLS_FOR_UP_OR_STOP = MAX_POLLS * 8

logger = sky_logging.init_logger(__name__)


def _get_head_instance(
        instances: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for instance_id, instance_meta in instances.items():
        return instance_meta
    return None


def _filter_instances(
        cluster_name_on_cloud: str,
        status_filters: Optional[List[str]]) -> Dict[str, Dict[str, Any]]:
    cloudrift_client = utils.get_cloudrift_client()
    instances = cloudrift_client.list_instances(
        cluster_name=cluster_name_on_cloud)

    filtered_instances = {}
    for instance in instances:
        if (status_filters is not None and
                instance.get('status') not in status_filters):
            continue
        filtered_instances[instance.get('id')] = instance
    return filtered_instances


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    cloudrift_client = utils.get_cloudrift_client()
    del cluster_name  # unused
    pending_status = ['Initializing']
    while True:
        instances_dict = _filter_instances(cluster_name_on_cloud, pending_status)
        if not instances_dict:
            break
        instance_statuses = [instance.get('status') for instance in instances_dict.values()]
        logger.info(f'Waiting for {len(instances_dict)} instances to be ready: '
                    f'{instance_statuses}')
        time.sleep(constants.POLL_INTERVAL)

    exist_instances = _filter_instances(cluster_name_on_cloud,
                                        status_filters=pending_status +
                                        ['Active'])

    head_instance = _get_head_instance(exist_instances)
    to_start_count = config.count - len(exist_instances)
    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')
    if to_start_count == 0:
        if head_instance is None:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no head node.')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(exist_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(
            provider_name='CloudRift',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,
            head_instance_id=str(head_instance.get('id')),
            resumed_instance_ids=[],
            created_instance_ids=[],
        )

    def launch_node(node_type: str) -> str:
        try:
            suffix = uuid.uuid4().hex[:8]
            ssh_public_key_file = config.authentication_config['ssh_public_key']
            with open(ssh_public_key_file, 'r', encoding='utf-8') as f:
                ssh_public_key = f.read().strip()
                launch_data = cloudrift_client.deploy_instance(
                    instance_type=config.node_config['InstanceType'],
                    cluster_name=cluster_name_on_cloud,
                    name=f'{cluster_name_on_cloud}-{suffix}-{node_type}',
                    ssh_keys=[ssh_public_key],
                )
                instance_id = launch_data[0]
                logger.info(f'Launched {node_type} node, '
                            f'instance_id: {instance_id}')
                return instance_id
        except Exception as e:
            logger.warning(f'run_instances error: {e}')
            raise
    created_instance_ids: List[str] = []
    for _ in range(to_start_count):
        instance_type = 'head' if head_instance is None else 'worker'
        try:
            instance_id = launch_node(instance_type)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'run_instances error: {e}')
            raise

        logger.info(f'Launched instance {instance_id}.')
        created_instance_ids.append(instance_id)
        if head_instance is None:
            head_instance = instance_id

    instances_to_wait = created_instance_ids.copy()

    print("instances_to_wait", instances_to_wait)

    # Wait for instances to be ready.
    for _ in range(MAX_POLLS_FOR_UP_OR_STOP):
        instances = cloudrift_client.list_instances(cluster_name_on_cloud)
        print("list instances", instances)
        for instance in instances:
            if instance.get('status') == 'Active' and instance.get(
                'id') in instances_to_wait:
                instances_to_wait.remove(instance.get('id'))
            elif instance.get('status') != 'Initializing' and instance.get(
                'id') in instances_to_wait:
                raise RuntimeError(
                    f'Failed to launch instance {instance.get("id")}')
        logger.info('Waiting for instances to be ready: '
                    f'({len(instances)}/{config.count}).')
        if len(instances_to_wait) == 0:
            break

        time.sleep(constants.POLL_INTERVAL)
    else:
        # Failed to launch config.count of instances after max retries
        msg = 'run_instances: Failed to create the instances'
        logger.warning(msg)
        raise RuntimeError(msg)
    assert head_instance is not None, 'head_instance should not be None'
    return common.ProvisionRecord(
        provider_name='CloudRift',
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=str(head_instance),
        resumed_instance_ids=[],
        created_instance_ids=created_instance_ids,
    )


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state  # unused
    # We already wait on ready state in `run_instances` no need


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    raise NotImplementedError('stop_instances is not supported for CloudRift')

def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config  # unused

    cloudrift_client = utils.get_cloudrift_client()
    exist_instances = _filter_instances(
        cluster_name_on_cloud, status_filters=['Active', "Initializing"])
    for instance_id in exist_instances:
        logger.info(f'Terminating instance {instance_id}')
        try:
            cloudrift_client.terminate_instance(instance_id)
        except Exception as e:
            logger.warning(f'Failed to terminate instance {instance_id}: {e}')


def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    """Returns head ip and worker ips of a cluster.

    Args:
        region: The region of the cluster.
        cluster_name_on_cloud: The cluster name on the cloud.
        provider_config: The provider config.

    Returns:
        A tuple of (head_ip, worker_ips).
    """
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud,
                                          ['Active', 'Initializing'])
    print("running_instances", running_instances)
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        port_mappings = instance_info.get('port_mappings', [])
        ssh_port = 22
        for port_mapping in port_mappings:
            if port_mapping[0] == 22:
                ssh_port = port_mapping[1]
                break
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=instance_info.get('internal_host_address', ''),
                external_ip=instance_info.get('host_address', ''),
                ssh_port=ssh_port,
                tags={},
            )
        ]
        if head_instance_id is None:
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='cloudrift',
        provider_config=provider_config,
    )


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """See sky/provision/__init__.py"""
    del cluster_name, provider_config  # unused
    instances = _filter_instances(cluster_name_on_cloud, status_filters=None)
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}

    status_map = {
        'Active': status_lib.ClusterStatus.UP,
        'Initializing': status_lib.ClusterStatus.INIT,
        'Inactive': None,
    }
    for instance_name, instance_meta in instances.items():
        status = instance_meta.get('status')
        # Skip terminated instances if non_terminated_only is True
        if non_terminated_only and status == 'terminated':
            continue
        # Convert the status to the SkyPilot status
        statuses[instance_name] = (status_map.get(status), None)
    return statuses


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config  # unused
    # instances = utils.filter_instances(cluster_name_on_cloud, ['running'])
    # for instance_meta in instances.values():
    #     utils.open_ports_instance(instance_meta, ports)


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    del cluster_name_on_cloud, ports, provider_config  # unused
    # No cleanup needed for CloudRift