"""RunPod library wrapper for SkyPilot."""
import os
import time
from typing import Any, Dict, List, Optional

from nebius.api.nebius.common.v1 import ResourceMetadata, GetByNameRequest
from nebius.aio.service_error import RequestError

from sky import sky_logging
from sky.adaptors import nebius
from sky.utils import common_utils

from nebius.api.nebius.compute.v1 import ListInstancesRequest, CreateInstanceRequest, InstanceSpec, \
    NetworkInterfaceSpec, IPAddress, ResourcesSpec, AttachedDiskSpec, ExistingDisk, DiskServiceClient, \
    CreateDiskRequest, DiskSpec, SourceImageFamily, InstanceStatus, DiskStatus, DeleteInstanceRequest, \
    PublicIPAddress
from nebius.api.nebius.compute.v1 import InstanceServiceClient

from nebius.sdk import SDK

DEFAULT_NEBIUS_TOKEN_PATH = os.path.expanduser('~/.nebius/NEBIUS_IAM_TOKEN.txt')
with open(DEFAULT_NEBIUS_TOKEN_PATH, 'r') as file:
    NEBIUS_IAM_TOKEN = file.read().strip()
sdk = SDK(credentials=NEBIUS_IAM_TOKEN)

logger = sky_logging.init_logger(__name__)

GPU_NAME_MAP = {
    'A100-80GB': 'NVIDIA A100 80GB PCIe',
    'A100-40GB': 'NVIDIA A100-PCIE-40GB',
    'A100-80GB-SXM': 'NVIDIA A100-SXM4-80GB',
    'A30': 'NVIDIA A30',
    'A40': 'NVIDIA A40',
    'RTX3070': 'NVIDIA GeForce RTX 3070',
    'RTX3080': 'NVIDIA GeForce RTX 3080',
    'RTX3080Ti': 'NVIDIA GeForce RTX 3080 Ti',
    'RTX3090': 'NVIDIA GeForce RTX 3090',
    'RTX3090Ti': 'NVIDIA GeForce RTX 3090 Ti',
    'RTX4070Ti': 'NVIDIA GeForce RTX 4070 Ti',
    'RTX4080': 'NVIDIA GeForce RTX 4080',
    'RTX4090': 'NVIDIA GeForce RTX 4090',
    # Following instance is displayed as SXM at the console
    # but the ID from the API appears as HBM
    'H100-SXM': 'NVIDIA H100 80GB HBM3',
    'H100': 'NVIDIA H100 PCIe',
    'L4': 'NVIDIA L4',
    'L40': 'NVIDIA L40',
    'RTX4000-Ada-SFF': 'NVIDIA RTX 4000 SFF Ada Generation',
    'RTX4000-Ada': 'NVIDIA RTX 4000 Ada Generation',
    'RTX6000-Ada': 'NVIDIA RTX 6000 Ada Generation',
    'RTXA4000': 'NVIDIA RTX A4000',
    'RTXA4500': 'NVIDIA RTX A4500',
    'RTXA5000': 'NVIDIA RTX A5000',
    'RTXA6000': 'NVIDIA RTX A6000',
    'RTX5000': 'Quadro RTX 5000',
    'V100-16GB-FHHL': 'Tesla V100-FHHL-16GB',
    'V100-16GB-SXM2': 'V100-SXM2-16GB',
    'RTXA2000': 'NVIDIA RTX A2000',
    'V100-16GB-PCIe': 'Tesla V100-PCIE-16GB'
}


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
                time.sleep(1)

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
        # print('geted instance ::', instance)

        info = {}
        # FIX later
        info['status'] = InstanceStatus.InstanceState(instance.status.state).name
        info['name'] = instance.metadata.name
        info['port2endpoint'] = {}
        if instance.status.network_interfaces:
            info['external_ip'] = instance.status.network_interfaces[0].public_ip_address.address.split('/')[0]
            info['internal_ip'] = instance.status.network_interfaces[0].ip_address.address.split('/')[0]
        info['ssh_port'] = 22
        # Sometimes when the cluster is in the process of being created,
        # the `port` field in the runtime is None and we need to check for it.
        # if (instance['desiredStatus'] == 'RUNNING' and
    #             instance.get('runtime') and
    #             instance.get('runtime').get('ports')):
    #         for port in instance['runtime']['ports']:
    #             if port['isIpPublic']:
    #                 if port['privatePort'] == 22:
    #                     info['external_ip'] = port['ip']
    #                     info['ssh_port'] = port['publicPort']
    #                 info['port2endpoint'][port['privatePort']] = {
    #                     'host': port['ip'],
    #                     'port': port['publicPort']
    #                 }
    #             else:
    #                 info['internal_ip'] = port['ip']
    #
        instance_dict[instance.metadata.id] = info

    return instance_dict


