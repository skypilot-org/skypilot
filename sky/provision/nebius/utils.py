"""RunPod library wrapper for SkyPilot."""
import os
import time
from typing import Any, Dict

from nebius.aio.service_error import RequestError
from nebius.api.nebius.common.v1 import GetByNameRequest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute.v1 import AttachedDiskSpec
from nebius.api.nebius.compute.v1 import CreateDiskRequest
from nebius.api.nebius.compute.v1 import CreateGpuClusterRequest
from nebius.api.nebius.compute.v1 import CreateInstanceRequest
from nebius.api.nebius.compute.v1 import DeleteDiskRequest
from nebius.api.nebius.compute.v1 import DeleteGpuClusterRequest
from nebius.api.nebius.compute.v1 import DeleteInstanceRequest
from nebius.api.nebius.compute.v1 import DiskServiceClient
from nebius.api.nebius.compute.v1 import DiskSpec
from nebius.api.nebius.compute.v1 import ExistingDisk
from nebius.api.nebius.compute.v1 import GetInstanceRequest
from nebius.api.nebius.compute.v1 import GpuClusterServiceClient
from nebius.api.nebius.compute.v1 import GpuClusterSpec
from nebius.api.nebius.compute.v1 import InstanceGpuClusterSpec
from nebius.api.nebius.compute.v1 import InstanceServiceClient
from nebius.api.nebius.compute.v1 import InstanceSpec
from nebius.api.nebius.compute.v1 import IPAddress
from nebius.api.nebius.compute.v1 import ListInstancesRequest
from nebius.api.nebius.compute.v1 import NetworkInterfaceSpec
from nebius.api.nebius.compute.v1 import PublicIPAddress
from nebius.api.nebius.compute.v1 import ResourcesSpec
from nebius.api.nebius.compute.v1 import SourceImageFamily
from nebius.api.nebius.compute.v1 import StartInstanceRequest
from nebius.api.nebius.compute.v1 import StopInstanceRequest
from nebius.api.nebius.iam.v1 import ListProjectsRequest
from nebius.api.nebius.iam.v1 import ProjectServiceClient
from nebius.api.nebius.vpc.v1 import ListSubnetsRequest
from nebius.api.nebius.vpc.v1 import SubnetServiceClient
from nebius.sdk import SDK

from sky import sky_logging
from sky.adaptors import nebius
from sky.utils import common_utils

POLL_INTERVAL = 5

logger = sky_logging.init_logger(__name__)


def get_iam_token_project_id() -> Dict[str, str]:
    with open(os.path.expanduser('~/.nebius/NEBIUS_IAM_TOKEN.txt'),
              encoding='utf-8') as file:
        iam_token = file.read().strip()
    with open(os.path.expanduser('~/.nebius/NB_TENANT_ID.txt'),
              encoding='utf-8') as file:
        tenant_id = file.read().strip()
    return {'iam_token': iam_token, 'tenant_id': tenant_id}


params = get_iam_token_project_id()
NEBIUS_IAM_TOKEN = params['iam_token']
NB_TENANT_ID = params['tenant_id']

sdk = SDK(credentials=NEBIUS_IAM_TOKEN)


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
    service = ProjectServiceClient(sdk)
    projects = service.list(ListProjectsRequest(parent_id=NB_TENANT_ID,)).wait()
    for project in projects.items:
        if region == 'eu-north1' and project.metadata.id[8:11] == 'e00':
            return project.metadata.id
        if region == 'eu-west1' and project.metadata.id[8:11] == 'e01':
            return project.metadata.id
    raise Exception(f'No project found for region "{region}".')


def get_or_creat_gpu_cluster(name: str, project_id: str) -> str:
    """Creates a GPU cluster."""
    service = GpuClusterServiceClient(sdk)
    try:
        cluster = service.get_by_name(
            GetByNameRequest(
                parent_id=project_id,
                name=name,
            )).wait()
        cluster_id = cluster.metadata.id
    except RequestError:
        cluster = service.create(
            CreateGpuClusterRequest(
                metadata=ResourceMetadata(
                    parent_id=project_id,
                    name=name,
                ),
                spec=GpuClusterSpec(infiniband_fabric='fabric-4'))).wait()
        cluster_id = cluster.resource_id
    return cluster_id


def delete_cluster(name: str, project_id: str) -> None:
    """Delete a GPU cluster."""
    service = GpuClusterServiceClient(sdk)
    try:
        cluster = service.get_by_name(
            GetByNameRequest(
                parent_id=project_id,
                name=name,
            )).wait()
        cluster_id = cluster.metadata.id
        logger.debug(f'Found GPU Cluster : {cluster_id}.')
        service.delete(DeleteGpuClusterRequest(id=cluster_id)).wait()
        logger.debug(f'Deleted GPU Cluster : {cluster_id}.')
    except RequestError as e:
        logger.debug(f'GPU Cluster does not exist or can not deleted {e}.')
        pass
    return


