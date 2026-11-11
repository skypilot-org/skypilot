"""Modal provisioning, backed by Modal Functions.

Each cluster is one spawned Function call in its own detached App. Every node
is a container of that call that runs sshd behind a Modal TCP tunnel and
publishes the tunnel address to a per-cluster Modal Dict. Multi-node clusters
use `@modal.clustered`, which gives the nodes a private network.
"""

from typing import Any, Dict, List, Optional, Tuple

from sky import exceptions
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


def _environment_name(
        provider_config: Optional[Dict[str, Any]]) -> Optional[str]:
    return (provider_config or {}).get('environment_name')


def _running_cluster_state(
        cluster_name_on_cloud: str,
        environment_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """The cluster's state if its Function call is still running."""
    state = modal_utils.get_cluster_state(cluster_name_on_cloud,
                                          environment_name)
    if state is None or state['preempted']:
        return None
    if not modal_utils.call_is_running(state['call_id']):
        return None
    return state


def _function_kwargs(node_config: Dict[str, Any]) -> Dict[str, Any]:
    gpu = node_config.get('Gpu')
    cpu = node_config.get('Cpu')
    memory = node_config.get('Memory')
    volumes: Dict[str, Any] = {}
    volumes.update(
        modal_utils.get_modal_volume_mounts(node_config.get('ModalVolumes',
                                                            [])))
    volumes.update(
        modal_utils.get_cloud_bucket_mounts(
            node_config.get('CloudBucketMounts', [])))
    return {
        'image': modal_utils.get_image(node_config.get('DockerImage')),
        'gpu': gpu,
        'cpu': (cpu, cpu) if cpu is not None else None,
        'memory': (memory, memory) if memory is not None else None,
        'region': node_config.get('ModalRegion'),
        'timeout': node_config.get('Timeout'),
        'volumes': volumes,
        # CPU Functions are preemptible by default; keep CPU clusters
        # non-preemptible, at 3x the CPU/memory list price.
        # Modal does not support this for GPU Functions.
        'nonpreemptible': gpu is None,
    }


def _wait_for_nodes_or_cancel(cluster_name_on_cloud: str,
                              environment_name: Optional[str], call_id: str,
                              num_nodes: int) -> None:
    try:
        modal_utils.wait_for_nodes(cluster_name_on_cloud, environment_name,
                                   call_id, num_nodes)
    except Exception:
        modal_utils.cancel_call(call_id)
        raise


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    del cluster_name  # unused
    environment_name = _environment_name(config.provider_config)
    state = _running_cluster_state(cluster_name_on_cloud, environment_name)
    if state is not None:
        if state['num_nodes'] != config.count:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has {state["num_nodes"]} '
                f'Modal node(s), but {config.count} were requested.')
        logger.info(f'Cluster {cluster_name_on_cloud} is already running on '
                    'Modal.')
        # The call can be running with nodes still pending, e.g. a launch
        # that was interrupted while waiting for capacity.
        _wait_for_nodes_or_cancel(cluster_name_on_cloud, environment_name,
                                  state['call_id'], config.count)
        return common.ProvisionRecord(provider_name=PROVIDER_NAME,
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=modal_utils.instance_id(
                                          state['call_id'], 0),
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    user_ports = sorted(
        set(config.ports_to_open_on_launch or []) - {modal_utils.SSH_PORT})
    try:
        call_id = modal_utils.launch_cluster(
            cluster_name_on_cloud,
            environment_name,
            num_nodes=config.count,
            public_key=config.node_config['PublicKey'],
            user_ports=user_ports,
            function_kwargs=_function_kwargs(config.node_config))
    except (modal_adaptor.modal.exception.AuthError,
            modal_adaptor.modal.exception.PermissionDeniedError) as e:
        # Surfaced in the failover summary instead of a generic "failed to
        # acquire resources".
        raise exceptions.InvalidCloudCredentials(
            f'Modal rejected the credentials: {e}') from e
    except modal_adaptor.modal.exception.InvalidError as e:
        # Retrying cannot fix these, e.g. GPUs on an account without a payment
        # method, or a multi-node shape Modal does not offer: stop failover
        # instead of suggesting --retry-until-up.
        raise exceptions.ProvisionUnsupportedError(
            f'Modal rejected the request: {e}') from e
    _wait_for_nodes_or_cancel(cluster_name_on_cloud, environment_name, call_id,
                              config.count)
    logger.info(f'Launched Modal Function call {call_id} with '
                f'{config.count} node(s).')
    created = [
        modal_utils.instance_id(call_id, rank) for rank in range(config.count)
    ]
    return common.ProvisionRecord(provider_name=PROVIDER_NAME,
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=created[0],
                                  resumed_instance_ids=[],
                                  created_instance_ids=created)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    # run_instances() already waits for every node's SSH tunnel. This
    # interface does not receive provider_config, so another lookup could use
    # a different active Modal Environment than the one persisted for the
    # cluster.
    del region, cluster_name_on_cloud, state  # unused


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
    if worker_only:
        # The nodes of a clustered call cannot be stopped separately.
        return
    environment_name = _environment_name(provider_config)
    state = modal_utils.get_cluster_state(cluster_name_on_cloud,
                                          environment_name)
    if state is not None:
        call_id = state['call_id']
        try:
            logger.debug(f'Cancelling Modal Function call {call_id}.')
            modal_utils.cancel_call(call_id)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to cancel Modal Function call {call_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e
    modal_utils.delete_state(cluster_name_on_cloud, environment_name)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    state = _running_cluster_state(cluster_name_on_cloud,
                                   _environment_name(provider_config))
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    if state is not None:
        for rank, node in sorted(state['nodes'].items()):
            node_id = modal_utils.instance_id(state['call_id'], rank)
            instances[node_id] = [
                common.InstanceInfo(instance_id=node_id,
                                    internal_ip=node['internal_ip'],
                                    external_ip=node['ssh_host'],
                                    ssh_port=node['ssh_port'],
                                    tags={},
                                    node_name=node_id)
            ]
            if rank == 0:
                head_instance_id = node_id
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
    del cluster_name, retry_if_missing  # unused
    environment_name = _environment_name(provider_config)
    state = modal_utils.get_cluster_state(cluster_name_on_cloud,
                                          environment_name)
    if state is None:
        return {}
    running = (not state['preempted'] and
               modal_utils.call_is_running(state['call_id']))
    if not running:
        # The cluster cannot come back. A status refresh drops it without
        # calling terminate_instances(), so clean up here.
        if state['preempted']:
            # Only the rerun node has ended. The call can still be running,
            # e.g. the other ranks of a clustered call, and nothing could
            # find it once the state is gone.
            try:
                modal_utils.cancel_call(state['call_id'])
            except Exception as e:  # pylint: disable=broad-except
                # Do not drop the state: it is the only handle on the call.
                # The next refresh retries.
                raise RuntimeError(
                    'Failed to cancel the preempted Modal Function call '
                    f'{state["call_id"]}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e
        modal_utils.delete_state(cluster_name_on_cloud, environment_name)
        if non_terminated_only:
            return {}
    reason = 'Preempted by Modal.' if state['preempted'] else None
    status = status_lib.ClusterStatus.UP if running else None
    return {
        modal_utils.instance_id(state['call_id'], rank):
        (status, None if running else reason)
        for rank in range(state['num_nodes'])
    }


def open_ports(cluster_name_on_cloud: str,
               ports: List[str],
               provider_config: Optional[Dict[str, Any]] = None) -> None:
    del cluster_name_on_cloud, ports, provider_config  # unused
    # Tunnels are opened when the nodes start. SkyPilot passes launch-time
    # ports through ProvisionConfig.


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
    del head_ip  # unused
    state = _running_cluster_state(cluster_name_on_cloud,
                                   _environment_name(provider_config))
    head = (state or {}).get('nodes', {}).get(0)
    if head is None:
        return {}
    ports_to_query = resources_utils.port_ranges_to_set(ports)
    result: Dict[int, List[common.Endpoint]] = {}
    user_ports = sorted(ports_to_query - {modal_utils.SSH_PORT})
    for port, endpoint in modal_utils.get_port_endpoints(
            head.get('port_urls', {}), user_ports).items():
        result[port] = [endpoint]
    if modal_utils.SSH_PORT in ports_to_query:
        result[modal_utils.SSH_PORT] = [
            common.SocketEndpoint(host=head['ssh_host'], port=head['ssh_port'])
        ]
    return result
