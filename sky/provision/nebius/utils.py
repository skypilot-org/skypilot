"""RunPod library wrapper for SkyPilot."""
import os
import time
from typing import Any, Dict, List

from nebius.api.nebius.common.v1 import ResourceMetadata, GetByNameRequest
from nebius.aio.service_error import RequestError
from nebius.api.nebius.vpc.v1 import SubnetServiceClient, ListSubnetsRequest
from nebius.api.nebius.compute.v1 import ListInstancesRequest, CreateInstanceRequest, InstanceSpec, \
    NetworkInterfaceSpec, IPAddress, ResourcesSpec, AttachedDiskSpec, ExistingDisk, DiskServiceClient, \
    CreateDiskRequest, DiskSpec, SourceImageFamily,InstanceServiceClient, InstanceStatus, DiskStatus, DeleteInstanceRequest, \
    PublicIPAddress, StopInstanceRequest, StartInstanceRequest, DeleteDiskRequest, GetInstanceRequest

from sky import sky_logging
from sky.adaptors import nebius
from sky.utils import common_utils

from nebius.sdk import SDK

POLL_INTERVAL = 5

logger = sky_logging.init_logger(__name__)

def get_iam_token_project_id() -> (str, str):
    with open(os.path.expanduser('~/.nebius/NEBIUS_IAM_TOKEN.txt'), 'r') as file:
        iam_token = file.read().strip()
    with open(os.path.expanduser('~/.nebius/NB_PROJECT_ID.txt'), 'r') as file:
        project_id = file.read().strip()
    return iam_token, project_id


NEBIUS_IAM_TOKEN, NB_PROJECT_ID = get_iam_token_project_id()
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

def list_instances() -> Dict[str, Dict[str, Any]]:
    """Lists instances associated with API key."""
    service = InstanceServiceClient(sdk)
    result = service.list(ListInstancesRequest(
        parent_id = "project-e00w18sheap5emdjx8"
    )).wait()

    instances = result

    instance_dict: Dict[str, Dict[str, Any]] = {}
    for instance in instances.items:
        info = {}
        # FIX later
        info['status'] = InstanceStatus.InstanceState(instance.status.state).name
        info['name'] = instance.metadata.name
        info['port2endpoint'] = {}
        if instance.status.network_interfaces:
            info['external_ip'] = instance.status.network_interfaces[0].public_ip_address.address.split('/')[0]
            info['internal_ip'] = instance.status.network_interfaces[0].ip_address.address.split('/')[0]
        instance_dict[instance.metadata.id] = info

    return instance_dict

def stop(instance_id: str) -> None:
    service = InstanceServiceClient(sdk)
    service.stop(StopInstanceRequest(
        id=instance_id
    )).wait()

def start(instance_id: str) -> None:
    service = InstanceServiceClient(sdk)
    service.start(StartInstanceRequest(
        id=instance_id
    )).wait()

def launch(name: str, instance_type: str, region: str, disk_size: int, user_data: str) -> str:
    logger.debug("Launching instance '%s'", name)
    """Launches an instance with the given parameters.

    Converts the instance_type to the RunPod GPU name, finds the specs for the
    GPU, and launches the instance.
    """
    platform, preset = instance_type.split('_')
    if platform in ('cpu-d3', 'cpu-e2'):
        image_family = 'ubuntu22.04-driverless'
    elif platform in ('gpu-h100-sxm', 'gpu-h200-sxm', 'gpu-l40s-a'):
        image_family = 'ubuntu22.04-cuda12'
    else:
        raise RuntimeError(f"Unsupported platform: {platform}")
    disk_name = 'disk-'+name
    try:
        service = DiskServiceClient(sdk)
        disk = service.get_by_name(GetByNameRequest(
                parent_id=NB_PROJECT_ID,
                name=disk_name,
        )).wait()
        disk_id = disk.metadata.id
    except RequestError:
        service = DiskServiceClient(sdk)
        disk = service.create(CreateDiskRequest(
            metadata=ResourceMetadata(
                parent_id=NB_PROJECT_ID,
                name=disk_name,
            ),
            spec=DiskSpec(
                source_image_family=SourceImageFamily(image_family=image_family),
                size_gibibytes=disk_size,
                type=DiskSpec.DiskType.NETWORK_SSD,
            )

        )).wait()
        disk_id = disk.resource_id
        while True:
            disk = service.get_by_name(GetByNameRequest(
                parent_id=NB_PROJECT_ID,
                name=disk_name,
            )).wait()
            if DiskStatus.State(disk.status.state).name == "READY":
                break
            logger.debug(f'Waiting for disk {disk_name} to be ready.')
            time.sleep(POLL_INTERVAL)
    try:
        service = InstanceServiceClient(sdk)
        instance = service.get_by_name(GetByNameRequest(
                parent_id=NB_PROJECT_ID,
                name=name,
        )).wait()
        start(instance.metadata.id)
        instance_id = instance.metadata.id
    except RequestError:
        service = SubnetServiceClient(sdk)
        sub_net = service.list(ListSubnetsRequest(
            parent_id=NB_PROJECT_ID,
        )).wait()

        service = InstanceServiceClient(sdk)
        service.create(CreateInstanceRequest(
            metadata=ResourceMetadata(
                parent_id=NB_PROJECT_ID,
                name=name,
            ),
            spec=InstanceSpec(
                boot_disk=AttachedDiskSpec(
                    attach_mode=AttachedDiskSpec.AttachMode(2),
                    existing_disk = ExistingDisk(
                        id=disk_id
                    )

                ),
                cloud_init_user_data=user_data,
                resources=ResourcesSpec(
                    platform=platform,
                    preset=preset
                ),
                network_interfaces=[NetworkInterfaceSpec(
                    subnet_id = sub_net.items[0].metadata.id,
                    ip_address = IPAddress(),
                    name='network-interface-0',
                    public_ip_address=PublicIPAddress()
                )]
            )
        )).wait()
        while True:
            service = InstanceServiceClient(sdk)
            instance = service.get_by_name(GetByNameRequest(
                parent_id=NB_PROJECT_ID,
                name=name,
            )).wait()
            if instance.status.state.name == "STARTING":
                break
            time.sleep(POLL_INTERVAL)
            logger.debug(f'Waiting for instance {name} start running.')
        instance_id = instance.metadata.id
    return instance_id


def remove(instance_id: str) -> None:
    """Terminates the given instance."""
    service = InstanceServiceClient(sdk)
    result = service.get(GetInstanceRequest(
            id=instance_id
        )).wait()
    disk_id = result.spec.boot_disk.existing_disk.id
    service.delete(DeleteInstanceRequest(
        id=instance_id
    )).wait()
    while True:
        try:
            service = DiskServiceClient(sdk)
            service.delete(DeleteDiskRequest(
                id=disk_id
            )).wait()
            break
        except Exception as e:
            logger.debug(f'Waiting for disk deletion. {e}')
            time.sleep(POLL_INTERVAL)


def get_ssh_ports(cluster_name) -> List[int]:
    """Gets the SSH ports for the given cluster."""
    logger.debug(f'Getting SSH ports for cluster {cluster_name}.')

    instances = list_instances()
    possible_names = [f'{cluster_name}-head', f'{cluster_name}-worker']

    ssh_ports = []

    for instance in instances.values():
        if instance['name'] in possible_names:
            ssh_ports.append(22)
    assert ssh_ports, (
        f'Could not find any instances for cluster {cluster_name}.')

    return ssh_ports
