"""Vast instance provisioning."""
import time
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky import status_lib
from sky.provision import common
from sky.provision.vast import utils
from sky.utils import common_utils
from sky.utils import ux_utils

POLL_INTERVAL = 10

logger = sky_logging.init_logger(__name__)
# a much more convenient method
status_filter = lambda machine_dict, stat_list: {
    k: v for k, v in machine_dict.items() if v['status'] in stat_list
}


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]],
                      head_only: bool = False) -> Dict[str, Any]:

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
    for inst_id, inst in instances.items():
        if inst['name'].endswith('-head'):
            return inst_id
    return None


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    pending_status = ['CREATED', 'RESTARTING']

    created_instance_ids = []
    instances: Dict[str, Any] = {}

    while True:
        instances = _filter_instances(cluster_name_on_cloud, None)
        if not status_filter(instances, pending_status):
            break
        logger.info(f'Waiting for {len(instances)} instances to be ready.')
        time.sleep(POLL_INTERVAL)

    running_instances = status_filter(instances, ['RUNNING'])
    head_instance_id = _get_head_instance_id(running_instances)
    stopped_instances = status_filter(instances, ['EXITED', 'STOPPED'])

    if config.resume_stopped_nodes and stopped_instances:
        for instance in stopped_instances.values():
            utils.start(instance['id'])
    else:
        to_start_count = config.count - (len(running_instances) +
                                         len(stopped_instances))
        if to_start_count < 0:
            raise RuntimeError(f'Cluster {cluster_name_on_cloud} already has '
                               f'{len(running_instances)} nodes,'
                               f'but {config.count} are required.')
        if to_start_count == 0:
            if head_instance_id is None:
                raise RuntimeError(
                    f'Cluster {cluster_name_on_cloud} has no head node.')
            logger.info(
                f'Cluster {cluster_name_on_cloud} already has '
                f'{len(running_instances)} nodes, no need to start more.')
            return common.ProvisionRecord(provider_name='vast',
                                          cluster_name=cluster_name_on_cloud,
                                          region=region,
                                          zone=None,
                                          head_instance_id=head_instance_id,
                                          resumed_instance_ids=[],
                                          created_instance_ids=[])

        for _ in range(to_start_count):
            node_type = 'head' if head_instance_id is None else 'worker'
            try:
                instance_id = utils.launch(
                    name=f'{cluster_name_on_cloud}-{node_type}',
                    instance_type=config.node_config['InstanceType'],
                    region=region,
                    disk_size=config.node_config['DiskSize'],
                    preemptible=config.node_config['Preemptible'],
                    image_name=config.node_config['ImageId'])
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f'run_instances error: {e}')
                raise
            logger.info(f'Launched instance {instance_id}.')
            created_instance_ids.append(instance_id)
            if head_instance_id is None:
                head_instance_id = instance_id

    # Wait for instances to be ready.
    while True:
        instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
        ready_instance_cnt = 0
        for instance_id, instance in instances.items():
            if instance.get('ssh_port') is not None:
                ready_instance_cnt += 1
        logger.info('Waiting for instances to be ready: '
                    f'({ready_instance_cnt}/{config.count}).')
        if ready_instance_cnt == config.count:
            break

        time.sleep(POLL_INTERVAL)

    head_instance_id = _get_head_instance_id(utils.list_instances())
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(provider_name='vast',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    return action_instances('stop', cluster_name_on_cloud, provider_config,
                            worker_only)


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    return action_instances('remove', cluster_name_on_cloud, provider_config,
                            worker_only)


def action_instances(
    fn: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config  # unused
    instances = _filter_instances(cluster_name_on_cloud, None)
    for inst_id, inst in instances.items():
        logger.debug(f'Instance {fn} {inst_id}: {inst}')
        if worker_only and inst['name'].endswith('-head'):
            continue
        try:
            getattr(utils, fn)(inst_id)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to {fn} instance {inst_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=instance_info['local_ipaddrs'].strip(),
                external_ip=instance_info['public_ipaddr'],
                ssh_port=instance_info['ports']['22/tcp'][0]['HostPort'],
                tags={},
            )
        ]
        if instance_info['name'].endswith('-head'):
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='vast',
        provider_config=provider_config,
    )


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    raise NotImplementedError('open_ports is not supported for Vast')


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""

    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud, None)
    # "running", "frozen", "stopped", "unknown", "loading"
    status_map = {
        'LOADING': status_lib.ClusterStatus.INIT,
        'EXITED': status_lib.ClusterStatus.STOPPED,
        'STOPPED': status_lib.ClusterStatus.STOPPED,
        'RUNNING': status_lib.ClusterStatus.UP,
    }
    statuses: Dict[str, Optional[status_lib.ClusterStatus]] = {}
    for inst_id, inst in instances.items():
        status = status_map[inst['status']]
        if non_terminated_only and status is None:
            continue
        statuses[inst_id] = status
    return statuses


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del cluster_name_on_cloud, ports, provider_config  # Unused.
