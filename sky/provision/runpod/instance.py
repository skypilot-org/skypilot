"""RunPod instance provisioning."""
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.runpod import utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import ux_utils

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
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    head_instance_id = None
    for inst_id, inst in instances.items():
        if inst['name'].endswith('-head'):
            head_instance_id = inst_id
            break
    return head_instance_id


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    del cluster_name  # unused
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
        return common.ProvisionRecord(
            provider_name='runpod',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=config.provider_config['availability_zone'],
            head_instance_id=head_instance_id,
            resumed_instance_ids=[],
            created_instance_ids=[])

    created_instance_ids = []
    volume_mounts = config.node_config.get('VolumeMounts', [])
    network_volume_id = None
    volume_mount_path = None
    if volume_mounts:
        if len(volume_mounts) > 1:
            logger.warning(
                f'RunPod only supports one network volume mount, '
                f'but {len(volume_mounts)} are specified. Only the first one '
                f'will be used.')
        volume_mount = volume_mounts[0]
        network_volume_id = volume_mount.get('VolumeIdOnCloud')
        volume_mount_path = volume_mount.get('MountPath')
        if network_volume_id is None or volume_mount_path is None:
            raise RuntimeError(
                'Network volume ID and mount path must be specified.')
    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            instance_id = utils.launch(
                cluster_name=cluster_name_on_cloud,
                node_type=node_type,
                instance_type=config.node_config['InstanceType'],
                region=region,
                zone=config.provider_config['availability_zone'],
                disk_size=config.node_config['DiskSize'],
                image_name=config.node_config['ImageId'],
                ports=config.ports_to_open_on_launch,
                public_key=config.node_config['PublicKey'],
                preemptible=config.node_config['Preemptible'],
                bid_per_gpu=config.node_config['BidPerGPU'],
                docker_login_config=config.provider_config.get(
                    'docker_login_config'),
                network_volume_id=network_volume_id,
                volume_mount_path=volume_mount_path,
            )
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
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(
        provider_name='runpod',
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=config.provider_config['availability_zone'],
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
    instances = _filter_instances(cluster_name_on_cloud, None)
    template_name, registry_auth_id = utils.get_registry_auth_resources(
        cluster_name_on_cloud)
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
    if template_name is not None:
        utils.delete_pod_template(template_name)
    if registry_auth_id is not None:
        utils.delete_register_auth(registry_auth_id)


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
        provider_name='runpod',
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
        'CREATED': status_lib.ClusterStatus.INIT,
        'RESTARTING': status_lib.ClusterStatus.INIT,
        'PAUSED': status_lib.ClusterStatus.INIT,
        'RUNNING': status_lib.ClusterStatus.UP,
    }
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for inst_id, inst in instances.items():
        status = status_map[inst['status']]
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
        time.sleep(1)
