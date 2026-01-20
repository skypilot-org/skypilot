"""Mithril instance provisioning."""

from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.mithril import utils
from sky.provision.mithril.utils import MithrilStatus
from sky.utils import status_lib

PROVIDER_NAME = 'mithril'

logger = sky_logging.init_logger(__name__)


def _filter_instances(
    cluster_name_on_cloud: str,
    status_in: Optional[List[MithrilStatus]] = None,
    status_not_in: Optional[List[MithrilStatus]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Filter instances by cluster name and status.

    Args:
        cluster_name_on_cloud: Cluster name prefix to match.
        status_in: If provided, only include instances with these statuses.
        status_not_in: If provided, exclude instances with these statuses.
    """
    logger.debug(f'Filtering instances: cluster={cluster_name_on_cloud}, '
                 f'status_in={status_in}, status_not_in={status_not_in}')

    instances = utils.list_instances()
    filtered_instances: Dict[str, Dict[str, Any]] = {}

    for instance_id, instance in instances.items():
        instance_name = instance.get('name', '')
        if not instance_name.startswith(cluster_name_on_cloud):
            continue

        status = instance['status']
        if status_in is not None and status not in status_in:
            continue
        if status_not_in is not None and status in status_not_in:
            continue

        filtered_instances[instance_id] = instance

    logger.debug(f'Found {len(filtered_instances)} instances matching filters')
    return filtered_instances


def run_instances(
    region: str,
    cluster_name: str,
    cluster_name_on_cloud: str,
    config: common.ProvisionConfig,
) -> common.ProvisionRecord:
    """Provision instances for a Mithril cluster.

    Logic:
    1. Check for paused bid and unpause if resume_stopped_nodes is True
    2. Check for existing instances with SSH destinations → use them
    3. Check for existing instances without SSH destinations → wait for them
    4. No instances exist → launch new ones
    """
    del cluster_name  # unused
    logger.debug(f'Starting run_instances with region={region}, '
                 f'cluster={cluster_name_on_cloud}')
    logger.debug(f'Config: {config}')

    # Check if there's a paused bid that needs to be resumed
    resumed_instance_ids: List[str] = []
    if config.resume_stopped_nodes:
        bid = utils.get_bid(cluster_name_on_cloud)
        if bid:
            bid_status = bid.get('status')
            if bid_status == 'Terminated':
                raise RuntimeError(
                    f'Cluster {cluster_name_on_cloud} has been terminated '
                    'and cannot be resumed. Use \'sky down\' to clean up '
                    'and \'sky launch\' to create a new cluster.')
            if bid_status == 'Paused':
                bid_id = bid['fid']
                resumed_instance_ids = bid.get('instances', [])
                logger.debug(f'Found paused bid {bid_id}, unpausing')
                utils.update_bid(bid_id, paused=False)

    # Check for existing instances
    all_instances = _filter_instances(
        cluster_name_on_cloud,
        status_not_in=['STATUS_TERMINATED', 'STATUS_FAILED', 'STATUS_ERROR'],
    )

    # Separate instances with and without SSH destinations
    ready_instances = {}
    pending_instances = {}
    for inst_id, inst in all_instances.items():
        ssh_destination = inst['ssh_destination']
        if ssh_destination:
            ready_instances[inst_id] = inst
        else:
            pending_instances[inst_id] = inst

    logger.debug(f'Found {len(ready_instances)} ready instances, '
                 f'{len(pending_instances)} pending instances')

    # If we have pending instances, wait for them to get SSH destinations
    if pending_instances:
        logger.debug(
            f'Waiting for {len(pending_instances)} pending instances...')
        for instance_id, instance_info in pending_instances.items():
            if not utils.wait_for_ssh_ip(instance_id):
                raise utils.MithrilError(
                    f'Instance {instance_id} failed to get SSH destination')
            ready_instances[instance_id] = instance_info

    # Check if we have enough instances
    desired_count = config.count
    existing_count = len(ready_instances)

    if existing_count >= desired_count:
        # Already have enough instances
        instance_ids = list(ready_instances.keys())
        head_instance_id = instance_ids[0]
        logger.debug(f'Cluster {cluster_name_on_cloud} already has '
                     f'{existing_count} instances')
        return common.ProvisionRecord(
            provider_name=PROVIDER_NAME,
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,
            head_instance_id=head_instance_id,
            resumed_instance_ids=resumed_instance_ids,
            created_instance_ids=[],
        )

    if existing_count > 0 and existing_count < desired_count:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} has {existing_count} instances '
            f'but {desired_count} requested. Adding instances to existing '
            f'cluster is not supported.')

    # No instances exist - launch new ones
    to_start_count = desired_count
    logger.debug(f'Launching {to_start_count} new instances')

    instance_type = config.node_config.get('InstanceType')
    if not instance_type:
        raise RuntimeError('InstanceType is not set in node_config. '
                           'Please specify an instance type for Mithril.')

    # Get SSH public key
    public_key_path = config.authentication_config['ssh_public_key']
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read().strip()

    bid_id, created_instance_ids = utils.launch_instances(
        instance_type,
        cluster_name_on_cloud,
        region,
        public_key,
        instance_quantity=to_start_count,
    )
    logger.debug(
        f'Submitted bid {bid_id}, created {len(created_instance_ids)} instances'
    )

    head_instance_id = created_instance_ids[0]
    return common.ProvisionRecord(
        provider_name=PROVIDER_NAME,
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=head_instance_id,
        resumed_instance_ids=[],
        created_instance_ids=created_instance_ids,
    )


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    worker_only: bool = False,
) -> None:
    """Terminate all instances in the cluster by canceling their bid.

    Uses the cancel bid API (DELETE spot/bids/{bid_id}) which immediately
    terminates all instances associated with the bid.
    """
    del provider_config, worker_only  # unused
    logger.debug(
        f'Terminating all instances for cluster {cluster_name_on_cloud}')

    # Get the bid for this cluster
    bid = utils.get_bid(cluster_name_on_cloud)
    if not bid:
        logger.debug(f'No bid found for cluster {cluster_name_on_cloud}')
        return

    bid_id = bid['fid']
    utils.cancel_bid(bid_id)
    logger.debug(f'Canceled bid {bid_id} for cluster {cluster_name_on_cloud}')


def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    """Returns information about the cluster.

    Note: We include any instance with an IP address, not just RUNNING
    instances. This allows wait_for_ssh in provisioner.py to handle SSH
    readiness checking, enabling earlier access to instances that have IPs
    but may not be fully RUNNING.
    """
    del region  # unused
    # Get all non-terminated instances (not just RUNNING) - include any instance
    # with an IP address so that wait_for_ssh can check SSH readiness
    all_instances = _filter_instances(
        cluster_name_on_cloud,
        status_not_in=[
            'STATUS_TERMINATED',
            'STATUS_STOPPED',
            'STATUS_FAILED',
            'STATUS_ERROR',
            'STATUS_PAUSED',
        ],
    )
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    ssh_user = 'ubuntu'  # Default SSH user for Mithril

    for instance_id, instance_info in all_instances.items():
        # Only include instances with an SSH destination
        ssh_destination = instance_info['ssh_destination']
        if not ssh_destination:
            logger.debug(
                f'Skipping instance {instance_id} - no SSH destination yet')
            continue

        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=ssh_destination,
                external_ip=ssh_destination,
                ssh_port=22,
                tags={},
            )
        ]
        if head_instance_id is None:
            head_instance_id = instance_id

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
    """Returns the status of the specified instances for Mithril."""
    del cluster_name, provider_config, retry_if_missing  # unused
    instances = _filter_instances(cluster_name_on_cloud)
    if not instances:
        # No instances found: return empty dict to indicate fully deleted
        return {}

    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for instance_id, instance in instances.items():
        cluster_status = utils.to_cluster_status(instance['status'])
        if non_terminated_only and cluster_status is None:
            continue
        statuses[instance_id] = (cluster_status, None)
    return statuses


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """Wait for instances to reach the desired state."""
    del region  # unused
    if state == status_lib.ClusterStatus.UP:
        # Check if any instances are in RUNNING state
        instances = _filter_instances(cluster_name_on_cloud,
                                      status_in=['STATUS_RUNNING'])
        if not instances:
            # Check if any instances are in a failed state
            failed_instances = _filter_instances(
                cluster_name_on_cloud,
                status_in=['STATUS_FAILED', 'STATUS_ERROR'])
            if failed_instances:
                raise RuntimeError(
                    f'Cluster {cluster_name_on_cloud} has failed instances: '
                    f'{failed_instances}')
            raise RuntimeError('No running instances found for cluster '
                               f'{cluster_name_on_cloud}')
    elif state == status_lib.ClusterStatus.STOPPED:
        # Check if any instances are in TERMINATED state
        instances = _filter_instances(cluster_name_on_cloud,
                                      status_in=['STATUS_TERMINATED'])
        if not instances:
            # Check if any instances are in a failed state
            failed_instances = _filter_instances(
                cluster_name_on_cloud,
                status_in=['STATUS_FAILED', 'STATUS_ERROR'])
            if failed_instances:
                raise RuntimeError(
                    f'Cluster {cluster_name_on_cloud} has failed instances: '
                    f'{failed_instances}')
            raise RuntimeError('No terminated instances found for cluster '
                               f'{cluster_name_on_cloud}')
        # Check if any instances are in RUNNING state
        running_instances = _filter_instances(cluster_name_on_cloud,
                                              status_in=['STATUS_RUNNING'])
        if running_instances:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} is in STOPPED state, '
                f'but {len(running_instances)} instances are running.')
    else:
        raise RuntimeError(f'Unsupported state: {state}')


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Stop running instances by pausing the bid.

    This pauses the bid, which stops all instances associated with it.
    The instances can be resumed later by unpausing the bid.
    """
    del provider_config, worker_only  # unused
    logger.debug(f'Stopping instances for cluster {cluster_name_on_cloud}')

    bid = utils.get_bid(cluster_name_on_cloud)
    if not bid:
        logger.debug(f'No bid found for cluster {cluster_name_on_cloud}')
        return

    bid_id = bid['fid']
    utils.update_bid(bid_id, paused=True)
    logger.debug(f'Paused bid {bid_id} for cluster {cluster_name_on_cloud}')


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    ports: Optional[list] = None,
) -> None:
    """Cleanup ports. Not supported for Mithril."""
    raise NotImplementedError('cleanup_ports is not supported for Mithril')


def cleanup_custom_multi_network(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    failover: bool = False,
) -> None:
    """Cleanup custom multi-network. Not supported for Mithril."""
    raise NotImplementedError(
        'cleanup_custom_multi_network is not supported for Mithril')


def open_ports(
    cluster_name_on_cloud: str,
    ports: list,
    provider_config: Optional[dict] = None,
) -> None:
    """Open ports. Not supported for Mithril."""
    raise NotImplementedError('open_ports is not supported for Mithril')
