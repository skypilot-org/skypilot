"""Yotta instance provisioning."""
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.yotta import yotta_utils
from sky.provision.yotta.yotta_utils import PodStatusEnum
from sky.provision.yotta.yotta_utils import yotta_client
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import ux_utils

POLL_INTERVAL = 10
QUERY_PORTS_TIMEOUT_SECONDS = 30

logger = sky_logging.init_logger(__name__)

HEAD_NODE_SUFFIX = '-head'
WORKER_NODE_SUFFIX = '-worker'


def _format_instances(instances: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{
        'id': inst_id,
        'podName': inst.get('podName'),
        'status': inst.get('status')
    } for inst_id, inst in instances.items()]


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[PodStatusEnum]] = None,
                      head_only: bool = False) -> Dict[str, Any]:

    instances = yotta_client.list_instances(cluster_name_on_cloud)
    possible_names = [f'{cluster_name_on_cloud}{HEAD_NODE_SUFFIX}']
    if not head_only:
        possible_names.append(f'{cluster_name_on_cloud}{WORKER_NODE_SUFFIX}')
    logger.debug(f'Possible names: {possible_names}')
    filtered_instances = {}
    for instance_id, instance in instances.items():
        status = instance.get('status')
        logger.debug(f'Instance {instance_id} status: {status}')
        try:
            instance_status = PodStatusEnum(status)
        except ValueError:
            logger.warning(f'Unknown pod status: {status}')
            continue
        logger.debug(f'Instance {instance_id} status: {instance_status}, '
                     f'status_filters: {status_filters}')
        if (status_filters is not None and
                instance_status not in status_filters):
            continue
        logger.debug(f'Instance {instance_id} podName: '
                     f'{instance.get("podName")} add filter: '
                     f'{instance.get("podName") in possible_names}')
        if instance.get('podName') in possible_names:
            filtered_instances[instance_id] = instance
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    head_instance_id = None
    for inst_id, inst in instances.items():
        if inst['name'].endswith(HEAD_NODE_SUFFIX):
            head_instance_id = inst_id
            break
    return head_instance_id


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    del cluster_name  # unused

    exist_instances = _filter_instances(
        cluster_name_on_cloud,
        [PodStatusEnum.RUNNING, PodStatusEnum.INITIALIZE])
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
        logger.debug(f'Cluster {cluster_name_on_cloud} already has '
                     f'{len(exist_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(provider_name='yotta',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    # 1. Create cluster
    logger.debug(f'Creating cluster {cluster_name_on_cloud}...')
    try:
        cluster_id = yotta_client.create_cluster(
            cluster_name=cluster_name_on_cloud,
            instance_type=config.node_config['InstanceType'],
            region=region,
            image_name=config.node_config['ImageId'],
            disk_size=config.node_config['DiskSize'],
            ports=config.ports_to_open_on_launch,
            public_key=config.node_config['PublicKey'],
            ssh_user=config.authentication_config['ssh_user'],
            node_num=config.count,
        )
    except Exception as e:
        logger.error(f'Failed to create cluster: {e}')
        raise

    # 2. Poll cluster status until RUNNING
    logger.debug(f'Polling cluster {cluster_name_on_cloud} status...')
    while True:
        status = yotta_client.get_cluster_status(cluster_id)
        if status == yotta_utils.ClusterStatusEnum.RUNNING.value:
            logger.debug(f'Cluster {cluster_name_on_cloud} is RUNNING.')
            break
        if status in [
                yotta_utils.ClusterStatusEnum.TERMINATED.value,
                yotta_utils.ClusterStatusEnum.TERMINATING.value
        ]:
            logger.error(
                f'Cluster {cluster_name_on_cloud} is TERMINATING or TERMINATED.'
            )
            raise RuntimeError(f'Cluster {cluster_name_on_cloud} is {status}')
        logger.debug(f'Cluster {cluster_name_on_cloud} status: {status}. '
                     'Waiting...')
        time.sleep(20)

    # 3. Create pods (instances)
    created_instance_ids = []
    to_start_count = config.count - len(exist_instances)
    for _ in range(to_start_count):
        node_suffix = (HEAD_NODE_SUFFIX
                       if head_instance_id is None else WORKER_NODE_SUFFIX)
        name = f'{cluster_name_on_cloud}{node_suffix}'
        try:
            instance_id = yotta_client.launch(
                cluster_name=cluster_name_on_cloud,
                cluster_id=cluster_id,
                name=name,
                image_name=config.node_config['ImageId'],
                docker_login_config=config.provider_config.get(
                    'docker_login_config'),
                ports=config.ports_to_open_on_launch,
                public_key=config.node_config['PublicKey'],
            )
        except Exception as e:
            logger.error(f'Failed to launch instance: {e}')
            raise
        logger.debug(f'Launched instance {instance_id}.')
        created_instance_ids.append(instance_id)
        if head_instance_id is None:
            head_instance_id = instance_id

    # Wait for instances to be ready.
    while True:
        instances = _filter_instances(cluster_name_on_cloud)
        logger.debug(f'Waiting for instances to be ready: '
                     f'{_format_instances(instances)}')
        ready_instance_cnt = 0

        for instance_id, instance in instances.items():
            if instance.get('status') in [
                    yotta_utils.PodStatusEnum.TERMINATED.value,
                    yotta_utils.PodStatusEnum.FAILED.value
            ]:
                logger.error(f'Instance {instance_id} is TERMINATED or FAILED.')
                raise RuntimeError(
                    f'Instance {instance_id} is {instance.get("status")}.')
            port = yotta_utils.get_ssh_port(instance)
            logger.debug(f'Instance {instance_id} port: {port}.')
            if port is not None and port.get('healthy'):
                ready_instance_cnt += 1
        logger.debug('Waiting for instances to be ready: '
                     f'({ready_instance_cnt}/{config.count}).')
        if ready_instance_cnt == config.count:
            break

        time.sleep(POLL_INTERVAL)
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(provider_name='yotta',
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
    del provider_config  # unused
    if worker_only:
        # TODO: support worker_only termination
        logger.warning('worker_only termination is not supported on Yotta. '
                       'Terminating the whole cluster.')
    try:
        logger.debug(f'Terminating cluster {cluster_name_on_cloud}...')
        yotta_client.terminate_instances(cluster_name_on_cloud)
        while True:
            instances = _filter_instances(cluster_name_on_cloud)
            logger.debug(
                f'Cluster {cluster_name_on_cloud} terminate_instances: '
                f'{_format_instances(instances)}.')
            all_terminated = all(
                instance.get('status') in
                [PodStatusEnum.TERMINATED.value, PodStatusEnum.FAILED.value]
                for instance in instances.values())
            if all_terminated:
                logger.debug(f'Cluster {cluster_name_on_cloud} is terminated.')
                break
            time.sleep(POLL_INTERVAL)

    except Exception as e:  # pylint: disable=broad-except
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to terminate cluster {cluster_name_on_cloud}: '
                f'{common_utils.format_exception(e, use_bracket=False)}') from e


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud,
                                          [PodStatusEnum.RUNNING])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        port = yotta_utils.get_ssh_port(instance_info)
        external_ip = port.get('host')
        internal_ip = instance_info.get('internalIp')
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=internal_ip,
                external_ip=external_ip,
                ssh_port=port.get('proxyPort'),
                tags={},
            )
        ]
        if instance_info['podName'].endswith(HEAD_NODE_SUFFIX):
            head_instance_id = instance_id
    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='yotta',
        provider_config=provider_config,
        custom_ray_options={'use_external_ip': True},
    )


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional[status_lib.ClusterStatus], Optional[str]]]:
    """See sky/provision/__init__.py"""
    del cluster_name, retry_if_missing  # unused
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud)
    status_map = {
        PodStatusEnum.INITIALIZE: status_lib.ClusterStatus.INIT,
        PodStatusEnum.RUNNING: status_lib.ClusterStatus.UP,
        PodStatusEnum.TERMINATING: status_lib.ClusterStatus.UP,
        PodStatusEnum.TERMINATED: status_lib.ClusterStatus.STOPPED,
        PodStatusEnum.FAILED: status_lib.ClusterStatus.STOPPED,
        # not support pause just mapping status
        PodStatusEnum.PAUSING: status_lib.ClusterStatus.UP,
        PodStatusEnum.PAUSED: status_lib.ClusterStatus.STOPPED,
    }
    statuses: Dict[str, Tuple[Optional[status_lib.ClusterStatus],
                              Optional[str]]] = {}
    for inst_id, instance in instances.items():
        status = status_map[PodStatusEnum(instance.get('status'))]
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


def query_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    head_ip: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[int, List[common.Endpoint]]:
    """See sky/provision/__init__.py"""
    del head_ip, provider_config  # Unused.
    # Yotta ports sometimes take a while to be ready.
    start_time = time.time()
    ports_to_query = resources_utils.port_ranges_to_set(ports)
    while True:
        instances = _filter_instances(cluster_name_on_cloud, head_only=True)
        # don't support multiple instances
        assert len(instances) <= 1
        # It is possible that the instance is terminated on console by
        # the user. In this case, the instance will not be found, and we
        # should return an empty dict.
        if not instances:
            return {}
        head_instance = list(instances.values())[0]
        ready_ports: Dict[int, List[common.Endpoint]] = {
            port: [common.SocketEndpoint(**endpoint)]
            for port, endpoint in head_instance['port2endpoint'].items()
            if port in ports_to_query
        }
        not_ready_ports = ports_to_query - set(ready_ports.keys())
        if not not_ready_ports:
            return ready_ports
        if time.time() - start_time > QUERY_PORTS_TIMEOUT_SECONDS:
            logger.warning(f'Querying ports {ports} timed out. Ports '
                           f'{not_ready_ports} are not ready.')
            return ready_ports
        time.sleep(1)
