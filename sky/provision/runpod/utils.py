"""RunPod library wrapper for SkyPilot."""

import base64
import time
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky.adaptors import runpod
import sky.provision.runpod.api.commands as runpod_commands
from sky.skylet import constants
from sky.utils import common_utils

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
            except runpod.runpod.error.QueryError as e:
                if cnt >= 3:
                    raise
                logger.warning('Retrying for exception: '
                               f'{common_utils.format_exception(e)}.')
                time.sleep(1)

    return wrapper


def list_instances() -> Dict[str, Dict[str, Any]]:
    """Lists instances associated with API key."""
    instances = runpod.runpod.get_pods()

    instance_dict: Dict[str, Dict[str, Any]] = {}
    for instance in instances:
        info = {}

        info['status'] = instance['desiredStatus']
        info['name'] = instance['name']
        info['port2endpoint'] = {}

        # Sometimes when the cluster is in the process of being created,
        # the `port` field in the runtime is None and we need to check for it.
        if (instance['desiredStatus'] == 'RUNNING' and
                instance.get('runtime') and
                instance.get('runtime').get('ports')):
            for port in instance['runtime']['ports']:
                if port['isIpPublic']:
                    if port['privatePort'] == 22:
                        info['external_ip'] = port['ip']
                        info['ssh_port'] = port['publicPort']
                    info['port2endpoint'][port['privatePort']] = {
                        'host': port['ip'],
                        'port': port['publicPort']
                    }
                else:
                    info['internal_ip'] = port['ip']

        instance_dict[instance['id']] = info

    return instance_dict


def launch(name: str, instance_type: str, region: str, disk_size: int,
           image_name: str, ports: Optional[List[int]], public_key: str,
           preemptible: Optional[bool], bid_per_gpu: float) -> str:
    """Launches an instance with the given parameters.

    Converts the instance_type to the RunPod GPU name, finds the specs for the
    GPU, and launches the instance.
    """
    gpu_type = GPU_NAME_MAP[instance_type.split('_')[1]]
    gpu_quantity = int(instance_type.split('_')[0].replace('x', ''))
    cloud_type = instance_type.split('_')[2]

    gpu_specs = runpod.runpod.get_gpu(gpu_type)
    # TODO(zhwu): keep this align with setups in
    # `provision.kuberunetes.instance.py`
    setup_cmd = (
        'prefix_cmd() '
        '{ if [ $(id -u) -ne 0 ]; then echo "sudo"; else echo ""; fi; }; '
        '$(prefix_cmd) apt update;'
        'export DEBIAN_FRONTEND=noninteractive;'
        '$(prefix_cmd) apt install openssh-server rsync curl patch -y;'
        '$(prefix_cmd) mkdir -p /var/run/sshd; '
        '$(prefix_cmd) '
        'sed -i "s/PermitRootLogin prohibit-password/PermitRootLogin yes/" '
        '/etc/ssh/sshd_config; '
        '$(prefix_cmd) sed '
        '"s@session\\s*required\\s*pam_loginuid.so@session optional '
        'pam_loginuid.so@g" -i /etc/pam.d/sshd; '
        'cd /etc/ssh/ && $(prefix_cmd) ssh-keygen -A; '
        '$(prefix_cmd) mkdir -p ~/.ssh; '
        '$(prefix_cmd) chown -R $(whoami) ~/.ssh;'
        '$(prefix_cmd) chmod 700 ~/.ssh; '
        f'$(prefix_cmd) echo "{public_key}" >> ~/.ssh/authorized_keys; '
        '$(prefix_cmd) chmod 644 ~/.ssh/authorized_keys; '
        '$(prefix_cmd) service ssh restart; '
        '[ $(id -u) -eq 0 ] && echo alias sudo="" >> ~/.bashrc;sleep infinity')
    # Use base64 to deal with the tricky quoting issues caused by runpod API.
    encoded = base64.b64encode(setup_cmd.encode('utf-8')).decode('utf-8')

    # Port 8081 is occupied for nginx in the base image.
    custom_ports_str = ''
    if ports is not None:
        custom_ports_str = ''.join([f'{p}/tcp,' for p in ports])

    docker_args = (f'bash -c \'echo {encoded} | base64 --decode > init.sh; '
                   f'bash init.sh\'')
    ports = (f'22/tcp,'
             f'{custom_ports_str}'
             f'{constants.SKY_REMOTE_RAY_DASHBOARD_PORT}/http,'
             f'{constants.SKY_REMOTE_RAY_PORT}/http')

    params = {
        'name': name,
        'image_name': image_name,
        'gpu_type_id': gpu_type,
        'cloud_type': cloud_type,
        'container_disk_in_gb': disk_size,
        'min_vcpu_count': 4 * gpu_quantity,
        'min_memory_in_gb': gpu_specs['memoryInGb'] * gpu_quantity,
        'gpu_count': gpu_quantity,
        'country_code': region,
        'ports': ports,
        'support_public_ip': True,
        'docker_args': docker_args,
    }

    if preemptible is None or not preemptible:
        new_instance = runpod.runpod.create_pod(**params)
    else:
        new_instance = runpod_commands.create_spot_pod(
            bid_per_gpu=bid_per_gpu,
            **params,
        )

    return new_instance['id']


def remove(instance_id: str) -> None:
    """Terminates the given instance."""
    runpod.runpod.terminate_pod(instance_id)


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
