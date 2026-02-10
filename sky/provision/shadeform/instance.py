"""Shadeform instance provisioning."""
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from sky import sky_logging
from sky.provision import common
from sky.provision.shadeform import shadeform_utils
from sky.utils import status_lib

POLL_INTERVAL = 10
INSTANCE_READY_TIMEOUT = 3600

logger = sky_logging.init_logger(__name__)

# Status mapping from Shadeform to SkyPilot
SHADEFORM_STATUS_MAP = {
    'creating': status_lib.ClusterStatus.INIT,
    'pending_provider': status_lib.ClusterStatus.INIT,
    'pending': status_lib.ClusterStatus.INIT,
    'active': status_lib.ClusterStatus.UP,
    'deleted': status_lib.ClusterStatus.STOPPED,
}


def _get_cluster_instances(cluster_name_on_cloud: str) -> Dict[str, Any]:
    """Get all instances belonging to a cluster."""
    try:
        response = shadeform_utils.get_instances()
        instances = response.get('instances', [])

        cluster_instances = {}
        possible_names = [
            f'{cluster_name_on_cloud}-head', f'{cluster_name_on_cloud}-worker'
        ]

        for instance in instances:
            if instance.get('name') in possible_names:
                cluster_instances[instance['id']] = instance

        return cluster_instances
    except (ValueError, KeyError, requests.exceptions.RequestException) as e:
        logger.warning(f'Failed to get instances: {e}')
        return {}


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    """Get the head instance ID from a list of instances."""
    for instance_id, instance in instances.items():
        if instance.get('name', '').endswith('-head'):
            return instance_id
    return None


