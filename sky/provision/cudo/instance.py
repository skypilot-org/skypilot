"""Cudo Compute instance provisioning."""

import time
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky import status_lib
from sky.provision import common
from sky.provision.cudo import cudo_machine_type
from sky.provision.cudo import cudo_wrapper

POLL_INTERVAL = 10

logger = sky_logging.init_logger(__name__)


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]]) -> Dict[str, Any]:

    instances = cudo_wrapper.list_instances()
    possible_names = [
        f'{cluster_name_on_cloud}-head', f'{cluster_name_on_cloud}-worker'
    ]

    filtered_nodes = {}
    for instance_id, instance in instances.items():
        if (status_filters is not None and
                instance['status'] not in status_filters):
            continue
        if instance.get('name') in possible_names:
            filtered_nodes[instance_id] = instance
    return filtered_nodes


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

    pending_status = ['pend', 'init', 'prol', 'boot']

    while True:
        instances = _filter_instances(cluster_name_on_cloud, pending_status)
        if not instances:
            break
        logger.info(f'Waiting for {len(instances)} instances to be ready.')
        time.sleep(POLL_INTERVAL)
    exist_instances = _filter_instances(cluster_name_on_cloud, ['runn'])
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
        return common.ProvisionRecord(provider_name='cudo',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    created_instance_ids = []
    public_key = config.node_config['AuthorizedKey']

    for _ in range(to_start_count):
        instance_type = config.node_config['InstanceType']
        spec = cudo_machine_type.get_spec_from_instance(instance_type, region)

        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            instance_id = cudo_wrapper.launch(
                name=f'{cluster_name_on_cloud}-{node_type}',
                ssh_key=public_key,
                data_center_id=region,
                machine_type=spec['machine_type'],
                memory_gib=int(spec['mem_gb']),
                vcpu_count=int(spec['vcpu_count']),
                gpu_count=int(float(spec['gpu_count'])),
                gpu_model=spec['gpu_model'],
                tags={},
                disk_size=config.node_config['DiskSize'])
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'run_instances error: {e}')
            raise
        logger.info(f'Launched instance {instance_id}.')
        created_instance_ids.append(instance_id)
        if head_instance_id is None:
            head_instance_id = instance_id

    # Wait for instances to be ready.
    retries = 60  # times 10 second
    results = {}
    for instance_id in created_instance_ids:
        for _ in range(retries):
            logger.info('Waiting for instance(s) to be ready '
                        f'{instance_id}')
            vm = cudo_wrapper.get_instance(instance_id)
            if vm['vm']['short_state'] == 'runn':
                results[id] = vm['vm']
                break
            else:
                time.sleep(POLL_INTERVAL)

    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(provider_name='cudo',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    # Waiting is done in run_instances
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
    del provider_config
    instances = _filter_instances(cluster_name_on_cloud, None)
    for inst_id, inst in instances.items():
        logger.info(f'Terminating instance {inst_id}.'
                    f'{inst}')
        if worker_only and inst['name'].endswith('-head'):
            continue
        logger.info(f'Removing {inst_id}: {inst}')
        cudo_wrapper.remove(inst_id)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region
    nodes = _filter_instances(cluster_name_on_cloud, ['runn', 'pend'])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for node_id, node_info in nodes.items():
        instances[node_id] = [
            common.InstanceInfo(
                instance_id=node_id,
                internal_ip=node_info['internal_ip'],
                external_ip=node_info['external_ip'],
                tags=node_info['tags'],
            )
        ]
        if node_info['name'].endswith('-head'):
            head_instance_id = node_id

    return common.ClusterInfo(instances=instances,
                              head_instance_id=head_instance_id,
                              provider_name='cudo',
                              provider_config=provider_config)


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud, None)

    status_map = {
        'init': status_lib.ClusterStatus.INIT,
        'pend': status_lib.ClusterStatus.INIT,
        'prol': status_lib.ClusterStatus.INIT,
        'boot': status_lib.ClusterStatus.INIT,
        'runn': status_lib.ClusterStatus.UP,
        'stop': status_lib.ClusterStatus.STOPPED,
        'susp': status_lib.ClusterStatus.STOPPED,
        'done': status_lib.ClusterStatus.STOPPED,
        'poff': status_lib.ClusterStatus.STOPPED,
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
    del cluster_name_on_cloud, ports, provider_config
