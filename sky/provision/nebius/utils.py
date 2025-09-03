"""Nebius library wrapper for SkyPilot."""
import time
from typing import Any, Dict, List, Optional
import uuid

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import nebius
from sky.provision.nebius import constants as nebius_constants
from sky.utils import common_utils
from sky.utils import resources_utils

logger = sky_logging.init_logger(__name__)

POLL_INTERVAL = 5

_MAX_OPERATIONS_TO_FETCH = 1000


def retry(func):
    """Decorator to retry a function."""

    def wrapper(*args, **kwargs):
        """Wrapper for retrying a function."""
        cnt = 0
        while True:
            try:
                return func(*args, **kwargs)
            except nebius.nebius.error.QueryError as e:
                if cnt >= 3:
                    raise
                logger.warning('Retrying for exception: '
                               f'{common_utils.format_exception(e)}.')
                time.sleep(POLL_INTERVAL)

    return wrapper


def get_project_by_region(region: str) -> str:
    service = nebius.iam().ProjectServiceClient(nebius.sdk())
    projects = nebius.sync_call(
        service.list(
            nebius.iam().ListProjectsRequest(parent_id=nebius.get_tenant_id()),
            timeout=nebius.READ_TIMEOUT))

    #  Check is there project if in config
    project_id = skypilot_config.get_effective_region_config(
        cloud='nebius', region=region, keys=('project_id',), default_value=None)
    if project_id is not None:
        return project_id
    for project in projects.items:
        if project.status.region == region:
            return project.metadata.id
    raise Exception(f'No project found for region "{region}".')


def get_or_create_gpu_cluster(name: str, project_id: str, fabric: str) -> str:
    """Creates a GPU cluster.
    https://docs.nebius.com/compute/clusters/gpu
    """
    service = nebius.compute().GpuClusterServiceClient(nebius.sdk())
    try:
        cluster = nebius.sync_call(
            service.get_by_name(nebius.nebius_common().GetByNameRequest(
                parent_id=project_id,
                name=name,
            )))
        cluster_id = cluster.metadata.id
    except nebius.request_error():
        cluster = nebius.sync_call(
            service.create(nebius.compute().CreateGpuClusterRequest(
                metadata=nebius.nebius_common().ResourceMetadata(
                    parent_id=project_id,
                    name=name,
                ),
                spec=nebius.compute().GpuClusterSpec(
                    infiniband_fabric=fabric))))
        cluster_id = cluster.resource_id
    return cluster_id


def delete_cluster(name: str, region: str) -> None:
    """Delete a GPU cluster."""
    project_id = get_project_by_region(region)
    service = nebius.compute().GpuClusterServiceClient(nebius.sdk())
    try:
        cluster = nebius.sync_call(
            service.get_by_name(nebius.nebius_common().GetByNameRequest(
                parent_id=project_id,
                name=name,
            )))
        cluster_id = cluster.metadata.id
        logger.debug(f'Found GPU Cluster : {cluster_id}.')
        nebius.sync_call(
            service.delete(
                nebius.compute().DeleteGpuClusterRequest(id=cluster_id)))
        logger.debug(f'Deleted GPU Cluster : {cluster_id}.')
    except nebius.request_error():
        logger.debug('GPU Cluster does not exist.')


def list_instances(project_id: str) -> Dict[str, Dict[str, Any]]:
    """Lists instances associated with API key."""
    service = nebius.compute().InstanceServiceClient(nebius.sdk())
    page_token = ''
    instances = []
    while True:
        result = nebius.sync_call(
            service.list(nebius.compute().ListInstancesRequest(
                parent_id=project_id,
                page_size=100,
                page_token=page_token,
            ),
                         timeout=nebius.READ_TIMEOUT))
        instances.extend(result.items)
        if not result.next_page_token:  # "" means no more pages
            break
        page_token = result.next_page_token

    instance_dict: Dict[str, Dict[str, Any]] = {}
    for instance in instances:
        info = {}
        info['status'] = instance.status.state.name
        info['name'] = instance.metadata.name
        if instance.status.network_interfaces:
            info['external_ip'] = instance.status.network_interfaces[
                0].public_ip_address.address.split('/')[0]
            info['internal_ip'] = instance.status.network_interfaces[
                0].ip_address.address.split('/')[0]
        instance_dict[instance.metadata.id] = info

    return instance_dict


