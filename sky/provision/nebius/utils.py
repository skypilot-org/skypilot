"""RunPod library wrapper for SkyPilot."""
import time
from typing import Any, Dict

from sky import sky_logging
from sky.adaptors import nebius
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

sdk = nebius.sdk(credentials=nebius.get_iam_token())

POLL_INTERVAL = 5


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
    service = nebius.iam().ProjectServiceClient(sdk)
    projects = service.list(nebius.iam().ListProjectsRequest(
        parent_id=nebius.get_tenant_id())).wait()
    for project in projects.items:
        if region == 'eu-north1' and project.metadata.id[8:11] == 'e00':
            return project.metadata.id
        if region == 'eu-west1' and project.metadata.id[8:11] == 'e01':
            return project.metadata.id
    raise Exception(f'No project found for region "{region}".')


def get_or_creat_gpu_cluster(name: str, region: str) -> str:
    """Creates a GPU cluster."""
    project_id = get_project_by_region(region)
    service = nebius.compute().GpuClusterServiceClient(sdk)
    try:
        cluster = service.get_by_name(nebius.nebius_common().GetByNameRequest(
            parent_id=project_id,
            name=name,
        )).wait()
        cluster_id = cluster.metadata.id
    except nebius.request_error():
        cluster = service.create(nebius.compute().CreateGpuClusterRequest(
            metadata=nebius.nebius_common().ResourceMetadata(
                parent_id=project_id,
                name=name,
            ),
            spec=nebius.compute().GpuClusterSpec(
                infiniband_fabric='fabric-4'))).wait()
        cluster_id = cluster.resource_id
    return cluster_id


def delete_cluster(name: str, region: str) -> None:
    """Delete a GPU cluster."""
    project_id = get_project_by_region(region)
    service = nebius.compute().GpuClusterServiceClient(sdk)
    try:
        cluster = service.get_by_name(nebius.nebius_common().GetByNameRequest(
            parent_id=project_id,
            name=name,
        )).wait()
        cluster_id = cluster.metadata.id
        logger.debug(f'Found GPU Cluster : {cluster_id}.')
        service.delete(
            nebius.compute().DeleteGpuClusterRequest(id=cluster_id)).wait()
        logger.debug(f'Deleted GPU Cluster : {cluster_id}.')
    except nebius.request_error():
        logger.debug('GPU Cluster does not exist.')
        pass
    return


def list_instances(project_id: str) -> Dict[str, Dict[str, Any]]:
    """Lists instances associated with API key."""
    service = nebius.compute().InstanceServiceClient(sdk)
    result = service.list(
        nebius.compute().ListInstancesRequest(parent_id=project_id)).wait()

    instances = result

    instance_dict: Dict[str, Dict[str, Any]] = {}
    for instance in instances.items:
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
    service = nebius.compute().InstanceServiceClient(sdk)
    service.stop(nebius.compute().StopInstanceRequest(id=instance_id)).wait()


def start(instance_id: str) -> None:
    service = nebius.compute().InstanceServiceClient(sdk)
    service.start(nebius.compute().StartInstanceRequest(id=instance_id)).wait()


