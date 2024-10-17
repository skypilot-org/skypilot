"""Paperspace instance provisioning."""

import time
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky import status_lib
from sky.provision import common
from sky.provision.paperspace import utils
from sky.utils import common_utils
from sky.utils import ux_utils

# The maximum number of times to poll for the status of an operation.
POLL_INTERVAL = 5
MAX_POLLS = 60 // POLL_INTERVAL
# Stopping instances can take several minutes, so we increase the timeout
MAX_POLLS_FOR_UP_OR_STOP = MAX_POLLS * 16

logger = sky_logging.init_logger(__name__)


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]]) -> Dict[str, Any]:
    client = utils.PaperspaceCloudClient()
    instances = client.list_instances()
    possible_names = [
        f'{cluster_name_on_cloud}-head',
        f'{cluster_name_on_cloud}-worker',
    ]

    filtered_instances = {}
    for instance in instances:
        instance_id = instance['id']
        if status_filters is not None and instance[
                'state'] not in status_filters:
            continue
        if instance.get('name') in possible_names:
            filtered_instances[instance_id] = instance
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    head_instance_id = None
    for inst_id, inst in instances.items():
        if inst['name'].endswith('-head'):
            head_instance_id = inst_id
            break
    return head_instance_id


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""

    pending_status = [
        'starting', 'restarting', 'upgrading', 'provisioning', 'stopping'
    ]
    newly_started_instances = _filter_instances(cluster_name_on_cloud,
                                                pending_status + ['off'])
    client = utils.PaperspaceCloudClient()

    while True:
        instances = _filter_instances(cluster_name_on_cloud, pending_status)
        if not instances:
            break
        instance_statuses = [
            instance['state'] for instance in instances.values()
        ]
        logger.info(f'Waiting for {len(instances)} instances to be ready: '
                    f'{instance_statuses}')
        time.sleep(POLL_INTERVAL)

    exist_instances = _filter_instances(cluster_name_on_cloud,
                                        status_filters=pending_status +
                                        ['ready', 'off'])
    if len(exist_instances) > config.count:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')

    stopped_instances = _filter_instances(cluster_name_on_cloud,
                                          status_filters=['off'])
    for instance_id in stopped_instances:
        try:
            client.start(instance_id=instance_id)
        except utils.PaperspaceCloudError as e:
            if 'This machine is currently starting.' in str(e):
                continue
            raise e
    while True:
        instances = _filter_instances(cluster_name_on_cloud,
                                      pending_status + ['off'])
        if not instances:
            break
        num_stopped_instances = len(stopped_instances)
        num_restarted_instances = num_stopped_instances - len(instances)
        logger.info(
            f'Waiting for {num_restarted_instances}/{num_stopped_instances} '
            'stopped instances to be restarted.')
        time.sleep(POLL_INTERVAL)

    exist_instances = _filter_instances(cluster_name_on_cloud,
                                        status_filters=['ready'])
    head_instance_id = _get_head_instance_id(exist_instances)
    to_start_count = config.count - len(exist_instances)
    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')
    if to_start_count == 0:
        if head_instance_id is None:
            head_instance_id = list(exist_instances.keys())[0]
            client.rename(
                instance_id=head_instance_id,
                name=f'{cluster_name_on_cloud}-head',
            )
        assert head_instance_id is not None, (
            'head_instance_id should not be None')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(exist_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(
            provider_name='paperspace',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,
            head_instance_id=head_instance_id,
            resumed_instance_ids=list(newly_started_instances.keys()),
            created_instance_ids=[],
        )

    created_instance_ids = []
    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            instance_id = client.launch(
                name=f'{cluster_name_on_cloud}-{node_type}',
                instance_type=config.node_config['InstanceType'],
                network_id=config.node_config['NetworkId'],
                region=region,
                disk_size=config.node_config['DiskSize'],
            )['data']['id']
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'run_instances error: {e}')
            raise e
        logger.info(f'Launched instance {instance_id}.')
        created_instance_ids.append(instance_id)
        if head_instance_id is None:
            head_instance_id = instance_id

    # Wait for instances to be ready.
    for _ in range(MAX_POLLS_FOR_UP_OR_STOP):
        instances = _filter_instances(cluster_name_on_cloud, ['ready'])
        logger.info('Waiting for instances to be ready: '
                    f'({len(instances)}/{config.count}).')
        if len(instances) == config.count:
            break

        time.sleep(POLL_INTERVAL)
    else:
        # Failed to launch config.count of instances after max retries
        msg = ('run_instances: Failed to create the'
               'instances due to capacity issue.')
        logger.warning(msg)
        raise RuntimeError(msg)
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(
        provider_name='paperspace',
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=head_instance_id,
        resumed_instance_ids=list(stopped_instances.keys()),
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
    client = utils.PaperspaceCloudClient()
    all_instances = _filter_instances(cluster_name_on_cloud, [
        'ready', 'serviceready', 'upgrading', 'provisioning', 'starting',
        'restarting'
    ])
    num_instances = len(all_instances)

    # Request a stop on all instances
    for instance_id, instance in all_instances.items():
        if worker_only and instance['name'].endswith('-head'):
            num_instances -= 1
            continue
        client.stop(instance_id=instance_id)

    # Wait for instances to stop
    for _ in range(MAX_POLLS_FOR_UP_OR_STOP):
        all_instances = _filter_instances(cluster_name_on_cloud, ['off'])
        if len(all_instances) >= num_instances:
            break
        time.sleep(POLL_INTERVAL)
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
    client = utils.PaperspaceCloudClient()
    instances = _filter_instances(cluster_name_on_cloud, None)
    for inst_id, inst in instances.items():
        logger.debug(f'Terminating instance {inst_id}')
        if worker_only and inst['name'].endswith('-head'):
            continue
        try:
            client.remove(inst_id)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate instance {inst_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e

    # TODO(asaiacai): Possible private network resource leakage for autodown
    if not worker_only:
        try:
            time.sleep(POLL_INTERVAL)
            network = client.get_network(network_name=cluster_name_on_cloud)
            client.delete_network(network_id=network['id'])
        except IndexError:
            logger.warning(f'Network {cluster_name_on_cloud}'
                           'already deleted')


def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud, ['ready'])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=instance_info['privateIp'],
                external_ip=instance_info['publicIp'],
                ssh_port=22,
                tags={},
            )
        ]
        if instance_info['name'].endswith('-head'):
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='paperspace',
        provider_config=provider_config,
    )


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
    del non_terminated_only
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud, None)

    status_map = {
        'starting': status_lib.ClusterStatus.INIT,
        'restarting': status_lib.ClusterStatus.INIT,
        'upgrading': status_lib.ClusterStatus.INIT,
        'provisioning': status_lib.ClusterStatus.INIT,
        'stopping': status_lib.ClusterStatus.INIT,
        'serviceready': status_lib.ClusterStatus.INIT,
        'ready': status_lib.ClusterStatus.UP,
        'off': status_lib.ClusterStatus.STOPPED,
    }
    statuses: Dict[str, Optional[status_lib.ClusterStatus]] = {}
    for inst_id, inst in instances.items():
        status = status_map[inst['state']]
        statuses[inst_id] = status
    return statuses


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    del cluster_name_on_cloud, provider_config, ports


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del cluster_name_on_cloud, provider_config, ports
