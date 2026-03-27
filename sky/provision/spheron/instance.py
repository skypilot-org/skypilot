"""Spheron instance provisioning."""
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.spheron import spheron_utils
from sky.utils import status_lib

POLL_INTERVAL = 10
INSTANCE_READY_TIMEOUT = 3600

logger = sky_logging.init_logger(__name__)

# Status mapping from Spheron to SkyPilot.
# Spheron statuses: deploying, running, failed, terminated, terminated-provider
SPHERON_STATUS_MAP = {
    'deploying': status_lib.ClusterStatus.INIT,
    'running': status_lib.ClusterStatus.UP,
    'failed': status_lib.ClusterStatus.INIT,
    'terminated': status_lib.ClusterStatus.STOPPED,
    'terminated-provider': status_lib.ClusterStatus.STOPPED,
}


def _get_cluster_instances(cluster_name_on_cloud: str) -> Dict[str, Any]:
    """Get all instances belonging to a cluster."""
    try:
        deployments = spheron_utils.get_deployments()
        possible_names = {
            f'{cluster_name_on_cloud}-head',
            f'{cluster_name_on_cloud}-worker',
        }
        return {
            d['id']: d for d in deployments if d.get('name') in possible_names
        }
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get deployments: {e}')
        return {}


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    """Get the head instance ID from a dict of instances."""
    for instance_id, instance in instances.items():
        if instance.get('name', '').endswith('-head'):
            return instance_id
    return None