def stop(instance_id: str) -> None:
    service = nebius.compute().InstanceServiceClient(nebius.sdk())
    nebius.sync_call(
        service.stop(nebius.compute().StopInstanceRequest(id=instance_id)))
    retry_count = 0
    while retry_count < nebius.MAX_RETRIES_TO_INSTANCE_STOP:
        service = nebius.compute().InstanceServiceClient(nebius.sdk())
        instance = nebius.sync_call(
            service.get(nebius.compute().GetInstanceRequest(id=instance_id,)))
        if instance.status.state.name == 'STOPPED':
            break
        time.sleep(POLL_INTERVAL)
        logger.debug(f'Waiting for instance {instance_id} stopping.')
        retry_count += 1

    if retry_count == nebius.MAX_RETRIES_TO_INSTANCE_STOP:
        raise TimeoutError(
            f'Exceeded maximum retries '
            f'({nebius.MAX_RETRIES_TO_INSTANCE_STOP * POLL_INTERVAL}'
            f' seconds) while waiting for instance {instance_id}'
            f' to be stopped.')


def start(instance_id: str) -> None:
    service = nebius.compute().InstanceServiceClient(nebius.sdk())
    nebius.sync_call(
        service.start(nebius.compute().StartInstanceRequest(id=instance_id)))
    retry_count = 0
    while retry_count < nebius.MAX_RETRIES_TO_INSTANCE_START:
        service = nebius.compute().InstanceServiceClient(nebius.sdk())
        instance = nebius.sync_call(
            service.get(nebius.compute().GetInstanceRequest(id=instance_id,)))
        if instance.status.state.name == 'RUNNING':
            break
        time.sleep(POLL_INTERVAL)
        logger.debug(f'Waiting for instance {instance_id} starting.')
        retry_count += 1

    if retry_count == nebius.MAX_RETRIES_TO_INSTANCE_START:
        raise TimeoutError(
            f'Exceeded maximum retries '
            f'({nebius.MAX_RETRIES_TO_INSTANCE_START * POLL_INTERVAL}'
            f' seconds) while waiting for instance {instance_id}'
            f' to be ready.')