def list_instances(project_id: str) -> Dict[str, Dict[str, Any]]:
    """Lists instances associated with API key."""
    service = InstanceServiceClient(sdk)
    result = service.list(ListInstancesRequest(parent_id=project_id)).wait()

    instances = result

    instance_dict: Dict[str, Dict[str, Any]] = {}
    for instance in instances.items:
        info = {}
        # FIX later
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
    service = InstanceServiceClient(sdk)
    service.stop(StopInstanceRequest(id=instance_id)).wait()


def start(instance_id: str) -> None:
    service = InstanceServiceClient(sdk)
    service.start(StartInstanceRequest(id=instance_id)).wait()


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
    project_id = get_project_by_region(region)
    cluster_id = None
    cluster_name = '-'.join(name.split('-')[:4])
    if platform in ('gpu-h100-sxm', 'gpu-h200-sxm'):
        if preset == '8gpu-128vcpu-1600gb':
            cluster_id = get_or_creat_gpu_cluster(cluster_name, project_id)
    try:
        service = DiskServiceClient(sdk)
        disk = service.get_by_name(
            GetByNameRequest(
                parent_id=project_id,
                name=disk_name,
            )).wait()
        disk_id = disk.metadata.id
    except RequestError:
        service = DiskServiceClient(sdk)
        disk = service.create(
            CreateDiskRequest(metadata=ResourceMetadata(
                parent_id=project_id,
                name=disk_name,
            ),
                              spec=DiskSpec(
                                  source_image_family=SourceImageFamily(
                                      image_family=image_family),
                                  size_gibibytes=disk_size,
                                  type=DiskSpec.DiskType.NETWORK_SSD,
                              ))).wait()
        disk_id = disk.resource_id
        while True:
            disk = service.get_by_name(
                GetByNameRequest(
                    parent_id=project_id,
                    name=disk_name,
                )).wait()
            if disk.status.state.name == 'READY':
                break
            logger.debug(f'Waiting for disk {disk_name} to be ready.')
            time.sleep(POLL_INTERVAL)
    try:
        service = InstanceServiceClient(sdk)
        instance = service.get_by_name(
            GetByNameRequest(
                parent_id=project_id,
                name=name,
            )).wait()
        start(instance.metadata.id)
        instance_id = instance.metadata.id
    except RequestError:
        service = SubnetServiceClient(sdk)
        sub_net = service.list(ListSubnetsRequest(parent_id=project_id,)).wait()

        service = InstanceServiceClient(sdk)
        service.create(
            CreateInstanceRequest(
                metadata=ResourceMetadata(
                    parent_id=project_id,
                    name=name,
                ),
                spec=InstanceSpec(
                    gpu_cluster=InstanceGpuClusterSpec(id=cluster_id,)
                    if cluster_id else None,
                    boot_disk=AttachedDiskSpec(
                        attach_mode=AttachedDiskSpec.AttachMode(2),
                        existing_disk=ExistingDisk(id=disk_id)),
                    cloud_init_user_data=user_data,
                    resources=ResourcesSpec(platform=platform, preset=preset),
                    network_interfaces=[
                        NetworkInterfaceSpec(
                            subnet_id=sub_net.items[0].metadata.id,
                            ip_address=IPAddress(),
                            name='network-interface-0',
                            public_ip_address=PublicIPAddress())
                    ]))).wait()
        while True:
            service = InstanceServiceClient(sdk)
            instance = service.get_by_name(
                GetByNameRequest(
                    parent_id=project_id,
                    name=name,
                )).wait()
            if instance.status.state.name == 'STARTING':
                break
            time.sleep(POLL_INTERVAL)
            logger.debug(f'Waiting for instance {name} start running.')
        instance_id = instance.metadata.id
    return instance_id


def remove(instance_id: str) -> None:
    """Terminates the given instance."""
    service = InstanceServiceClient(sdk)
    result = service.get(GetInstanceRequest(id=instance_id)).wait()
    disk_id = result.spec.boot_disk.existing_disk.id
    service.delete(DeleteInstanceRequest(id=instance_id)).wait()
    while True:
        try:
            service = DiskServiceClient(sdk)
            service.delete(DeleteDiskRequest(id=disk_id)).wait()
            break
        except RequestError:
            logger.debug('Waiting for disk deletion.')
            time.sleep(POLL_INTERVAL)
