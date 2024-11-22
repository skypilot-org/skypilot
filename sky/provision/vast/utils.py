# pylint: disable=assignment-from-no-return
"""Vast library wrapper for SkyPilot."""
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky.adaptors import vast

logger = sky_logging.init_logger(__name__)


def list_instances() -> Dict[str, Dict[str, Any]]:
    """Lists instances associated with API key."""
    instances = vast.vast().show_instances()

    instance_dict: Dict[str, Dict[str, Any]] = {}
    for instance in instances:
        instance['id'] = str(instance['id'])
        info = instance.copy()

        if isinstance(instance['actual_status'], str):
            info['status'] = instance['actual_status'].upper()
        else:
            info['status'] = 'UNKNOWN'
        info['name'] = instance['label']

        instance_dict[instance['id']] = info

    return instance_dict


def launch(name: str, instance_type: str, region: str, disk_size: int,
           image_name: str, ports: Optional[List[int]], public_key: str) -> str:
    """Launches an instance with the given parameters.

    Converts the instance_type to the Vast GPU name, finds the specs for the
    GPU, and launches the instance.
    """
    del ports
    del public_key

    gpu_name = instance_type.split('-')[1].replace('_', ' ')
    num_gpus = int(instance_type.split('-')[0].replace('x', ''))

    query = ' '.join([
        f'geolocation="{region[-2:]}"',
        f'disk_space>={disk_size}',
        f'num_gpus={num_gpus}',
        f'gpu_name="{gpu_name}"',
    ])

    instance_list = vast.vast().search_offers(query=query)

    if isinstance(instance_list, int) or len(instance_list) == 0:
        return ''

    instance_touse = instance_list[0]

    new_instance_contract = vast.vast().create_instance(
        id=instance_touse['id'],
        direct=True,
        ssh=True,
        env='-e __SOURCE=skypilot',
        onstart_cmd='touch ~/.no_auto_tmux;apt install lsof',
        label=name,
        image=image_name)

    new_instance = vast.vast().show_instance(
        id=new_instance_contract['new_contract'])

    return new_instance['id']


def start(instance_id: str) -> None:
    """Stops the given instance."""
    vast.vast().start_instance(id=instance_id)


def stop(instance_id: str) -> None:
    """Stops the given instance."""
    vast.vast().stop_instance(id=instance_id)


def remove(instance_id: str) -> None:
    """Terminates the given instance."""
    vast.vast().destroy_instance(id=instance_id)


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
