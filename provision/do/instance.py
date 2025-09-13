"""DigitalOcean instance provisioning."""

import time
from typing import Any, Dict, List, Optional
import uuid

from sky import sky_logging
from sky.provision import common
from sky.provision.do import constants
from sky.provision.do import utils
from sky.utils import status_lib

# The maximum number of times to poll for the status of an operation
MAX_POLLS = 60 // constants.POLL_INTERVAL
# Stopping instances can take several minutes, so we increase the timeout
MAX_POLLS_FOR_UP_OR_STOP = MAX_POLLS * 8

logger = sky_logging.init_logger(__name__)


def _get_head_instance(
        instances: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for instance_name, instance_meta in instances.items():
        if instance_name.endswith('-head'):
            return instance_meta
    return None


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""

    pending_status = ['new']
    newly_started_instances = utils.filter_instances(cluster_name_on_cloud,
                                                     pending_status + ['off'])
    while True:
        instances = utils.filter_instances(cluster_name_on_cloud,
                                           pending_status)
        if not instances:
            break
        instance_statuses = [
            instance['status'] for instance in instances.values()
        ]
        logger.info(f'Waiting for {len(instances)} instances to be ready: '
                    f'{instance_statuses}')
        time.sleep(constants.POLL_INTERVAL)

    exist_instances = utils.filter_instances(cluster_name_on_cloud,
                                             status_filters=pending_status +
                                             ['active', 'off'])
    if len(exist_instances) > config.count:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')

    stopped_instances = utils.filter_instances(cluster_name_on_cloud,
                                               status_filters=['off'])
    for instance in stopped_instances.values():
        utils.start_instance(instance)
    for _ in range(MAX_POLLS_FOR_UP_OR_STOP):
        instances = utils.filter_instances(cluster_name_on_cloud, ['off'])
        if len(instances) == 0:
            break
        num_stopped_instances = len(stopped_instances)
        num_restarted_instances = num_stopped_instances - len(instances)
        logger.info(
            f'Waiting for {num_restarted_instances}/{num_stopped_instances} '
            'stopped instances to be restarted.')
        time.sleep(constants.POLL_INTERVAL)
    else:
        msg = ('run_instances: Failed to restart all'
               'instances possibly due to to capacity issue.')
        logger.warning(msg)
        raise RuntimeError(msg)

    exist_instances = utils.filter_instances(cluster_name_on_cloud,
                                             status_filters=['active'])
    head_instance = _get_head_instance(exist_instances)
    to_start_count = config.count - len(exist_instances)
    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')
    if to_start_count == 0:
        if head_instance is None:
            head_instance = list(exist_instances.values())[0]
            utils.rename_instance(
                head_instance,
                f'{cluster_name_on_cloud}-{uuid.uuid4().hex[:4]}-head')
        assert head_instance is not None, ('`head_instance` should not be None')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(exist_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(
            provider_name='do',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,
            head_instance_id=head_instance['name'],
            resumed_instance_ids=list(newly_started_instances.keys()),
            created_instance_ids=[],
        )

    created_instances: List[Dict[str, Any]] = []
    for _ in range(to_start_count):
        instance_type = 'head' if head_instance is None else 'worker'
        instance = utils.create_instance(
            region=region,
            cluster_name_on_cloud=cluster_name_on_cloud,
            instance_type=instance_type,
            config=config)
        logger.info(f'Launched instance {instance["name"]}.')
        created_instances.append(instance)
        if head_instance is None:
            head_instance = instance

    # Wait for instances to be ready.
    for _ in range(MAX_POLLS_FOR_UP_OR_STOP):
        instances = utils.filter_instances(cluster_name_on_cloud,
                                           status_filters=['active'])
        logger.info('Waiting for instances to be ready: '
                    f'({len(instances)}/{config.count}).')
        if len(instances) == config.count:
            break

        time.sleep(constants.POLL_INTERVAL)
    else:
        # Failed to launch config.count of instances after max retries
        msg = 'run_instances: Failed to create the instances'
        logger.warning(msg)
        raise RuntimeError(msg)
    assert head_instance is not None, 'head_instance should not be None'
    return common.ProvisionRecord(
        provider_name='do',
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=head_instance['name'],
        resumed_instance_ids=list(stopped_instances.keys()),
        created_instance_ids=[
            instance['name'] for instance in created_instances
        ],
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
        all_instances = utils.filter_instances(cluster_name_on_cloud, ['off'])
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
        msg = ('Failed to delete all instances')
        logger.warning(msg)
        raise RuntimeError(msg)


def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    del region  # unused
    running_instances = utils.filter_instances(cluster_name_on_cloud,
                                               ['active'])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance: Optional[str] = None
    for instance_name, instance_meta in running_instances.items():
        if instance_name.endswith('-head'):
            head_instance = instance_name
        for net in instance_meta['networks']['v4']:
            if net['type'] == 'public':
                instance_ip = net['ip_address']
                break
        instances[instance_name] = [
            common.InstanceInfo(
                instance_id=instance_meta['name'],
                internal_ip=instance_ip,
                external_ip=instance_ip,
                ssh_port=22,
                tags={},
            )
        ]

    assert head_instance is not None, 'no head instance found'
    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance,
        provider_name='do',
        provider_config=provider_config,
    )


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
    # terminated instances are not retrieved by the
    # API making `non_terminated_only` argument moot.
    del non_terminated_only
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = utils.filter_instances(cluster_name_on_cloud,
                                       status_filters=None)

    status_map = {
        'new': status_lib.ClusterStatus.INIT,
        'archive': status_lib.ClusterStatus.INIT,
        'active': status_lib.ClusterStatus.UP,
        'off': status_lib.ClusterStatus.STOPPED,
    }
    statuses: Dict[str, Optional[status_lib.ClusterStatus]] = {}
    for instance_meta in instances.values():
        status = status_map[instance_meta['status']]
        statuses[instance_meta['name']] = status
    return statuses


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    logger.debug(
        f'Skip opening ports {ports} for DigitalOcean instances, as all '
        'ports are open by default.')
    del cluster_name_on_cloud, provider_config, ports


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del cluster_name_on_cloud, provider_config, ports