def launch(cluster_name_on_cloud: str,
           node_type: str,
           platform: str,
           preset: str,
           region: str,
           image_family: str,
           disk_size: int,
           user_data: str,
           associate_public_ip_address: bool,
           filesystems: List[Dict[str, Any]],
           use_spot: bool = False,
           network_tier: Optional[resources_utils.NetworkTier] = None) -> str:
    # Each node must have a unique name to avoid conflicts between
    # multiple worker VMs. To ensure uniqueness,a UUID is appended
    # to the node name.
    instance_name = (f'{cluster_name_on_cloud}-'
                     f'{uuid.uuid4().hex[:4]}-{node_type}')
    logger.debug(f'Launching instance: {instance_name}')

    disk_name = 'disk-' + instance_name
    cluster_id = None
    project_id = get_project_by_region(region)
    # 8 GPU virtual machines can be grouped into a GPU cluster.
    # The GPU clusters are built with InfiniBand secure high-speed networking.
    # https://docs.nebius.com/compute/clusters/gpu
    if platform in nebius_constants.INFINIBAND_INSTANCE_PLATFORMS:
        if preset == '8gpu-128vcpu-1600gb':
            fabric = skypilot_config.get_effective_region_config(
                cloud='nebius',
                region=region,
                keys=('fabric',),
                default_value=None)

            # Auto-select fabric if network_tier=best and no fabric configured
            if (fabric is None and
                    str(network_tier) == str(resources_utils.NetworkTier.BEST)):
                try:
                    fabric = nebius_constants.get_default_fabric(
                        platform, region)
                    logger.info(f'Auto-selected InfiniBand fabric {fabric} '
                                f'for {platform} in {region}')
                except ValueError as e:
                    logger.warning(
                        f'InfiniBand fabric auto-selection failed: {e}')

            if fabric is None:
                logger.warning(
                    f'Set up fabric for region {region} in ~/.sky/config.yaml '
                    'to use GPU clusters.')
            else:
                cluster_id = get_or_create_gpu_cluster(cluster_name_on_cloud,
                                                       project_id, fabric)

    service = nebius.compute().DiskServiceClient(nebius.sdk())
    disk = nebius.sync_call(
        service.create(nebius.compute().CreateDiskRequest(
            metadata=nebius.nebius_common().ResourceMetadata(
                parent_id=project_id,
                name=disk_name,
            ),
            spec=nebius.compute().DiskSpec(
                source_image_family=nebius.compute().SourceImageFamily(
                    image_family=image_family),
                size_gibibytes=disk_size,
                type=nebius.compute().DiskSpec.DiskType.NETWORK_SSD,
            ))))
    disk_id = disk.resource_id
    retry_count = 0
    while retry_count < nebius.MAX_RETRIES_TO_DISK_CREATE:
        disk = nebius.sync_call(
            service.get_by_name(nebius.nebius_common().GetByNameRequest(
                parent_id=project_id,
                name=disk_name,
            )))
        if disk.status.state.name == 'READY':
            break
        logger.debug(f'Waiting for disk {disk_name} to be ready.')
        time.sleep(POLL_INTERVAL)
        retry_count += 1

    if retry_count == nebius.MAX_RETRIES_TO_DISK_CREATE:
        raise TimeoutError(
            f'Exceeded maximum retries '
            f'({nebius.MAX_RETRIES_TO_DISK_CREATE * POLL_INTERVAL}'
            f' seconds) while waiting for disk {disk_name}'
            f' to be ready.')

    filesystems_spec = []
    if filesystems:
        for fs in filesystems:
            filesystems_spec.append(nebius.compute().AttachedFilesystemSpec(
                mount_tag=fs['filesystem_mount_tag'],
                attach_mode=nebius.compute().AttachedFilesystemSpec.AttachMode[
                    fs['filesystem_attach_mode']],
                existing_filesystem=nebius.compute().ExistingFilesystem(
                    id=fs['filesystem_id'])))

    service = nebius.vpc().SubnetServiceClient(nebius.sdk())
    sub_net = nebius.sync_call(
        service.list(nebius.vpc().ListSubnetsRequest(parent_id=project_id,)))

    service = nebius.compute().InstanceServiceClient(nebius.sdk())
    logger.debug(f'Creating instance {instance_name} in project {project_id}.')
    nebius.sync_call(
        service.create(nebius.compute().CreateInstanceRequest(
            metadata=nebius.nebius_common().ResourceMetadata(
                parent_id=project_id,
                name=instance_name,
            ),
            spec=nebius.compute().InstanceSpec(
                gpu_cluster=nebius.compute().InstanceGpuClusterSpec(
                    id=cluster_id,) if cluster_id is not None else None,
                boot_disk=nebius.compute().AttachedDiskSpec(
                    attach_mode=nebius.compute(
                    ).AttachedDiskSpec.AttachMode.READ_WRITE,
                    existing_disk=nebius.compute().ExistingDisk(id=disk_id)),
                cloud_init_user_data=user_data,
                resources=nebius.compute().ResourcesSpec(platform=platform,
                                                         preset=preset),
                filesystems=filesystems_spec if filesystems_spec else None,
                network_interfaces=[
                    nebius.compute().NetworkInterfaceSpec(
                        subnet_id=sub_net.items[0].metadata.id,
                        ip_address=nebius.compute().IPAddress(),
                        name='network-interface-0',
                        public_ip_address=nebius.compute().PublicIPAddress()
                        if associate_public_ip_address else None,
                    )
                ],
                recovery_policy=nebius.compute().InstanceRecoveryPolicy.FAIL
                if use_spot else None,
                preemptible=nebius.compute().PreemptibleSpec(
                    priority=1,
                    on_preemption=nebius.compute().PreemptibleSpec.
                    PreemptionPolicy.STOP) if use_spot else None,
            ))))
    instance_id = ''
    retry_count = 0
    while retry_count < nebius.MAX_RETRIES_TO_INSTANCE_READY:
        service = nebius.compute().InstanceServiceClient(nebius.sdk())
        instance = nebius.sync_call(
            service.get_by_name(nebius.nebius_common().GetByNameRequest(
                parent_id=project_id,
                name=instance_name,
            )))
        instance_id = instance.metadata.id
        if instance.status.state.name == 'STARTING':
            break

        # All Instances initially have state=STOPPED and reconciling=True,
        # so we need to wait until reconciling is False.
        if instance.status.state.name == 'STOPPED' and \
                not instance.status.reconciling:
            next_token = ''
            total_operations = 0
            while True:
                operations_response = nebius.sync_call(
                    service.list_operations_by_parent(
                        nebius.compute().ListOperationsByParentRequest(
                            parent_id=project_id,
                            page_size=100,
                            page_token=next_token,
                        )))
                total_operations += len(operations_response.operations)
                for operation in operations_response.operations:
                    # Find the most recent operation for the instance.
                    if operation.resource_id == instance_id:
                        error_msg = operation.description
                        if operation.status:
                            error_msg += f' {operation.status.message}'
                        raise RuntimeError(error_msg)
                # If we've fetched too many operations, or there are no more
                # operations to fetch, just raise a generic error.
                if total_operations > _MAX_OPERATIONS_TO_FETCH or \
                        not operations_response.next_page_token:
                    raise RuntimeError(
                        f'Instance {instance_name} failed to start.')
                next_token = operations_response.next_page_token
        time.sleep(POLL_INTERVAL)
        logger.debug(f'Waiting for instance {instance_name} to start running. '
                     f'State: {instance.status.state.name}, '
                     f'Reconciling: {instance.status.reconciling}')
        retry_count += 1

    if retry_count == nebius.MAX_RETRIES_TO_INSTANCE_READY:
        raise TimeoutError(
            f'Exceeded maximum retries '
            f'({nebius.MAX_RETRIES_TO_INSTANCE_READY * POLL_INTERVAL}'
            f' seconds) while waiting for instance {instance_name}'
            f' to be ready.')
    return instance_id


