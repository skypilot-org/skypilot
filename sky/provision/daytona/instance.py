"""Daytona sandbox provisioning."""

from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.daytona import utils
from sky.utils import status_lib

PROVIDER_NAME = 'daytona'

logger = sky_logging.init_logger(__name__)


def _non_terminated_sandboxes(
        cluster_name_on_cloud: str) -> Dict[str, Dict[str, Any]]:
    sandboxes = {}
    for sandbox in utils.list_cluster_sandboxes(cluster_name_on_cloud):
        status = utils.to_cluster_status(sandbox.get('state'))
        if status is None:
            continue
        sandboxes[sandbox['id']] = sandbox
    return sandboxes


def run_instances(
    region: str,
    cluster_name: str,
    cluster_name_on_cloud: str,
    config: common.ProvisionConfig,
) -> common.ProvisionRecord:
    """Provisions a sandbox for the cluster.

    Daytona clusters are single-node: one sandbox per cluster. If a
    sandbox labeled with the cluster name already exists, it is reused.
    """
    del cluster_name  # unused
    if config.count > 1:
        raise utils.DaytonaError('Daytona does not support multi-node clusters '
                                 f'(requested {config.count} nodes).')

    existing = _non_terminated_sandboxes(cluster_name_on_cloud)
    if existing:
        sandbox_id = next(iter(existing))
        logger.debug(f'Reusing existing sandbox {sandbox_id} for cluster '
                     f'{cluster_name_on_cloud}')
        utils.wait_for_sandbox_started(sandbox_id)
        return common.ProvisionRecord(
            provider_name=PROVIDER_NAME,
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,
            head_instance_id=sandbox_id,
            resumed_instance_ids=[],
            created_instance_ids=[],
        )

    node_config = config.node_config
    instance_type = node_config.get('InstanceType')
    if not instance_type:
        raise utils.DaytonaError('InstanceType is not set in node_config.')

    image_id = node_config.get('ImageId') or None
    disk_size = node_config.get('DiskSize')
    use_spot = bool(node_config.get('Preemptible', False))

    sandbox_id = utils.launch_sandbox(
        cluster_name_on_cloud=cluster_name_on_cloud,
        instance_type=instance_type,
        region=region,
        use_spot=use_spot,
        disk_size=disk_size,
        image_id=image_id,
    )
    logger.debug(f'Created sandbox {sandbox_id} for cluster '
                 f'{cluster_name_on_cloud}')
    utils.wait_for_sandbox_started(sandbox_id)

    return common.ProvisionRecord(
        provider_name=PROVIDER_NAME,
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=sandbox_id,
        resumed_instance_ids=[],
        created_instance_ids=[sandbox_id],
    )


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """Waiting is handled in run_instances(); no-op."""
    del region, cluster_name_on_cloud, state  # unused


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    worker_only: bool = False,
) -> None:
    """Deletes all sandboxes belonging to the cluster."""
    del provider_config, worker_only  # unused
    for sandbox in utils.list_cluster_sandboxes(cluster_name_on_cloud):
        sandbox_id = sandbox['id']
        logger.debug(f'Deleting sandbox {sandbox_id} for cluster '
                     f'{cluster_name_on_cloud}')
        utils.delete_sandbox(sandbox_id)


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Stopping is not supported: Daytona GPU sandboxes are deleted on
    stop, so SkyPilot treats Daytona clusters as non-stoppable."""
    raise NotImplementedError('stop_instances is not supported for Daytona.')


def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    """Returns cluster info with SSH connection details.

    Daytona SSH uses per-sandbox access tokens as the SSH username against
    the shared SSH gateway. A fresh long-lived token is issued on each
    call and returned via `ClusterInfo.ssh_user`, which overrides the
    static `auth.ssh_user` from the cluster YAML.
    """
    del region  # unused
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    ssh_user = None

    for sandbox_id, sandbox in _non_terminated_sandboxes(
            cluster_name_on_cloud).items():
        state = (sandbox.get('state') or '').lower()
        if state != 'started':
            logger.debug(f'Skipping sandbox {sandbox_id} in state {state!r}')
            continue
        if head_instance_id is None:
            head_instance_id = sandbox_id
            ssh_user = utils.create_ssh_token(sandbox_id)
        instances[sandbox_id] = [
            common.InstanceInfo(
                instance_id=sandbox_id,
                internal_ip=utils.SSH_GATEWAY_HOST,
                external_ip=utils.SSH_GATEWAY_HOST,
                ssh_port=utils.SSH_GATEWAY_PORT,
                tags={},
            )
        ]

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
    """Returns the statuses of the cluster's sandboxes."""
    del cluster_name, provider_config, retry_if_missing  # unused
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for sandbox in utils.list_cluster_sandboxes(cluster_name_on_cloud):
        status = utils.to_cluster_status(sandbox.get('state'))
        if non_terminated_only and status is None:
            continue
        statuses[sandbox['id']] = (status, sandbox.get('errorReason'))
    return statuses


def open_ports(
    cluster_name_on_cloud: str,
    ports: list,
    provider_config: Optional[dict] = None,
) -> None:
    """Opening ports is not supported for Daytona."""
    raise NotImplementedError('open_ports is not supported for Daytona.')


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    ports: Optional[list] = None,
) -> None:
    """Cleanup ports. Not supported for Daytona."""
    raise NotImplementedError('cleanup_ports is not supported for Daytona.')


def cleanup_custom_multi_network(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    failover: bool = False,
) -> None:
    """Cleanup custom multi-network. Not supported for Daytona."""
    raise NotImplementedError(
        'cleanup_custom_multi_network is not supported for Daytona.')
