"""Lambda Cloud instance provisioning."""

import time
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.lambda_cloud import lambda_utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import ux_utils

POLL_INTERVAL = 1

logger = sky_logging.init_logger(__name__)
_lambda_client = None


def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = lambda_utils.LambdaCloudClient()
    return _lambda_client


def _filter_instances(
        cluster_name_on_cloud: str,
        status_filters: Optional[List[str]]) -> Dict[str, Dict[str, Any]]:
    lambda_client = _get_lambda_client()
    instances = lambda_client.list_instances()
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


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    head_instance_id = None
    for instance_id, instance in instances.items():
        if instance['name'].endswith('-head'):
            head_instance_id = instance_id
            break
    return head_instance_id


def _get_private_ip(instance_info: Dict[str, Any], single_node: bool) -> str:
    private_ip = instance_info.get('private_ip')
    if private_ip is None:
        if single_node:
            # The Lambda cloud API may return an instance info without
            # private IP. It does not align with their docs, but we still
            # allow single-node cluster to proceed with provisioning, by using
            # 127.0.0.1, as private IP is not critical for single-node case.
            return '127.0.0.1'
        msg = f'Failed to retrieve private IP for instance {instance_info}.'
        logger.error(msg)
        raise RuntimeError(msg)
    return private_ip


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster"""
    del cluster_name  # unused
    lambda_client = _get_lambda_client()
    pending_status = ['booting']
    while True:
        instances = _filter_instances(cluster_name_on_cloud, pending_status)
        if not instances:
            break
        logger.info(f'Waiting for {len(instances)} instances to be ready.')
        time.sleep(POLL_INTERVAL)
    exist_instances = _filter_instances(cluster_name_on_cloud, ['active'])
    head_instance_id = _get_head_instance_id(exist_instances)

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
        return common.ProvisionRecord(
            provider_name='lambda',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,
            head_instance_id=head_instance_id,
            resumed_instance_ids=[],
            created_instance_ids=[],
        )

    created_instance_ids = []
    remote_ssh_key_name = config.authentication_config['remote_key_name']

    def launch_node(node_type: str) -> str:
        try:
            instance_ids = lambda_client.create_instances(
                instance_type=config.node_config['InstanceType'],
                region=region,
                name=f'{cluster_name_on_cloud}-{node_type}',
                # Quantity cannot actually be greater than 1; see:
                # https://github.com/skypilot-org/skypilot/issues/7084
                quantity=1,
                ssh_key_name=remote_ssh_key_name,
            )
            logger.info(f'Launched {node_type} node, '
                        f'instance_id: {instance_ids[0]}')
            return instance_ids[0]
        except Exception as e:
            logger.warning(f'run_instances error: {e}')
            raise

    if head_instance_id is None:
        head_instance_id = launch_node('head')
        created_instance_ids.append(head_instance_id)

    assert head_instance_id is not None, 'head_instance_id should not be None'

    worker_node_count = to_start_count - 1
    if worker_node_count > 0:
        for _ in range(worker_node_count):
            worker_instance_id = launch_node('worker')
            created_instance_ids.append(worker_instance_id)

    while True:
        instances = _filter_instances(cluster_name_on_cloud, ['active'])
        if len(instances) == config.count:
            break

        time.sleep(POLL_INTERVAL)

    return common.ProvisionRecord(
        provider_name='lambda',
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=head_instance_id,
        resumed_instance_ids=[],
        created_instance_ids=created_instance_ids,
    )


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state  # Unused.


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    raise NotImplementedError(
        'stop_instances is not supported for Lambda Cloud')


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config
    lambda_client = _get_lambda_client()
    instances = _filter_instances(cluster_name_on_cloud, None)

    instance_ids_to_terminate = []
    for instance_id, instance in instances.items():
        if worker_only and not instance['name'].endswith('-worker'):
            continue
        instance_ids_to_terminate.append(instance_id)

    try:
        logger.debug(
            f'Terminating instances {", ".join(instance_ids_to_terminate)}')
        lambda_client.remove_instances(instance_ids_to_terminate)
    except Exception as e:  # pylint: disable=broad-except
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to terminate instances {instance_ids_to_terminate}: '
                f'{common_utils.format_exception(e, use_bracket=False)}') from e


def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud, ['active'])
    single_node = len(running_instances) == 1
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=_get_private_ip(instance_info, single_node),
                external_ip=instance_info['ip'],
                ssh_port=22,
                tags={},
            )
        ]
        if instance_info['name'].endswith('-head'):
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='lambda',
        provider_config=provider_config,
    )


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """See sky/provision/__init__.py"""
    del cluster_name, retry_if_missing  # unused
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud, None)

    status_map = {
        'booting': status_lib.ClusterStatus.INIT,
        'active': status_lib.ClusterStatus.UP,
        'unhealthy': status_lib.ClusterStatus.INIT,
        'terminating': None,
    }
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for instance_id, instance in instances.items():
        status = status_map.get(instance['status'])
        if non_terminated_only and status is None:
            continue
        statuses[instance_id] = (status, None)
    return statuses


def open_ports(cluster_name_on_cloud: str,
               ports: List[str],
               provider_config: Optional[Dict[str, Any]] = None) -> None:
    """Open firewall ports for Lambda Cloud.

    Args:
        cluster_name_on_cloud: Cluster name on Lambda Cloud.
        ports: List of ports to open.
        provider_config: Lambda Cloud provider config. Contains the API key.
    """
    if not ports:
        return

    # Skip port opening for us-south-1 region where it's not supported
    region = None
    if provider_config is not None:
        region = provider_config.get('region')

    # Skip port opening for us-south-1, as it's not supported by Lambda Cloud
    # https://cloud.lambda.ai/api/v1/docs#get-/api/v1/firewall-rules
    if region == 'us-south-1':
        logger.warning(
            f'Skipping port opening for cluster {cluster_name_on_cloud} in '
            f'us-south-1 region, as firewall rules are not supported there.')
        return

    del provider_config, cluster_name_on_cloud  # No longer needed

    lambda_client = lambda_utils.LambdaCloudClient()

    # Get existing rules to avoid duplicates
    existing_rules = lambda_client.list_firewall_rules()
    existing_ports = set()
    for rule in existing_rules:
        port_range = rule.get('port_range')
        if rule.get('protocol') == 'tcp' and port_range is not None:
            if len(port_range) == 1:
                # For single ports
                existing_ports.add(port_range[0])
            elif len(port_range) == 2:
                # For port ranges, add all ports in the range
                existing_ports.update(range(port_range[0], port_range[1] + 1))

    # Convert port strings to a set of individual ports
    ports_to_open = resources_utils.port_ranges_to_set(ports)

    # Remove ports that are already open
    ports_to_open = ports_to_open - existing_ports

    # If no ports need to be opened, return early
    if not ports_to_open:
        return

    # Convert individual ports to consolidated ranges
    port_ranges = resources_utils.port_set_to_ranges(ports_to_open)

    # Open port ranges
    for port_range in port_ranges:
        if '-' in port_range:
            # Handle range (e.g., "1000-1010")
            start, end = map(int, port_range.split('-'))
            logger.debug(f'Opening port range {port_range}/tcp')
            try:
                lambda_client.create_firewall_rule(port_range=[start, end],
                                                   protocol='tcp')
            except lambda_utils.LambdaCloudError as e:
                logger.warning(f'Failed to open port range {port_range}: {e}')
        else:
            # Handle single port
            port = int(port_range)
            logger.debug(f'Opening port {port}/tcp')
            try:
                lambda_client.create_firewall_rule(port_range=[port, port],
                                                   protocol='tcp')
            except lambda_utils.LambdaCloudError as e:
                logger.warning(f'Failed to open port {port}: {e}')


def cleanup_ports(cluster_name_on_cloud: str,
                  ports: List[str],
                  provider_config: Optional[Dict[str, Any]] = None) -> None:
    """Skip cleanup of firewall rules.

    Lambda Cloud firewall rules are global to the account, not cluster-specific.
    We skip cleanup because rules may be used by other clusters.

    TODO(zhwu): the firewall rules may accumulate over time, and we may need
    to add a way to automatically clean them up.

    Args:
        cluster_name_on_cloud: Unused.
        ports: Unused.
        provider_config: Unused.
    """
    del cluster_name_on_cloud, ports, provider_config  # Unused.

    # Break the long line by splitting it
    logger.info('Skipping cleanup of Lambda Cloud firewall rules '
                'as they are account-wide.')