def launch(name: str, instance_type: str, region: str, disk_size: int,
           image_name: str, ports: Optional[List[int]], public_key: str) -> str:
    logger.debug("Launching instance '%s'", name)
    """Launches an instance with the given parameters.

    Converts the instance_type to the RunPod GPU name, finds the specs for the
    GPU, and launches the instance.
    """
    print('LAUNCH', instance_type, region, disk_size, image_name, ports, public_key)
    disk_name = 'disk-'+name
    try:
        service = DiskServiceClient(sdk)
        disk = service.get_by_name(GetByNameRequest(
                parent_id="project-e00w18sheap5emdjx8",
                name=disk_name,
        )).wait()
        disk_id = disk.metadata.id
    except RequestError:
        service = DiskServiceClient(sdk)
        disk = service.create(CreateDiskRequest(
            metadata=ResourceMetadata(
                parent_id="project-e00w18sheap5emdjx8",
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
                parent_id="project-e00w18sheap5emdjx8",
                name=disk_name,
            )).wait()
            print('waiting :: ', DiskStatus.State(disk.status.state).name, disk_name)
            if DiskStatus.State(disk.status.state).name == "READY":
                break
            logger.info(f'Waiting for disk {disk_name} to be ready.')
            time.sleep(5)
    print('GET DISK::', disk_id)

    service = InstanceServiceClient(sdk)
    result = service.create(CreateInstanceRequest(
        metadata=ResourceMetadata(
            parent_id="project-e00w18sheap5emdjx8",
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
                platform='cpu-e2',
                preset='2vcpu-8gb'
            ),
            network_interfaces=[NetworkInterfaceSpec(
                subnet_id = 'vpcsubnet-e00jxg9a1tbkz54c49',
                ip_address = IPAddress(),
                name='network-interface-0',
                public_ip_address=PublicIPAddress()
            )]
        )
    )).wait()
    # To avoid Stopped status
    time.sleep(5)
    print('launch RESULT::', result, result.resource_id)

    #
    # gpu_type = GPU_NAME_MAP[instance_type.split('_')[1]]
    # gpu_quantity = int(instance_type.split('_')[0].replace('x', ''))
    # cloud_type = instance_type.split('_')[2]
    #
    # gpu_specs = nebius.nebius.get_gpu(gpu_type)
    # # TODO(zhwu): keep this align with setups in
    # # `provision.kuberunetes.instance.py`
    # setup_cmd = (
    #     'prefix_cmd() '
    #     '{ if [ $(id -u) -ne 0 ]; then echo "sudo"; else echo ""; fi; }; '
    #     '$(prefix_cmd) apt update;'
    #     'export DEBIAN_FRONTEND=noninteractive;'
    #     '$(prefix_cmd) apt install openssh-server rsync curl patch -y;'
    #     '$(prefix_cmd) mkdir -p /var/run/sshd; '
    #     '$(prefix_cmd) '
    #     'sed -i "s/PermitRootLogin prohibit-password/PermitRootLogin yes/" '
    #     '/etc/ssh/sshd_config; '
    #     '$(prefix_cmd) sed '
    #     '"s@session\\s*required\\s*pam_loginuid.so@session optional '
    #     'pam_loginuid.so@g" -i /etc/pam.d/sshd; '
    #     'cd /etc/ssh/ && $(prefix_cmd) ssh-keygen -A; '
    #     '$(prefix_cmd) mkdir -p ~/.ssh; '
    #     '$(prefix_cmd) chown -R $(whoami) ~/.ssh;'
    #     '$(prefix_cmd) chmod 700 ~/.ssh; '
    #     f'$(prefix_cmd) echo "{public_key}" >> ~/.ssh/authorized_keys; '
    #     '$(prefix_cmd) chmod 644 ~/.ssh/authorized_keys; '
    #     '$(prefix_cmd) service ssh restart; '
    #     '[ $(id -u) -eq 0 ] && echo alias sudo="" >> ~/.bashrc;sleep infinity')
    # # Use base64 to deal with the tricky quoting issues caused by runpod API.
    # encoded = base64.b64encode(setup_cmd.encode('utf-8')).decode('utf-8')
    #
    # # Port 8081 is occupied for nginx in the base image.
    # custom_ports_str = ''
    # if ports is not None:
    #     custom_ports_str = ''.join([f'{p}/tcp,' for p in ports])
    #
    #
    # new_instance = nebius.nebius.create_pod(
    #     name=name,
    #     image_name=image_name,
    #     gpu_type_id=gpu_type,
    #     cloud_type=cloud_type,
    #     container_disk_in_gb=disk_size,
    #     min_vcpu_count=4 * gpu_quantity,
    #     min_memory_in_gb=gpu_specs['memoryInGb'] * gpu_quantity,
    #     gpu_count=gpu_quantity,
    #     country_code=region,
    #     ports=(f'22/tcp,'
    #            f'{custom_ports_str}'
    #            f'{constants.SKY_REMOTE_RAY_DASHBOARD_PORT}/http,'
    #            f'{constants.SKY_REMOTE_RAY_PORT}/http'),
    #     support_public_ip=True,
    #     docker_args=
    #     f'bash -c \'echo {encoded} | base64 --decode > init.sh; bash init.sh\'')

    return result.resource_id


def remove(instance_id: str) -> None:
    """Terminates the given instance."""
    service = InstanceServiceClient(sdk)
    result = service.delete(DeleteInstanceRequest(
        id=instance_id
    )).wait()
    print("REMOVED:", instance_id)


def get_ssh_ports(cluster_name) -> List[int]:
    """Gets the SSH ports for the given cluster."""
    logger.debug(f'Getting SSH ports for cluster {cluster_name}.')

    instances = list_instances()
    possible_names = [f'{cluster_name}-head', f'{cluster_name}-worker']

    ssh_ports = []

    for instance in instances.values():
        if instance['name'] in possible_names:
            ssh_ports.append(instance['ssh_port'])
    assert ssh_ports, (
        f'Could not find any instances for cluster {cluster_name}.')

    return ssh_ports