def launch(name: str, instance_type: str, region: str, disk_size: int,
           user_data: str) -> str:
    logger.debug(f'Launching instance: {name}')
    platform, preset = instance_type.split('_')
    if platform in ('cpu-d3', 'cpu-e2'):
        image_family = 'ubuntu22.04-driverless'
    elif platform in ('gpu-h100-sxm', 'gpu-h200-sxm', 'gpu-l40s-a'):
        image_family = 'ubuntu22.04-cuda12'
    else:
        raise RuntimeError(f'Unsupported platform: {platform}')
    disk_name = 'disk-' + name
    cluster_id = None
    cluster_name = '-'.join(name.split('-')[:4])
    if platform in ('gpu-h100-sxm', 'gpu-h200-sxm'):
        if preset == '8gpu-128vcpu-1600gb':
            cluster_id = get_or_creat_gpu_cluster(cluster_name, region)

    project_id = get_project_by_region(region)
    service = nebius.compute().DiskServiceClient(sdk)
    disk = service.create(nebius.compute().CreateDiskRequest(
        metadata=nebius.nebius_common().ResourceMetadata(
            parent_id=project_id,
            name=disk_name,
        ),
        spec=nebius.compute().DiskSpec(
            source_image_family=nebius.compute().SourceImageFamily(
                image_family=image_family),
            size_gibibytes=disk_size,
            type=nebius.compute().DiskSpec.DiskType.NETWORK_SSD,
        ))).wait()
    disk_id = disk.resource_id
    retry_count = 0
    while retry_count < nebius.MAX_RETRIES_TO_DISK_CREATE:
        disk = service.get_by_name(nebius.nebius_common().GetByNameRequest(
            parent_id=project_id,
            name=disk_name,
        )).wait()
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

    service = nebius.vpc().SubnetServiceClient(sdk)
    sub_net = service.list(nebius.vpc().ListSubnetsRequest(
        parent_id=project_id,)).wait()

    service = nebius.compute().InstanceServiceClient(sdk)
    service.create(nebius.compute().CreateInstanceRequest(
        metadata=nebius.nebius_common().ResourceMetadata(
            parent_id=project_id,
            name=name,
        ),
        spec=nebius.compute().InstanceSpec(
            gpu_cluster=nebius.compute().InstanceGpuClusterSpec(id=cluster_id,)
            if cluster_id else None,
            boot_disk=nebius.compute().AttachedDiskSpec(
                attach_mode=nebius.compute(
                ).AttachedDiskSpec.AttachMode.READ_WRITE,
                existing_disk=nebius.compute().ExistingDisk(id=disk_id)),
            cloud_init_user_data=user_data,
            resources=nebius.compute().ResourcesSpec(platform=platform,
                                                     preset=preset),
            network_interfaces=[
                nebius.compute().NetworkInterfaceSpec(
                    subnet_id=sub_net.items[0].metadata.id,
                    ip_address=nebius.compute().IPAddress(),
                    name='network-interface-0',
                    public_ip_address=nebius.compute().PublicIPAddress())
            ]))).wait()
    instance_id = ''
    retry_count = 0
    while retry_count < nebius.MAX_RETRIES_TO_INSTANCE_READY:
        service = nebius.compute().InstanceServiceClient(sdk)
        instance = service.get_by_name(nebius.nebius_common().GetByNameRequest(
            parent_id=project_id,
            name=name,
        )).wait()
        if instance.status.state.name == 'STARTING':
            instance_id = instance.metadata.id
            break
        time.sleep(POLL_INTERVAL)
        logger.debug(f'Waiting for instance {name} start running.')
        retry_count += 1

    if retry_count == nebius.MAX_RETRIES_TO_INSTANCE_READY:
        raise TimeoutError(
            f'Exceeded maximum retries '
            f'({nebius.MAX_RETRIES_TO_INSTANCE_READY * POLL_INTERVAL}'
            f' seconds) while waiting for instance {name}'
            f' to be ready.')
    return instance_id


def remove(instance_id: str) -> None:
    """Terminates the given instance."""
    service = nebius.compute().InstanceServiceClient(sdk)
    result = service.get(
        nebius.compute().GetInstanceRequest(id=instance_id)).wait()
    disk_id = result.spec.boot_disk.existing_disk.id
    service.delete(
        nebius.compute().DeleteInstanceRequest(id=instance_id)).wait()
    retry_count = 0
    while retry_count < nebius.MAX_RETRIES_TO_DISK_DELETE:
        try:
            service = nebius.compute().DiskServiceClient(sdk)
            service.delete(
                nebius.compute().DeleteDiskRequest(id=disk_id)).wait()
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
