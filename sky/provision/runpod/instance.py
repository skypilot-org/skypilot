"""GCP instance provisioning."""
import os
import time
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky import status_lib
from sky.provision import common
from sky.provision.runpod import utils
from sky.utils import command_runner
from sky.utils import subprocess_utils

POLL_INTERVAL = 5
PRIVATE_SSH_KEY_PATH = '~/.ssh/sky-key'

_GET_INTERNAL_IP_CMD = r'ip -4 -br addr show | grep UP | grep -Eo "(10\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)|172\.(1[6-9]|2[0-9][0-9]?|3[0-1]))\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"'  # pylint: disable=line-too-long

logger = sky_logging.init_logger(__name__)


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]]) -> Dict[str, Any]:

    def _get_internal_ip(node: Dict[str, Any]):
        # TODO(ewzeng): cache internal ips in metadata file to reduce
        # ssh overhead.
        if node.get('ip') is None:
            node['ip'] = None
            node['internal_ip'] = None
            return
        runner = command_runner.SSHCommandRunner(
            node['ip'],
            'root',
            os.path.expanduser(PRIVATE_SSH_KEY_PATH),
            port=node['ssh_port'])
        retry_cnt = 0
        while True:
            rc, stdout, stderr = runner.run(_GET_INTERNAL_IP_CMD,
                                            require_outputs=True,
                                            stream_logs=False)
            if not rc or rc != 255:
                break
            if retry_cnt >= 3:
                if rc != 255:
                    break
                # If we fail to connect the node for 3 times, it is likely that:
                # 1. The node is terminated.
                # 2. We are on the same node as the node we are trying to
                #    connect to, and runpod does not allow ssh to itself.
                # In both cases, we can safely set the internal ip to None.
                node['internal_ip'] = None
                return
            time.sleep(1)
        subprocess_utils.handle_returncode(
            rc,
            _GET_INTERNAL_IP_CMD,
            'Failed get obtain private IP from node',
            stderr=stdout + stderr)
        node['internal_ip'] = stdout.strip()

    instances = utils.list_instances()
    possible_names = [
        f'{cluster_name_on_cloud}-head', f'{cluster_name_on_cloud}-worker'
    ]

    filtered_nodes = {}
    for instance_id, instance in instances.items():
        if status_filters is not None and instance[
                'status'] not in status_filters:
            continue
        if instance.get('name') in possible_names:
            filtered_nodes[instance_id] = instance
    subprocess_utils.run_in_parallel(_get_internal_ip,
                                     list(filtered_nodes.values()))
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

    pending_status = ['CREATED', 'RESTARTING']

    while True:
        instances = _filter_instances(cluster_name_on_cloud, pending_status)
        if not instances:
            break
        logger.info(f'Waiting for {len(instances)} instances to be ready.')
        time.sleep(POLL_INTERVAL)
    exist_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
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
        return common.ProvisionRecord(provider_name='runpod',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    created_instance_ids = []
    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        instance_id = utils.launch(
            name=f'{cluster_name_on_cloud}-{node_type}',
            instance_type=config.node_config['InstanceType'],
            region=region)
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
        if ready_instance_cnt == config.count:
            break

        logger.info('Waiting for instances to be ready '
                    f'({len(instances)}/{config.count}).')
        time.sleep(POLL_INTERVAL)
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(provider_name='runpod',
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
    raise NotImplementedError()


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud, None)
    for inst_id, inst in instances.items():
        if worker_only and inst['name'].endswith('-head'):
            continue
        utils.remove(inst_id)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region, provider_config
    nodes = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    instances: Dict[str, common.InstanceInfo] = {}
    head_instance_id = None
    for node_id, node_info in nodes.items():
        instances[node_id] = common.InstanceInfo(
            instance_id=node_id,
            internal_ip=node_info['internal_ip'],
            external_ip=node_info['ip'],
            ssh_port=node_info['ssh_port'],
            tags={},
        )
        if node_info['name'].endswith('-head'):
            head_instance_id = node_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
    )


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud, None)

    status_map = {
        'CREATED': status_lib.ClusterStatus.INIT,
        'RESTARTING': status_lib.ClusterStatus.INIT,
        'PAUSED': status_lib.ClusterStatus.INIT,
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
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del cluster_name_on_cloud, provider_config
    pass
