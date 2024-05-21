"""RunPod library wrapper for SkyPilot."""

import time
from typing import Any, Dict, List

from sky import sky_logging
from sky.adaptors import runpod
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

        if instance['desiredStatus'] == 'RUNNING' and instance.get('runtime'):
            for port in instance['runtime']['ports']:
                if port['privatePort'] == 22 and port['isIpPublic']:
                    info['external_ip'] = port['ip']
                    info['ssh_port'] = port['publicPort']
                elif not port['isIpPublic']:
                    info['internal_ip'] = port['ip']

        instance_dict[instance['id']] = info

    return instance_dict


def launch(name: str, instance_type: str, region: str, disk_size: int) -> str:
    """Launches an instance with the given parameters.

    Converts the instance_type to the RunPod GPU name, finds the specs for the
    GPU, and launches the instance.
    """
    gpu_type = GPU_NAME_MAP[instance_type.split('_')[1]]
    gpu_quantity = int(instance_type.split('_')[0].replace('x', ''))
    cloud_type = instance_type.split('_')[2]

    gpu_specs = runpod.runpod.get_gpu(gpu_type)

    new_instance = runpod.runpod.create_pod(
        name=name,
        image_name='runpod/base:0.0.2',
        gpu_type_id=gpu_type,
        cloud_type=cloud_type,
        container_disk_in_gb=disk_size,
        min_vcpu_count=4 * gpu_quantity,
        min_memory_in_gb=gpu_specs['memoryInGb'] * gpu_quantity,
        gpu_count=gpu_quantity,
        country_code=region,
        ports=(f'22/tcp,'
               f'{constants.SKY_REMOTE_RAY_DASHBOARD_PORT}/http,'
               f'{constants.SKY_REMOTE_RAY_PORT}/http'),
        support_public_ip=True,
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
