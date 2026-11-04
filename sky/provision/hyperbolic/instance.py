"""Hyperbolic instance provisioning."""
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.hyperbolic import utils
from sky.utils import status_lib

PROVIDER_NAME = 'hyperbolic'
# Hyperbolic rate-limits API calls per key; poll gently.
POLL_INTERVAL = 10
# VM rentals can take several minutes to boot.
TIMEOUT = 900

logger = sky_logging.init_logger(__name__)

_Status = utils.HyperbolicInstanceStatus


def _is_alive(rental: Dict[str, Any]) -> bool:
    return utils.get_status(rental).to_cluster_status() is not None


def _parse_instance_type(instance_type: str) -> Tuple[str, int]:
    """Returns (Hyperbolic gpuType, GPU count) for an instance type.

    Format: {gpu_count}x-{accelerator}-{vcpus}-{memory}, e.g. 1x-H100-20-240.
    """
    parts = instance_type.split('-')
    if len(parts) < 4 or not parts[0].endswith('x'):
        raise ValueError(f'Invalid Hyperbolic instance type: {instance_type}. '
                         'Expected {gpu_count}x-{gpu}-{vcpus}-{memory}.')
    accelerator = '-'.join(parts[1:-2])
    gpu_type = utils.ACCELERATOR_TO_GPU_TYPE.get(accelerator,
                                                 accelerator.lower())
    return gpu_type, int(parts[0][:-1])


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    del cluster_name  # unused
    rentals = {
        k: v
        for k, v in utils.list_instances(cluster_name_on_cloud).items()
        if _is_alive(v)
    }
    if len(rentals) > 1:
        raise RuntimeError(f'Cluster {cluster_name_on_cloud} has '
                           f'{len(rentals)} rentals; expected at most one.')
    created_instance_ids: List[str] = []
    if rentals:
        rental_id = next(iter(rentals))
        logger.info(f'Reusing Hyperbolic rental {rental_id} for cluster '
                    f'{cluster_name_on_cloud}.')
    else:
        gpu_type, gpu_count = _parse_instance_type(
            config.node_config['InstanceType'])
        rental_id = utils.launch_instance(gpu_type, gpu_count, region,
                                          cluster_name_on_cloud)
        created_instance_ids.append(rental_id)

    def _ready(rentals: Dict[str, Dict[str, Any]]) -> bool:
        rental = rentals.get(rental_id)
        if rental is None:  # Not listed yet right after creation.
            return False
        if (not _is_alive(rental) or
                utils.get_status(rental) == _Status.FAILED):
            raise RuntimeError(f'Hyperbolic rental {rental_id} failed to start'
                               f' (status: {rental.get("status")}).')
        return (utils.get_status(rental) == _Status.RUNNING and
                bool(rental.get('sshEndpoints')))

    rental = utils.wait_for_instances(cluster_name_on_cloud, _ready, TIMEOUT,
                                      POLL_INTERVAL)[rental_id]
    return common.ProvisionRecord(provider_name=PROVIDER_NAME,
                                  cluster_name=cluster_name_on_cloud,
                                  region=rental.get('region', region),
                                  zone=None,
                                  head_instance_id=rental_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    worker_only: bool = False,
) -> None:
    """Terminate all instances in the cluster."""
    del provider_config, worker_only  # unused
    rentals = utils.list_instances(cluster_name_on_cloud)
    for rental_id, rental in rentals.items():
        if utils.get_status(rental) not in (_Status.TERMINATING,
                                            _Status.TERMINATED):
            utils.terminate_instance(rental_id)
            logger.info(f'Terminating Hyperbolic rental {rental_id}.')
    utils.wait_for_instances(
        cluster_name_on_cloud,
        lambda rentals: not any(_is_alive(r) for r in rentals.values()),
        TIMEOUT, POLL_INTERVAL)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """Returns information about the cluster."""
    del region  # unused
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    ssh_user = 'ubuntu'
    for rental_id, rental in utils.list_instances(
            cluster_name_on_cloud).items():
        endpoints = rental.get('sshEndpoints') or []
        if utils.get_status(rental) != _Status.RUNNING or not endpoints:
            continue
        endpoint = endpoints[0]
        ssh_user = endpoint.get('user') or ssh_user
        node = (rental.get('instances') or [{}])[0]
        instances[rental_id] = [
            common.InstanceInfo(
                instance_id=rental_id,
                internal_ip=node.get('internalIp') or endpoint['host'],
                external_ip=endpoint['host'],
                ssh_port=int(endpoint['port']),
                tags={},
                node_name=rental_id,
            )
        ]
        head_instance_id = head_instance_id or rental_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name=PROVIDER_NAME,
        provider_config=provider_config,
        ssh_user=ssh_user,
    )


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """Returns the status of the specified instances for Hyperbolic."""
    del cluster_name, provider_config, retry_if_missing  # unused
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for rental_id, rental in utils.list_instances(
            cluster_name_on_cloud).items():
        status = utils.get_status(rental).to_cluster_status()
        if non_terminated_only and status is None:
            continue
        statuses[rental_id] = (status, rental.get('terminationReason'))
    return statuses


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """Instances are already waited for in run_instances."""
    del region, cluster_name_on_cloud, state  # unused


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Stop running instances. Not supported for Hyperbolic."""
    raise NotImplementedError('stop_instances is not supported for Hyperbolic')


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    ports: Optional[list] = None,
) -> None:
    """Cleanup ports. Not supported for Hyperbolic."""
    raise NotImplementedError('cleanup_ports is not supported for Hyperbolic')


def cleanup_custom_multi_network(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    failover: bool = False,
) -> None:
    """Cleanup custom multi-network. Not supported for Hyperbolic."""
    raise NotImplementedError(
        'cleanup_custom_multi_network is not supported for Hyperbolic')


def open_ports(
    cluster_name_on_cloud: str,
    ports: list,
    provider_config: Optional[dict] = None,
) -> None:
    """Open ports. Not supported for Hyperbolic."""
    raise NotImplementedError('open_ports is not supported for Hyperbolic')
