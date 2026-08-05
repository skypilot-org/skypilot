"""Modal instance provisioning."""
import traceback
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.modal import utils
from sky.utils import common_utils
from sky.utils import status_lib
from sky.utils import ux_utils

POLL_INTERVAL = 5

logger = sky_logging.init_logger(__name__)


def _filter_instances(cluster_name_on_cloud: str,
                      running_only: bool = False,
                      query_tunnels: bool = True) -> Dict[str, Any]:
    """Returns instances for the cluster, optionally filtered to running only.

    ``query_tunnels`` is forwarded to ``utils.list_instances``; status-only
    callers pass False to skip the per-sandbox SSH-tunnel network lookup.
    """
    instances = utils.list_instances(cluster_name_on_cloud,
                                     query_tunnels=query_tunnels)
    if running_only:
        return {
            k: v for k, v in instances.items() if v.get('status') == 'RUNNING'
        }
    return instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    """Returns the instance ID of the head node, or None if not found."""
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

    # Idempotency: skip Sandboxes that are already running.
    exist_instances = _filter_instances(cluster_name_on_cloud,
                                        running_only=True)
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
            provider_name='modal',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,  # Modal has no fixed zones
            head_instance_id=head_instance_id,
            resumed_instance_ids=[],
            created_instance_ids=[])

    created_instance_ids = []
    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            instance_id = utils.launch(
                cluster_name=cluster_name_on_cloud,
                node_type=node_type,
                instance_type=config.node_config['InstanceType'],
                region=region,
                image_id=config.node_config.get('ImageId'),
                public_key=config.node_config['PublicKey'],
                gpu_str=config.node_config.get('GpuStr'),
                cpu=config.node_config.get('Cpu'),
                memory_mib=config.node_config.get('MemoryMiB'),
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'run_instances error: {e}\n'
                           f'Full traceback:\n{traceback.format_exc()}')
            raise
        logger.info(f'Launched Modal Sandbox {instance_id}.')
        created_instance_ids.append(instance_id)
        if head_instance_id is None:
            head_instance_id = instance_id

    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(provider_name='modal',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """No-op: run_instances already blocks via sandbox.tunnels(timeout=50)."""
    del region, cluster_name_on_cloud, state


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Always raises NotImplementedError (STOP unsupported for Modal)."""
    raise NotImplementedError()


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Terminates all Sandboxes for the cluster."""
    del provider_config  # unused
    instances = _filter_instances(cluster_name_on_cloud)
    # Attempt to terminate every Sandbox before raising, so a single failure
    # does not leave the remaining (billable) Sandboxes orphaned.
    failures = []
    for inst_id, inst in instances.items():
        logger.debug(f'Terminating Modal Sandbox {inst_id}: {inst}')
        if worker_only and inst['name'].endswith('-head'):
            continue
        try:
            utils.remove(inst_id)
        except Exception as e:  # pylint: disable=broad-except
            failures.append((inst_id, e))
    if failures:
        details = '; '.join(
            f'{inst_id}: '
            f'{common_utils.format_exception(e, use_bracket=False)}'
            for inst_id, e in failures)
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to terminate {len(failures)} Modal Sandbox(es): '
                f'{details}') from failures[0][1]


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """Re-queries live tunnel endpoint on EVERY call. Never caches host:port.

    Implements D-03: get_cluster_info must call utils.list_instances() which
    forces a fresh Sandbox.from_id() + .tunnels() RPC on each invocation,
    returning the stable (but never cached) tunnel endpoint.
    """
    del region  # unused
    # query_tunnels=True (the default, stated explicitly): get_cluster_info
    # needs the live SSH tunnel host:port for the cluster's InstanceInfo.
    running_instances = _filter_instances(cluster_name_on_cloud,
                                          running_only=True,
                                          query_tunnels=True)
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                # no separate internal IP for Modal; tunnel host is both
                internal_ip=instance_info['external_ip'],
                external_ip=instance_info['external_ip'],  # Modal tunnel host
                # Modal tunnel port (NOT 22, it's a random port)
                ssh_port=instance_info['ssh_port'],
                tags={},
                node_name=instance_info['name'],
            )
        ]
        if instance_info['name'].endswith('-head'):
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='modal',
        provider_config=provider_config,
        ssh_user='root',  # Modal containers run as root by default
    )


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """Maps Modal Sandbox states to ClusterStatus."""
    del cluster_name, provider_config, retry_if_missing  # unused
    # Status query only needs sandbox state, not the SSH tunnel endpoint, so
    # skip the slow per-sandbox tunnel lookup.
    instances = _filter_instances(cluster_name_on_cloud, query_tunnels=False)

    status_map = {
        'RUNNING': status_lib.ClusterStatus.UP,
        # Terminated sandboxes are not returned by Sandbox.list(); map anyway:
        'TERMINATED': None,
    }
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for inst_id, inst in instances.items():
        status = status_map.get(inst.get('status'))
        if non_terminated_only and status is None:
            continue
        statuses[inst_id] = (status, None)
    return statuses


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """No-op: port management is handled by Modal tunnel mechanism."""
    del cluster_name_on_cloud, ports, provider_config  # Unused.


def query_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    head_ip: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[int, List[common.Endpoint]]:
    """No-op for v1 (port management deferred to v2)."""
    del cluster_name_on_cloud, ports, head_ip, provider_config  # Unused.
    return {}