def _wait_for_instances_ready(cluster_name_on_cloud: str,
                              expected_count: int,
                              timeout: int = INSTANCE_READY_TIMEOUT) -> bool:
    """Wait for instances to be ready (running state with SSH access)."""
    start_time = time.time()

    while time.time() - start_time < timeout:
        instances = _get_cluster_instances(cluster_name_on_cloud)
        ready_count = 0
        failed_count = 0

        for instance in instances.values():
            spheron_status = instance.get('status', '')
            # Spheron uses camelCase for response fields
            if (spheron_status == 'running' and
                    instance.get('ipAddress') is not None and
                    instance.get('sshPort') is not None):
                ready_count += 1
            elif spheron_status == 'terminated-provider':
                logger.warning(
                    f'Instance {instance.get("name", instance.get("id"))} was '
                    f'terminated by the provider before it became ready.')
                failed_count += 1
            elif spheron_status == 'failed':
                failed_count += 1

        if failed_count > 0:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud}: {failed_count} instance(s) '
                f'failed to start or were terminated by the provider.')

        logger.info(f'Waiting for instances to be ready: '
                    f'({ready_count}/{expected_count})')

        if ready_count >= expected_count:
            return True

        time.sleep(POLL_INTERVAL)

    return False


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Run instances for the given cluster."""
    del cluster_name  # unused - we use cluster_name_on_cloud
    logger.info(f'Running instances for cluster {cluster_name_on_cloud} '
                f'in region {region}')

    # Check existing instances
    existing_instances = _get_cluster_instances(cluster_name_on_cloud)

    # Filter active (running) instances only — head detection must exclude
    # failed/terminated nodes to avoid creating a worker when the head is down.
    active_instances = {
        iid: inst
        for iid, inst in existing_instances.items()
        if inst.get('status') == 'running'
    }
    head_instance_id = _get_head_instance_id(active_instances)

    current_count = len(active_instances)
    target_count = config.count

    logger.info(f'Current instances: {current_count}, target: {target_count}')

    if current_count >= target_count:
        if head_instance_id is None:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no head node')
        logger.info(f'Cluster already has {current_count} instances, '
                    f'no need to start more')
        return common.ProvisionRecord(provider_name='spheron',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    # Create new instances
    to_create = target_count - current_count
    created_instance_ids = []

    node_config = config.node_config
    assert 'InstanceType' in node_config, \
        'InstanceType must be present in node_config'

    # For Spheron, InstanceType is the offerId directly
    offer_id = node_config['InstanceType']

    # Extra deployment params encoded in node_config by the Ray template
    spheron_provider = node_config.get('SpheronProvider', '')
    gpu_type = node_config.get('GpuType', '')
    gpu_count = int(node_config.get('GpuCount', 1))
    operating_system = node_config.get('OperatingSystem', 'ubuntu-22.04')
    spheron_instance_type = node_config.get('SpheronInstanceType', 'DEDICATED')

    # Get SSH key ID and team ID once (both are static for this launch)
    ssh_key_id = config.authentication_config.get('ssh_key_id')
    team_id = spheron_utils.get_team_id()

    for _ in range(to_create):
        node_type = 'head' if head_instance_id is None else 'worker'
        instance_name = f'{cluster_name_on_cloud}-{node_type}'

        create_config: Dict[str, Any] = {
            'provider': spheron_provider,
            'offerId': offer_id,
            'gpuType': gpu_type,
            'gpuCount': gpu_count,
            'region': region,
            'operatingSystem': operating_system,
            'instanceType': spheron_instance_type,
            'name': instance_name,
            'teamId': team_id,
        }
        if ssh_key_id is not None:
            create_config['sshKeyId'] = ssh_key_id

        try:
            logger.info(f'Creating {node_type} instance: {instance_name}')
            response = spheron_utils.create_deployment(create_config)
            instance_id = response['id']
            created_instance_ids.append(instance_id)

            if head_instance_id is None:
                head_instance_id = instance_id

            logger.info(f'Created instance {instance_id} ({node_type})')

        except Exception as e:
            logger.error(f'Failed to create instance: {e}')
            # Clean up any created instances on failure.
            # Use broad Exception since SpheronMinimumRuntimeError (RuntimeError)
            # and HTTP errors can both occur during cleanup.
            for iid in created_instance_ids:
                try:
                    spheron_utils.delete_deployment(iid)
                except Exception as cleanup_e:  # pylint: disable=broad-except
                    logger.warning(
                        f'Failed to cleanup instance {iid}: {cleanup_e}')
            raise

    # Wait for all instances to be ready
    logger.info('Waiting for instances to become ready...')
    if not _wait_for_instances_ready(cluster_name_on_cloud, target_count):
        raise RuntimeError('Timed out waiting for instances to be ready')

    assert head_instance_id is not None, 'head_instance_id should not be None'

    return common.ProvisionRecord(provider_name='spheron',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """Wait for instances to reach the specified state."""
    del region, cluster_name_on_cloud, state  # unused
    # For Spheron, instances are ready when they reach 'running' status.
    # This is already handled in run_instances.


def stop_instances(cluster_name_on_cloud: str,
                   provider_config: Optional[Dict[str, Any]] = None,
                   worker_only: bool = False) -> None:
    """Stop instances (not supported by Spheron)."""
    del cluster_name_on_cloud, provider_config, worker_only  # unused
    raise NotImplementedError('Stopping instances is not supported by Spheron')


def terminate_instances(cluster_name_on_cloud: str,
                        provider_config: Optional[Dict[str, Any]] = None,
                        worker_only: bool = False) -> None:
    """Terminate instances."""
    del provider_config  # unused
    logger.info(f'Terminating instances for cluster {cluster_name_on_cloud}')

    instances = _get_cluster_instances(cluster_name_on_cloud)

    if not instances:
        logger.info(f'No instances found for cluster {cluster_name_on_cloud}')
        return

    instances_to_delete = instances
    if worker_only:
        # Only delete worker nodes, not head
        instances_to_delete = {
            iid: inst
            for iid, inst in instances.items()
            if not inst.get('name', '').endswith('-head')
        }

    max_retries = 30  # Up to 30 minutes for minimum-runtime constraint
    for instance_id, instance in instances_to_delete.items():
        instance_name = instance.get('name', instance_id)
        logger.info(f'Terminating instance {instance_id} ({instance_name})')
        for attempt in range(max_retries):
            try:
                spheron_utils.delete_deployment(instance_id)
                logger.info(f'Terminated instance {instance_id}')
                break
            except spheron_utils.SpheronMinimumRuntimeError as e:
                # Spheron enforces a minimum runtime; wait and retry.
                # The error message includes the time remaining.
                if attempt + 1 >= max_retries:
                    logger.warning(
                        f'Giving up on terminating {instance_id} after '
                        f'{max_retries} attempts: {e}')
                    break
                logger.info(
                    str(e) +
                    f' Waiting 60s before retrying ({attempt + 1}/{max_retries})...'
                )
                time.sleep(60)
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    f'Failed to terminate instance {instance_id}: {e}')
                break


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """Get cluster information."""
    del region, provider_config  # unused
    instances = _get_cluster_instances(cluster_name_on_cloud)

    if not instances:
        return common.ClusterInfo(instances={},
                                  head_instance_id=None,
                                  provider_name='spheron')

    # Only consider running instances for head detection — terminated deployments
    # with a matching name (from prior runs) must not be selected as head.
    running_instances = {
        iid: inst
        for iid, inst in instances.items()
        if inst.get('status') == 'running'
    }
    head_instance_id = _get_head_instance_id(running_instances)

    # Convert instance format for ClusterInfo.
    # Spheron uses camelCase: ipAddress, sshPort, user
    cluster_instances = {}
    for instance_id, instance in running_instances.items():
        ip = instance.get('ipAddress', '')
        ssh_port = instance.get('sshPort', 22)
        instance_info = common.InstanceInfo(
            instance_id=instance_id,
            internal_ip=ip,
            external_ip=ip,
            ssh_port=ssh_port,
            tags={},
            node_name=instance_id,
        )
        cluster_instances[instance_id] = [instance_info]

    # Use the SSH user from the deployment response, default to 'ubuntu'
    ssh_user = 'ubuntu'
    if head_instance_id is not None:
        ssh_user = running_instances.get(head_instance_id,
                                         {}).get('user', 'ubuntu')

    return common.ClusterInfo(instances=cluster_instances,
                              head_instance_id=head_instance_id,
                              provider_name='spheron',
                              ssh_user=ssh_user,
                              custom_ray_options={'use_external_ip': True})


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """Query the status of instances."""
    del cluster_name, provider_config, retry_if_missing  # unused
    instances = _get_cluster_instances(cluster_name_on_cloud)

    if not instances:
        return {}

    status_map: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                                Optional[str]]] = {}
    for instance_id, instance in instances.items():
        spheron_status = instance.get('status', 'unknown')
        sky_status = SPHERON_STATUS_MAP.get(spheron_status,
                                            status_lib.ClusterStatus.INIT)

        if spheron_status == 'terminated-provider':
            instance_name = instance.get('name', instance_id)
            logger.warning(
                f'Instance {instance_name} ({instance_id}) was terminated by '
                f'the provider. This may have been due to hardware failure, '
                f'resource reclamation, or a spot instance preemption. '
                f'The cluster will need to be relaunched.')

        if non_terminated_only and sky_status == status_lib.ClusterStatus.STOPPED:
            continue

        status_map[instance_id] = (sky_status, None)

    return status_map


def open_ports(cluster_name_on_cloud: str,
               ports: List[str],
               provider_config: Optional[Dict[str, Any]] = None) -> None:
    """Open ports for Spheron (ports are opened at launch time via template)."""
    del cluster_name_on_cloud, ports, provider_config  # unused


def cleanup_ports(cluster_name_on_cloud: str,
                  ports: List[str],
                  provider_config: Optional[Dict[str, Any]] = None) -> None:
    """Cleanup ports (not supported by Spheron)."""
    del cluster_name_on_cloud, ports, provider_config  # unused
    # Nothing to cleanup since we don't support dynamic port opening
