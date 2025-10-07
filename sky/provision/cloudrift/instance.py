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


_cloudrift_client = None


def _get_cloudrift_client():
    global _cloudrift_client
    if _cloudrift_client is None:
        _cloudrift_client = utils.RiftClient()
    return _cloudrift_client

def _get_head_instance(
        instances: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for instance_name, instance_meta in instances.items():
        if instance_name.endswith('-head'):
            return instance_meta
    return None


def _filter_instances(
        cluster_name_on_cloud: str,
        status_filters: Optional[List[str]]) -> Dict[str, Dict[str, Any]]:
    cloudrift_client = _get_cloudrift_client()
    instances = cloudrift_client.list_instances()
    possible_names = [
        f'{cluster_name_on_cloud}-head',
        f'{cluster_name_on_cloud}-worker',
    ]

    filtered_instances = {}
    for instance in instances:
        if (status_filters is not None and
                instance['status'] not in status_filters):
            continue
        if instance.get('name') in possible_names:
            filtered_instances[instance['id']] = instance
    return filtered_instances


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    cloudrift_client = _get_cloudrift_client()
    del cluster_name  # unused
    pending_status = ['Initializing']
    while True:
        instances = _filter_instances(cluster_name_on_cloud,
                                      pending_status)
        if not instances:
            break
        instance_statuses = [
            instance['status'] for instance in instances.values()
        ]
        logger.info(f'Waiting for {len(instances)} instances to be ready: '
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
            head_instance_id=head_instance['name'],
            resumed_instance_ids=[],
            created_instance_ids=[],
        )
    
    def launch_node(node_type: str) -> str:
        try:
            suffix = uuid.uuid4().hex[:8]
            instance_ids = cloudrift_client.deploy_instance(
                instance_type=config.node_config['InstanceType'],
                region=region,
                name = f'{cluster_name_on_cloud}-{suffix}-{node_type}',
                ssh_keys=[config.node_config['PublicKey']],
                cmd = ""
            )
            logger.info(f'Launched {node_type} node, '
                        f'instance_id: {instance_ids[0]}')
            return instance_ids[0]
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
        instances = cloudrift_client.list_instances(instances_to_wait)
        print("list instances", instances)
        for instance in instances:
            if instance['status'] == 'Active' and instance['id'] in instances_to_wait:
                instances_to_wait.remove(instance['id'])
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
        head_instance_id=head_instance,
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
    del provider_config  # unused
    all_instances = utils.filter_instances(cluster_name_on_cloud,
                                           status_filters=None)
    num_instances = len(all_instances)

    # Request a stop on all instances
    for instance_name, instance_meta in all_instances.items():
        if worker_only and instance_name.endswith('-head'):
            num_instances -= 1
            continue
        utils.stop_instance(instance_meta)

    # Wait for instances to stop
    for _ in range(MAX_POLLS_FOR_UP_OR_STOP):
        all_instances = utils.filter_instances(cluster_name_on_cloud, ['stopped'])
        if len(all_instances) >= num_instances:
            break
        time.sleep(constants.POLL_INTERVAL)
    else:
        raise RuntimeError(f'Maximum number of polls: '
                           f'{MAX_POLLS_FOR_UP_OR_STOP} reached. '
                           f'Instance {all_instances} is still not in '
                           'STOPPED status.')


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config  # unused
    instances = utils.filter_instances(cluster_name_on_cloud,
                                       status_filters=None)
    for instance_name, instance_meta in instances.items():
        logger.debug(f'Terminating instance {instance_name}')
        if worker_only and instance_name.endswith('-head'):
            continue
        utils.down_instance(instance_meta)

    for _ in range(MAX_POLLS_FOR_UP_OR_STOP):
        instances = utils.filter_instances(cluster_name_on_cloud,
                                           status_filters=None)
        if len(instances) == 0 or len(instances) <= 1 and worker_only:
            break
        time.sleep(constants.POLL_INTERVAL)
    else:
        logger.warning(f'terminate_instances: Failed to terminate all instances '
                       f'for {cluster_name_on_cloud}. Remaining: {instances}')


def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], List[str]]:
    """Returns head ip and worker ips of a cluster.

    Args:
        region: The region of the cluster.
        cluster_name_on_cloud: The cluster name on the cloud.
        provider_config: The provider config.

    Returns:
        A tuple of (head_ip, worker_ips).
    """
    del provider_config, region  # Unused
    head_ip = None
    worker_ips = []
    instances = utils.filter_instances(cluster_name_on_cloud, ['running'])
    for instance_name, instance_meta in instances.items():
        if instance_name.endswith('-head'):
            head_ip = instance_meta.get('public_ip')
        else:
            ip = instance_meta.get('public_ip')
            if ip is not None:
                worker_ips.append(ip)
    return head_ip, worker_ips


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> List[Dict[str, Any]]:
    """See sky/provision/__init__.py"""
    del cluster_name, provider_config  # unused
    instances = utils.filter_instances(cluster_name_on_cloud, status_filters=None)
    ret = []
    for instance_name, instance_meta in instances.items():
        status = instance_meta.get('status')
        # Skip terminated instances if non_terminated_only is True
        if non_terminated_only and status == 'terminated':
            continue
        # Convert the status to the SkyPilot status
        if status == 'running':
            status = status_lib.ClusterStatus.UP
        elif status in ('stopped', 'stopping'):
            status = status_lib.ClusterStatus.STOPPED
        else:  # terminated, error, etc.
            status = status_lib.ClusterStatus.TERMINATED

        instance_meta['status'] = status
        ret.append(instance_meta)
    return ret


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