def _wait_for_instances_ready(cluster_name_on_cloud: str,
                              expected_count: int,
                              timeout: int = INSTANCE_READY_TIMEOUT) -> bool:
    """Wait for instances to be ready (active state with SSH access)."""
    start_time = time.time()

    while time.time() - start_time < timeout:
        instances = _get_cluster_instances(cluster_name_on_cloud)
        ready_count = 0

        for instance in instances.values():
            if (instance.get('status') == 'active' and
                    instance.get('ip') is not None and
                    instance.get('ssh_port') is not None):
                ready_count += 1

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
    logger.debug(f'DEBUG: region type={type(region)}, value={region!r}')
    logger.debug(f'DEBUG: config node_config={config.node_config}')

    # Check existing instances
    existing_instances = _get_cluster_instances(cluster_name_on_cloud)
    head_instance_id = _get_head_instance_id(existing_instances)

    # Filter active instances
    active_instances = {
        iid: inst
        for iid, inst in existing_instances.items()
        if inst.get('status') == 'active'
    }

    current_count = len(active_instances)
    target_count = config.count

    logger.info(f'Current instances: {current_count}, target: {target_count}')

    if current_count >= target_count:
        if head_instance_id is None:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no head node')
        logger.info(f'Cluster already has {current_count} instances, '
                    f'no need to start more')
        return common.ProvisionRecord(
            provider_name='shadeform',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,  # Shadeform doesn't use separate zones
            head_instance_id=head_instance_id,
            resumed_instance_ids=[],
            created_instance_ids=[])

    # Create new instances
    to_create = target_count - current_count
    created_instance_ids = []

    for _ in range(to_create):
        node_type = 'head' if head_instance_id is None else 'worker'
        instance_name = f'{cluster_name_on_cloud}-{node_type}'

        # Extract configuration from node_config

        # The node_config contains instance specs including InstanceType
        # which follows the format: {cloud_provider}_{instance_type}
        # (e.g., "massedcompute_A6000_basex2")
        node_config = config.node_config
        assert 'InstanceType' in node_config, \
            'InstanceType must be present in node_config'

        # Parse the instance type to extract cloud provider and instance specs
        # Expected format: "{cloud}_{instance_type}" where cloud is provider
        # (massedcompute, scaleway, lambda, etc.)
        instance_type_full = node_config['InstanceType']
        assert (isinstance(instance_type_full, str) and
                '_' in instance_type_full), \
            f'InstanceType must be in format cloud_instance_type, got: ' \
            f'{instance_type_full}'

        instance_type_split = instance_type_full.split('_')
        assert len(instance_type_split) >= 2, \
            f'InstanceType must contain at least one underscore, got: ' \
            f'{instance_type_full}'

        # Extract cloud provider (first part) and instance type (remaining)
        # Example: "massedcompute_A6000-basex2" -> cloud="massedcompute",
        # instance_type="A6000-basex2"
        cloud = instance_type_split[0]
        instance_type = '_'.join(instance_type_split[1:])

        # Shadeform uses underscores instead of hyphens
        instance_type = instance_type.replace('-', '_')

        if instance_type.endswith('B'):
            instance_type = instance_type[:-1]

        # Replace "GBx" with "Gx" (case sensitive)
        if 'GBx' in instance_type:
            instance_type = instance_type.replace('GBx', 'Gx')

        assert cloud, 'Cloud provider cannot be empty'
        assert instance_type, 'Instance type cannot be empty'

        # Get SSH key ID for authentication - this is optional and may be None
        ssh_key_id = config.authentication_config.get('ssh_key_id')

        create_config = {
            'cloud': cloud,
            'region': region,
            'shade_instance_type': instance_type,
            'name': instance_name,
            'ssh_key_id': ssh_key_id
        }

        try:
            logger.info(f'Creating {node_type} instance: {instance_name}')
            response = shadeform_utils.create_instance(create_config)
            instance_id = response['id']
            created_instance_ids.append(instance_id)

            if head_instance_id is None:
                head_instance_id = instance_id

            logger.info(f'Created instance {instance_id} ({node_type})')

        except Exception as e:
            logger.error(f'Failed to create instance: {e}')
            # Clean up any created instances
            for iid in created_instance_ids:
                try:
                    shadeform_utils.delete_instance(iid)
                except requests.exceptions.RequestException as cleanup_e:
                    logger.warning(
                        f'Failed to cleanup instance {iid}: {cleanup_e}')
            raise

    # Wait for all instances to be ready
    logger.info('Waiting for instances to become ready...')
    if not _wait_for_instances_ready(cluster_name_on_cloud, target_count):
        raise RuntimeError('Timed out waiting for instances to be ready')

    assert head_instance_id is not None, 'head_instance_id should not be None'

    return common.ProvisionRecord(provider_name='shadeform',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=region,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """Wait for instances to reach the specified state."""
    del region, cluster_name_on_cloud, state  # unused
    # For Shadeform, instances are ready when they reach 'active' status
    # This is already handled in run_instances


def stop_instances(cluster_name_on_cloud: str,
                   provider_config: Optional[Dict[str, Any]] = None,
                   worker_only: bool = False) -> None:
    """Stop instances (not supported by Shadeform)."""
    del cluster_name_on_cloud, provider_config, worker_only  # unused
    raise NotImplementedError(
        'Stopping instances is not supported by Shadeform')


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

    for instance_id, instance in instances_to_delete.items():
        try:
            logger.info(
                f'Terminating instance {instance_id} ({instance.get("name")})')
            shadeform_utils.delete_instance(instance_id)
        except requests.exceptions.RequestException as e:
            logger.warning(f'Failed to terminate instance {instance_id}: {e}')


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
                                  provider_name='shadeform')

    head_instance_id = _get_head_instance_id(instances)

    # Convert instance format for ClusterInfo
    cluster_instances = {}
    for instance_id, instance in instances.items():
        instance_info = common.InstanceInfo(
            instance_id=instance_id,
            internal_ip=instance.get('ip', ''),
            external_ip=instance.get('ip', ''),
            ssh_port=instance.get('ssh_port', 22),
            tags={},
        )
        # ClusterInfo expects Dict[InstanceId, List[InstanceInfo]]
        cluster_instances[instance_id] = [instance_info]

    ssh_user = 'shadeform'  # default
    if head_instance_id is not None:
        ssh_user = instances.get(head_instance_id,
                                 {}).get('ssh_user', 'shadeform')

    return common.ClusterInfo(instances=cluster_instances,
                              head_instance_id=head_instance_id,
                              provider_name='shadeform',
                              ssh_user=ssh_user)


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """Query the status of instances."""
    del cluster_name, provider_config  # unused
    instances = _get_cluster_instances(cluster_name_on_cloud)

    if not instances:
        return {}

    status_map: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                                Optional[str]]] = {}
    for instance_id, instance in instances.items():
        shadeform_status = instance.get('status', 'unknown')
        sky_status = SHADEFORM_STATUS_MAP.get(shadeform_status,
                                              status_lib.ClusterStatus.INIT)

        if (non_terminated_only and
                sky_status == status_lib.ClusterStatus.STOPPED):
            continue

        status_map[instance_id] = (sky_status, None)

    return status_map


def open_ports(cluster_name_on_cloud: str,
               ports: List[str],
               provider_config: Optional[Dict[str, Any]] = None) -> None:
    """Open ports (not supported by Shadeform)."""
    del cluster_name_on_cloud, ports, provider_config  # unused
    raise NotImplementedError()


def cleanup_ports(cluster_name_on_cloud: str,
                  ports: List[str],
                  provider_config: Optional[Dict[str, Any]] = None) -> None:
    """Cleanup ports (not supported by Shadeform)."""
    del cluster_name_on_cloud, ports, provider_config  # unused
    # Nothing to cleanup since we don't support dynamic port opening