def remove(instance_id: str) -> None:
    """Terminates the given instance."""
    service = nebius.compute().InstanceServiceClient(nebius.sdk())
    result = nebius.sync_call(
        service.get(nebius.compute().GetInstanceRequest(id=instance_id)))
    disk_id = result.spec.boot_disk.existing_disk.id
    nebius.sync_call(
        service.delete(nebius.compute().DeleteInstanceRequest(id=instance_id)))
    retry_count = 0
    # The instance begins deleting and attempts to delete the disk.
    # Must wait until the disk is unlocked and becomes deletable.
    while retry_count < nebius.MAX_RETRIES_TO_DISK_DELETE:
        try:
            service = nebius.compute().DiskServiceClient(nebius.sdk())
            nebius.sync_call(
                service.delete(nebius.compute().DeleteDiskRequest(id=disk_id)))
            break
        except nebius.request_error():
            logger.debug('Waiting for disk deletion.')
            time.sleep(POLL_INTERVAL)
            retry_count += 1

    if retry_count == nebius.MAX_RETRIES_TO_DISK_DELETE:
        raise TimeoutError(
            f'Exceeded maximum retries '
            f'({nebius.MAX_RETRIES_TO_DISK_DELETE * POLL_INTERVAL}'
            f' seconds) while waiting for disk {disk_id}'
            f' to be deleted.')
