"""Modal Sandbox provisioning."""

from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.adaptors import modal as modal_adaptor
from sky.provision import common
from sky.provision.modal import modal_utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import ux_utils

PROVIDER_NAME = 'modal'

logger = sky_logging.init_logger(__name__)


def _filter_instances(
        cluster_name_on_cloud: str,
        status_filters: Optional[List[status_lib.ClusterStatus]]
) -> Dict[str, Any]:
    sandboxes = modal_utils.get_active_sandboxes_by_name(cluster_name_on_cloud)
    if status_filters is None:
        return sandboxes
    filtered = {}
    for sandbox_id, sandbox in sandboxes.items():
        status = _sandbox_to_cluster_status(sandbox)
        if status in status_filters:
            filtered[sandbox_id] = sandbox
    return filtered


def _sandbox_to_cluster_status(sandbox) -> Optional[status_lib.ClusterStatus]:
    exit_code = modal_utils.sandbox_status(sandbox)
    if exit_code is None:
        return status_lib.ClusterStatus.UP
    return None


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    if not instances:
        return None
    return next(iter(instances.keys()))


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    del cluster_name  # unused
    if config.count != 1:
        raise RuntimeError('Modal only supports single-node clusters.')

    active_instances = _filter_instances(cluster_name_on_cloud,
                                         [status_lib.ClusterStatus.UP])
    if len(active_instances) > 1:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} has multiple Modal Sandboxes.')
    if active_instances:
        head_instance_id = _get_head_instance_id(active_instances)
        assert head_instance_id is not None
        logger.info(f'Cluster {cluster_name_on_cloud} already has an active '
                    'Modal Sandbox.')
        return common.ProvisionRecord(provider_name=PROVIDER_NAME,
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    app = modal_utils.get_app(create_if_missing=True)
    public_key = config.node_config['PublicKey']
    modal_region = config.node_config.get('ModalRegion')
    modal_gpu = config.node_config.get('Gpu')
    modal_cpu = config.node_config.get('Cpu')
    modal_memory = config.node_config.get('Memory')
    modal_timeout = config.node_config.get('Timeout')
    modal_idle_timeout = config.node_config.get('IdleTimeout')
    modal_docker_image = config.node_config.get('DockerImage')
    modal_volume_mounts = config.node_config.get('ModalVolumes', [])
    modal_bucket_mounts = config.node_config.get('CloudBucketMounts', [])
    sandbox_volumes = {}
    sandbox_volumes.update(
        modal_utils.get_modal_volume_mounts(modal_volume_mounts))
    sandbox_volumes.update(
        modal_utils.get_cloud_bucket_mounts(modal_bucket_mounts))
    sandbox_secrets = []
    modal_env_secret = modal_utils.get_modal_env_secret()
    if modal_env_secret is not None:
        sandbox_secrets.append(modal_env_secret)
    user_ports = sorted(
        set(config.ports_to_open_on_launch or []) - {modal_utils.SSH_PORT})
    sandbox = modal_adaptor.modal.Sandbox.create(
        'bash',
        '-lc',
        modal_utils.get_ssh_start_command(public_key),
        app=app,
        name=cluster_name_on_cloud,
        tags={
            'skypilot-cluster': cluster_name_on_cloud,
        },
        image=modal_utils.get_image(modal_docker_image),
        secrets=sandbox_secrets,
        encrypted_ports=user_ports,
        unencrypted_ports=[modal_utils.SSH_PORT],
        timeout=modal_timeout,
        idle_timeout=modal_idle_timeout,
        gpu=modal_gpu,
        region=modal_region,
        cpu=modal_cpu,
        memory=modal_memory,
        volumes=sandbox_volumes,
    )
    # Ensure the SSH tunnel exists before SkyPilot starts probing SSH.
    modal_utils.get_ssh_tunnel(sandbox)
    logger.info(f'Launched Modal Sandbox {sandbox.object_id}.')
    return common.ProvisionRecord(provider_name=PROVIDER_NAME,
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=sandbox.object_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=[sandbox.object_id])


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region  # unused
    if state == status_lib.ClusterStatus.UP:
        sandbox = modal_utils.get_head_sandbox(cluster_name_on_cloud)
        if sandbox is None:
            raise RuntimeError(
                f'No active Modal Sandbox found for {cluster_name_on_cloud}.')
        modal_utils.get_ssh_tunnel(sandbox)
    elif state is None:
        return


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    del cluster_name_on_cloud, provider_config, worker_only  # unused
    raise NotImplementedError('stop_instances is not supported for Modal.')


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    del provider_config  # unused
    if worker_only:
        return
    instances = _filter_instances(cluster_name_on_cloud, None)
    for sandbox_id, sandbox in instances.items():
        try:
            logger.debug(f'Terminating Modal Sandbox {sandbox_id}.')
            sandbox.terminate(wait=True)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate Modal Sandbox {sandbox_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud,
                                          [status_lib.ClusterStatus.UP])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for sandbox_id, sandbox in running_instances.items():
        host, port = modal_utils.get_ssh_tunnel(sandbox)
        instances[sandbox_id] = [
            common.InstanceInfo(instance_id=sandbox_id,
                                internal_ip='127.0.0.1',
                                external_ip=host,
                                ssh_port=port,
                                tags={},
                                node_name=sandbox_id)
        ]
        if head_instance_id is None:
            head_instance_id = sandbox_id

    return common.ClusterInfo(instances=instances,
                              head_instance_id=head_instance_id,
                              provider_name=PROVIDER_NAME,
                              provider_config=provider_config,
                              ssh_user=modal_utils.SSH_USER)


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    del cluster_name, provider_config, retry_if_missing  # unused
    instances = _filter_instances(cluster_name_on_cloud, None)
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for sandbox_id, sandbox in instances.items():
        status = _sandbox_to_cluster_status(sandbox)
        if non_terminated_only and status is None:
            continue
        statuses[sandbox_id] = (status, None)
    return statuses


def open_ports(cluster_name_on_cloud: str,
               ports: List[str],
               provider_config: Optional[Dict[str, Any]] = None) -> None:
    del cluster_name_on_cloud, ports, provider_config  # unused
    # Modal Sandbox tunnels are immutable after Sandbox.create().
    # SkyPilot passes launch-time ports through ProvisionConfig.


def cleanup_ports(cluster_name_on_cloud: str,
                  ports: List[str],
                  provider_config: Optional[Dict[str, Any]] = None) -> None:
    del cluster_name_on_cloud, ports, provider_config  # unused


def query_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    head_ip: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[int, List[common.Endpoint]]:
    del head_ip, provider_config  # unused
    sandbox = modal_utils.get_head_sandbox(cluster_name_on_cloud)
    if sandbox is None:
        return {}

    ports_to_query = resources_utils.port_ranges_to_set(ports)
    result: Dict[int, List[common.Endpoint]] = {}
    user_ports = sorted(ports_to_query - {modal_utils.SSH_PORT})
    for port, endpoint in modal_utils.get_port_tunnels(sandbox,
                                                       user_ports).items():
        result[port] = [endpoint]
    if modal_utils.SSH_PORT in ports_to_query:
        host, port = modal_utils.get_ssh_tunnel(sandbox)
        result[modal_utils.SSH_PORT] = [
            common.SocketEndpoint(host=host, port=port)
        ]
    return result
