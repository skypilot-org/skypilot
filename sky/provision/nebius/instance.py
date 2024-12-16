"""Nebius instance provisioning."""
import os
import time
from time import sleep
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky import status_lib
from sky.provision import common
from sky.provision.nebius import utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import ux_utils

from nebius.sdk import SDK

PENDING_STATUS = ['STARTING', 'DELETING', 'STOPPING']

DEFAULT_NEBIUS_TOKEN_PATH = os.path.expanduser('~/.nebius/NEBIUS_IAM_TOKEN.txt')
with open(DEFAULT_NEBIUS_TOKEN_PATH, 'r') as file:
    NEBIUS_IAM_TOKEN = file.read().strip()
sdk = SDK(credentials=NEBIUS_IAM_TOKEN)

POLL_INTERVAL = 5
QUERY_PORTS_TIMEOUT_SECONDS = 30

logger = sky_logging.init_logger(__name__)



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
    # print('_filter_instances :: ', instances, filtered_instances)
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    head_instance_id = None
    for inst_id, inst in instances.items():
        if inst['name'].endswith('-head'):
            head_instance_id = inst_id
            break
    return head_instance_id

def _wait_all_pending(region: str, cluster_name_on_cloud: str) -> None:
    while True:
        instances = _filter_instances(cluster_name_on_cloud, PENDING_STATUS)
        if not instances:
            break
        logger.info(f'Waiting for {len(instances)} instances to be ready.')
        time.sleep(POLL_INTERVAL)

def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    # print('TRY TO RUN INSTANCES')
    #  pr="project-e00w18sheap5emdjx8", name
    # service = ClusterServiceClient(sdk)
    # result = service.get_by_name(GetByNameRequest(name=cluster_name_on_cloud)).wait()
    # print('RESULT::', result)
    #
    # service = NodeGroupServiceClient(sdk)
    # result = service.list(ListNodeGroupsRequest(
    #     parent_id=result.id
    # ))
    # print('config', config)
    # print('----')
    # print('node_config', config.node_config)
    _wait_all_pending(region, cluster_name_on_cloud)
    running_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    head_instance_id = _get_head_instance_id(running_instances)
    to_start_count = config.count - len(running_instances)
    # print('to_start_count', to_start_count)
    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(running_instances)} nodes, but {config.count} are required.')
    if to_start_count == 0:
        if head_instance_id is None:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no head node.')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(running_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(provider_name='nebius',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    created_instance_ids = []
    resumed_instance_ids = []
    stopped_instances = _filter_instances(cluster_name_on_cloud, ['STOPPED'])
    for stopped_instance in stopped_instances.keys():
        if to_start_count > 0:
            try:
                utils.start(stopped_instance)
                resumed_instance_ids.append(stopped_instance)
                to_start_count-=1
                if stopped_instances[stopped_instance]['name'].endswith('-head'):
                    head_instance_id = stopped_instance
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f'Start instance error: {e}')
                raise
            sleep(POLL_INTERVAL)  # to avoid fake STOPPED status
            logger.info(f'Started instance {stopped_instance}.')

    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            instance_id = utils.launch(
                name=f'{cluster_name_on_cloud}-{node_type}',
                instance_type=config.node_config['InstanceType'],
                region=region,
                image_name=config.node_config['ImageId'],
                disk_size=config.node_config['DiskSize'],
                public_key=config.node_config['PublicKey'])
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'run_instances error: {e}')
            raise
        logger.info(f'Launched instance {instance_id}.')
        created_instance_ids.append(instance_id)
        if head_instance_id is None:
            head_instance_id = instance_id
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(provider_name='nebius',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=resumed_instance_ids,
                                  created_instance_ids=created_instance_ids)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    _wait_all_pending(region, cluster_name_on_cloud)


def stop_instances(
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None,
        worker_only: bool = False,
) -> None:
    exist_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    for instance in exist_instances:
        utils.stop(instance)


def terminate_instances(
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None,
        worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config  # unused
    instances = _filter_instances(cluster_name_on_cloud, None)
    for inst_id, inst in instances.items():
        logger.debug(f'Terminating instance {inst_id}: {inst}')
        if worker_only and inst['name'].endswith('-head'):
            continue
        try:
            utils.remove(inst_id)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate instance {inst_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:

    _wait_all_pending(region, cluster_name_on_cloud)
    running_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=instance_info['internal_ip'],
                external_ip=instance_info['external_ip'],
                ssh_port=instance_info['ssh_port'],
                tags={},
            )
        ]
        if instance_info['name'].endswith('-head'):
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='nebius',
        provider_config=provider_config,
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
        'STARTING': status_lib.ClusterStatus.INIT,
        'RUNNING': status_lib.ClusterStatus.UP,
        'STOPPED': status_lib.ClusterStatus.STOPPED,
        'STOPPING': status_lib.ClusterStatus.STOPPED,
        'DELETING': status_lib.ClusterStatus.STOPPED,
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


def query_ports(
        cluster_name_on_cloud: str,
        ports: List[str],
        head_ip: Optional[str] = None,
        provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[int, List[common.Endpoint]]:
    """See sky/provision/__init__.py"""
    del head_ip, provider_config  # Unused.
    # RunPod ports sometimes take a while to be ready.
    start_time = time.time()
    ports_to_query = resources_utils.port_ranges_to_set(ports)
    while True:
        instances = _filter_instances(cluster_name_on_cloud,
                                      None,
                                      head_only=True)
        assert len(instances) <= 1
        # It is possible that the instance is terminated on console by
        # the user. In this case, the instance will not be found and we
        # should return an empty dict.
        if not instances:
            return {}
        head_inst = list(instances.values())[0]
        ready_ports: Dict[int, List[common.Endpoint]] = {
            port: [common.SocketEndpoint(**endpoint)]
            for port, endpoint in head_inst['port2endpoint'].items()
            if port in ports_to_query
        }
        not_ready_ports = ports_to_query - set(ready_ports.keys())
        if not not_ready_ports:
            return ready_ports
        if time.time() - start_time > QUERY_PORTS_TIMEOUT_SECONDS:
            logger.warning(f'Querying ports {ports} timed out. Ports '
                           f'{not_ready_ports} are not ready.')
            return ready_ports
        time.sleep(POLL_INTERVAL)
