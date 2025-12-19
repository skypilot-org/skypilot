"""PPIO instance provisioning."""
import ast
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from sky import sky_logging
from sky.catalog import ppio_catalog
from sky.provision import common
from sky.provision.ppio import ppio_utils
from sky.utils import status_lib

POLL_INTERVAL = 10
INSTANCE_READY_TIMEOUT = 3600

logger = sky_logging.init_logger(__name__)

# Status mapping from PPIO to SkyPilot
PPIO_STATUS_MAP = {
    'creating': status_lib.ClusterStatus.INIT,
    'pending_provider': status_lib.ClusterStatus.INIT,
    'pending': status_lib.ClusterStatus.INIT,
    'active': status_lib.ClusterStatus.UP,
    'deleted': status_lib.ClusterStatus.STOPPED,
}


def _get_cluster_instances(cluster_name_on_cloud: str) -> Dict[str, Any]:
    """Get all instances belonging to a cluster."""
    try:
        response = ppio_utils.get_instances()
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
    last_log_time = 0
    log_interval = 30  # Log every 30 seconds

    while time.time() - start_time < timeout:
        instances = _get_cluster_instances(cluster_name_on_cloud)
        ready_count = 0
        status_summary: Dict[str, int] = {}  # all statuses and their counts

        for instance in instances.values():
            status = instance.get('status', 'unknown')
            status_summary[status] = status_summary.get(status, 0) + 1
            if (instance.get('status') == 'active' and
                    instance.get('ip') is not None and
                    instance.get('ssh_port') is not None):
                ready_count += 1

        elapsed = int(time.time() - start_time)
        # Log progress every 30 seconds or when status changes
        if (elapsed - last_log_time >= log_interval or
                ready_count >= expected_count):
            logger.info(f'Waiting for instances to be ready: '
                        f'({ready_count}/{expected_count}) after {elapsed}s. '
                        f'Status: {status_summary}')
            last_log_time = elapsed

        if ready_count >= expected_count:
            logger.info(f'All {expected_count} instances are ready!')
            return True

        time.sleep(POLL_INTERVAL)

    logger.error(
        f'Timeout waiting for instances after {timeout}s. '
        f'Ready: {ready_count}/{expected_count}, Status: {status_summary}')
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
            provider_name='ppio',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,  # PPIO doesn't use separate zones
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

        # PPIO uses underscores instead of hyphens
        instance_type = instance_type.replace('-', '_')
        # remove the 'B' suffix if it exists by default
        if instance_type.endswith('B'):
            instance_type = instance_type[:-1]

        # Replace "GBx" with "Gx" (case sensitive)
        if 'GBx' in instance_type:
            instance_type = instance_type.replace('GBx', 'Gx')

        assert cloud, 'Cloud provider cannot be empty'
        assert instance_type, 'Instance type cannot be empty'

        # PPIO doesn't require SSH key ID - instances use default SSH access
        # The SSH key is only used locally for connecting to instances
        # Region name may include parentheses like "AF-ZA-01 (South Africa)"
        # Extract just the region code (part before parentheses) for API
        # The PPIO API expects just the region code, not the full name
        region_code = region.split(' (')[0] if ' (' in region else region

        # Get catalog data and filter by instance type
        # Note: instance_type_full may include cloud provider prefix (e.g., "massedcompute_1x_RTX 4090 24GB")
        # but CSV InstanceType is just "1x_RTX 4090 24GB", so we need to match correctly
        df = ppio_catalog._get_df()

        # Try to match instance_type_full first, then try without cloud prefix
        df_filtered = df[df['InstanceType'] == instance_type_full]
        if df_filtered.empty and '_' in instance_type_full:
            # If not found, try matching without the cloud provider prefix
            # instance_type_full format: "cloud_1x_RTX 4090 24GB" -> "1x_RTX 4090 24GB"
            instance_type_without_cloud = '_'.join(
                instance_type_full.split('_')[1:])
            df_filtered = df[df['InstanceType'] == instance_type_without_cloud]

        # Also filter by region to get the correct row for this region
        if not df_filtered.empty:
            df_filtered = df_filtered[df_filtered['Region'] == region]

        if df_filtered.empty:
            raise ValueError(
                f'Instance type {instance_type_full} not found in region {region}. '
                f'Available regions for this instance type: '
                f'{df[df["InstanceType"] == instance_type_full]["Region"].unique().tolist() if not df[df["InstanceType"] == instance_type_full].empty else "N/A"}'
            )

        df = df_filtered
        rowData = df.iloc[0]
        gpuInfo_str = rowData['GpuInfo']
        gpuInfo = ast.literal_eval(gpuInfo_str)

        # the params are the same as the create_instance API: https://ppio.com/docs/gpus/instance/reference-create-gpu-instance
        # Extract productId from GpuInfo - this is the instance ID from PPIO API
        productId = gpuInfo['Gpus'][0]['Id']
        gpuNum = 1
        # Get imageUrl from node_config, default to 'nginx:latest' if not specified
        imageUrl = node_config.get('ImageUrl') or node_config.get(
            'imageUrl') or 'nginx:latest'
        imageAuth = node_config.get('ImageAuth') or node_config.get(
            'imageAuth') or ''
        imageAuthId = node_config.get('ImageAuthId') or node_config.get(
            'imageAuthId') or ''
        ports = node_config.get('Ports') or node_config.get(
            'ports'
        ) or ''  # e.g.: 80/http,3306/tcp. Supported port range: [1-65535], except for 2222, 2223, 2224 which are reserved for internal use
        envs = node_config.get('Envs') or node_config.get('envs') or [
        ]  # Instance environment variables. Up to 100 environment variables can be created. e.g.: {'ENV1': 'value1', 'ENV2': 'value2'}
        tools = node_config.get('Tools') or node_config.get('tools') or [
        ]  # some official images only include Jupyter. The total number of ports used by ports + tools must not exceed 15. e.g.: 【{'name': 'Jupyter', 'port': 8080, type: 'http'}]
        command = node_config.get('Command') or node_config.get(
            'command'
        ) or ''  # Instance startup command. String, length limit: 0-2047 characters.
        # clusterId = ""
        networkStorages = node_config.get('NetworkStorages') or node_config.get(
            'networkStorages'
        ) or [
        ]  # Cloud storage mount configuration. Up to 30 cloud storages can be mounted. e.g.: [{'Id': '1234567890', 'mountPath': '/network'}]
        networkId = node_config.get('NetworkId') or node_config.get(
            'networkId'
        ) or ''  # VPC network ID. Leave empty if not using a VPC network.
        kind = 'gpu'
        min_rootfs = gpuInfo['Gpus'][0].get(
            'MinRootFS') or gpuInfo['Gpus'][0].get('minRootFS', 10)
        rootfsSize = node_config.get('RootfsSize') or node_config.get(
            'rootfsSize') or int(min_rootfs)

        create_config = {
            'productId': productId,
            # 'region': region_code,  # Use region code without parentheses
            'name': instance_name,
            'gpuNum': gpuNum,
            'rootfsSize': rootfsSize,
            'imageUrl': imageUrl,
            'imageAuth': imageAuth,
            'imageAuthId': imageAuthId,
            'ports': ports,
            'envs': envs,
            'tools': tools,
            'command': command,
            # 'clusterId': clusterId,
            'networkStorages': networkStorages,
            'networkId': networkId,
            'kind': kind,
        }

        logger.info(f'create_config: {create_config}')

        # Only add optional fields if they have meaningful values
        # Empty strings and empty lists should be omitted
        # imageUrl = 'nginx:latest'  # Default image, may not be needed
        # kind = 'gpu'  # May be inferred from productId
        try:
            logger.info(f'Creating {node_type} instance: {instance_name}')
            response = ppio_utils.create_instance(create_config)
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
                    ppio_utils.delete_instance(iid)
                except requests.exceptions.RequestException as cleanup_e:
                    logger.warning(
                        f'Failed to cleanup instance {iid}: {cleanup_e}')
            raise

    # Wait for all instances to be ready
    logger.info('Waiting for instances to become ready...')
    if not _wait_for_instances_ready(cluster_name_on_cloud, target_count):
        raise RuntimeError('Timed out waiting for instances to be ready')

    assert head_instance_id is not None, 'head_instance_id should not be None'

    return common.ProvisionRecord(provider_name='ppio',
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
    # For PPIO, instances are ready when they reach 'active' status
    # This is already handled in run_instances


def stop_instances(cluster_name_on_cloud: str,
                   provider_config: Optional[Dict[str, Any]] = None,
                   worker_only: bool = False) -> None:
    """Stop instances (not supported by PPIO)."""
    del cluster_name_on_cloud, provider_config, worker_only  # unused
    raise NotImplementedError('Stopping instances is not supported by PPIO')


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
            ppio_utils.delete_instance(instance_id)
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
                                  provider_name='ppio')

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

    ssh_user = 'ppio'  # default
    if head_instance_id is not None:
        ssh_user = instances.get(head_instance_id, {}).get('ssh_user', 'ppio')

    return common.ClusterInfo(instances=cluster_instances,
                              head_instance_id=head_instance_id,
                              provider_name='ppio',
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
        ppio_status = instance.get('status', 'unknown')
        sky_status = PPIO_STATUS_MAP.get(ppio_status,
                                           status_lib.ClusterStatus.INIT)

        if (non_terminated_only and
                sky_status == status_lib.ClusterStatus.STOPPED):
            continue

        status_map[instance_id] = (sky_status, None)

    return status_map


def open_ports(cluster_name_on_cloud: str,
               ports: List[str],
               provider_config: Optional[Dict[str, Any]] = None) -> None:
    """Open ports (not supported by PPIO)."""
    del cluster_name_on_cloud, ports, provider_config  # unused
    raise NotImplementedError()


def cleanup_ports(cluster_name_on_cloud: str,
                  ports: List[str],
                  provider_config: Optional[Dict[str, Any]] = None) -> None:
    """Cleanup ports (not supported by PPIO)."""
    del cluster_name_on_cloud, ports, provider_config  # unused
    # Nothing to cleanup since we don't support dynamic port opening