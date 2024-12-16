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

with open(os.path.expanduser('~/.nebius/NEBIUS_IAM_TOKEN.txt'), 'r') as file:
    NEBIUS_IAM_TOKEN, NB_PROJECT_ID = file.read().strip().split('\n')
sdk = SDK(credentials=NEBIUS_IAM_TOKEN)

logger = sky_logging.init_logger(__name__)

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

def launch(name: str, instance_type: str, region: str, disk_size: int,
           image_name: str, public_key: str) -> str:
    logger.debug("Launching instance '%s'", name)
    """Launches an instance with the given parameters.

    Converts the instance_type to the RunPod GPU name, finds the specs for the
    GPU, and launches the instance.
    """
    # print('LAUNCH', instance_type, region, disk_size, image_name, public_key)
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
                source_image_family=SourceImageFamily(image_family="ubuntu22.04-driverless"),
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
            logger.info(f'Waiting for disk {disk_name} to be ready.')
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
                cloud_init_user_data="""
                users:
                  - name: ubuntu
                    sudo: ALL=(ALL) NOPASSWD:ALL
                    shell: /bin/bash
                    ssh_authorized_keys:
                      - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDLklNDmzf6c08IcPJgrlLrXs3WAkIB3rDWcLHQ6UGh7+Lq1SFheMriZVBYSsbweTt35OtF8NxGOcUZeYl0dE+QzVM+i5lhoBWiyt4Q6EAsGVpX3/jASKuc4aL6FeB44tJMmQhXekkkjGaP3UMp+/w7vy6P0dcGG6i6Ub8+lNpdxwlDZIMbD0964llkfDo6hjZpeolPsKdI8pKWXdglWnxnK3AAYxxWIbGNkOtSgZrte1wzDsva5K5itfF22jNNFJwJBImnTUF5PRaHLeM0loK7v5Kqf+3YzgyaQDVUfC+uJ6PdMYIDJCeOgOU9A2NSZ8XxHE/ogKxKsv6a8ekI1TZl
                """,
                resources=ResourcesSpec(
                    platform=instance_type.split('_')[0],
                    preset=instance_type.split('_')[1]
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
            logger.info(f'Waiting for instance {name} start running.')
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
