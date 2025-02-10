"""RunPod library wrapper for SkyPilot."""

import base64
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.adaptors import runpod
from sky.provision import docker_utils
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


def _construct_docker_login_template_name(cluster_name: str) -> str:
    """Constructs the registry auth template name."""
    return f'{cluster_name}-docker-login-template'


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


# Adapted from runpod.api.queries.pods.py::QUERY_POD.
# Adding containerRegistryAuthId to the query.
_QUERY_POD = """
query myPods {
    myself {
        pods {
            id
            containerDiskInGb
            containerRegistryAuthId
            costPerHr
            desiredStatus
            dockerArgs
            dockerId
            env
            gpuCount
            imageName
            lastStatusChange
            machineId
            memoryInGb
            name
            podType
            port
            ports
            uptimeSeconds
            vcpuCount
            volumeInGb
            volumeMountPath
            runtime {
                ports{
                    ip
                    isIpPublic
                    privatePort
                    publicPort
                    type
                }
            }
            machine {
                gpuDisplayName
            }
        }
    }
}
"""


def _sky_get_pods() -> dict:
    """List all pods with extra registry auth information.

    Adapted from runpod.get_pods() to include containerRegistryAuthId.
    """
    raw_return = runpod.runpod.api.graphql.run_graphql_query(_QUERY_POD)
    cleaned_return = raw_return['data']['myself']['pods']
    return cleaned_return


_QUERY_POD_TEMPLATE_WITH_REGISTRY_AUTH = """
query myself {
    myself {
        podTemplates {
            name
            containerRegistryAuthId
        }
    }
}
"""


def _list_pod_templates_with_container_registry() -> dict:
    """List all pod templates."""
    raw_return = runpod.runpod.api.graphql.run_graphql_query(
        _QUERY_POD_TEMPLATE_WITH_REGISTRY_AUTH)
    return raw_return['data']['myself']['podTemplates']


def list_instances() -> Dict[str, Dict[str, Any]]:
    """Lists instances associated with API key."""
    instances = _sky_get_pods()

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


def delete_pod_template(template_name: str) -> None:
    """Deletes a pod template."""
    try:
        runpod.runpod.api.graphql.run_graphql_query(
            f'mutation {{deleteTemplate(templateName: "{template_name}")}}')
    except runpod.runpod.error.QueryError as e:
        logger.warning(f'Failed to delete template {template_name}: {e} '
                       'Please delete it manually.')


def delete_register_auth(registry_auth_id: str) -> None:
    """Deletes a registry auth."""
    try:
        runpod.runpod.delete_container_registry_auth(registry_auth_id)
    except runpod.runpod.error.QueryError as e:
        logger.warning(
            f'Failed to delete registry auth {registry_auth_id}: {e} '
            'Please delete it manually.')


def _create_template_for_docker_login(
    cluster_name: str,
    image_name: str,
    docker_login_config: Optional[Dict[str, str]],
) -> Tuple[str, Optional[str]]:
    """Creates a template for the given image with the docker login config.

    Returns:
        formatted_image_name: The formatted image name.
        template_id: The template ID. None for no docker login config.
    """
    if docker_login_config is None:
        return image_name, None
    login_config = docker_utils.DockerLoginConfig(**docker_login_config)
    container_registry_auth_name = f'{cluster_name}-registry-auth'
    container_template_name = _construct_docker_login_template_name(
        cluster_name)
    # The `name` argument is only for display purpose and the registry server
    # will be splitted from the docker image name (Tested with AWS ECR).
    # Here we only need the username and password to create the registry auth.
    # TODO(tian): Now we create a template and a registry auth for each cluster.
    # Consider create one for each server and reuse them. Challenges including
    # calculate the reference count and delete them when no longer needed.
    create_auth_resp = runpod.runpod.create_container_registry_auth(
        name=container_registry_auth_name,
        username=login_config.username,
        password=login_config.password,
    )
    registry_auth_id = create_auth_resp['id']
    create_template_resp = runpod.runpod.create_template(
        name=container_template_name,
        image_name=None,
        registry_auth_id=registry_auth_id,
    )
    return login_config.format_image(image_name), create_template_resp['id']


def launch(cluster_name: str, node_type: str, instance_type: str, region: str,
           disk_size: int, image_name: str, ports: Optional[List[int]],
           public_key: str, preemptible: Optional[bool], bid_per_gpu: float,
           docker_login_config: Optional[Dict[str, str]]) -> str:
    """Launches an instance with the given parameters.

    Converts the instance_type to the RunPod GPU name, finds the specs for the
    GPU, and launches the instance.

    Returns:
        instance_id: The instance ID.
    """
    name = f'{cluster_name}-{node_type}'
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

    docker_args = (f'bash -c \'echo {encoded} | base64 --decode > init.sh; '
                   f'bash init.sh\'')

    # Port 8081 is occupied for nginx in the base image.
    custom_ports_str = ''
    if ports is not None:
        custom_ports_str = ''.join([f'{p}/tcp,' for p in ports])
    ports_str = (f'22/tcp,'
                 f'{custom_ports_str}'
                 f'{constants.SKY_REMOTE_RAY_DASHBOARD_PORT}/http,'
                 f'{constants.SKY_REMOTE_RAY_PORT}/http')

    image_name_formatted, template_id = _create_template_for_docker_login(
        cluster_name, image_name, docker_login_config)

    params = {
        'name': name,
        'image_name': image_name_formatted,
        'gpu_type_id': gpu_type,
        'cloud_type': cloud_type,
        'container_disk_in_gb': disk_size,
        'min_vcpu_count': 4 * gpu_quantity,
        'min_memory_in_gb': gpu_specs['memoryInGb'] * gpu_quantity,
        'gpu_count': gpu_quantity,
        'country_code': region,
        'ports': ports_str,
        'support_public_ip': True,
        'docker_args': docker_args,
        'template_id': template_id,
    }

    if preemptible is None or not preemptible:
        new_instance = runpod.runpod.create_pod(**params)
    else:
        new_instance = runpod_commands.create_spot_pod(
            bid_per_gpu=bid_per_gpu,
            **params,
        )

    return new_instance['id']


def get_registry_auth_resources(
        cluster_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Gets the registry auth resources."""
    container_registry_auth_name = _construct_docker_login_template_name(
        cluster_name)
    for template in _list_pod_templates_with_container_registry():
        if template['name'] == container_registry_auth_name:
            return container_registry_auth_name, template[
                'containerRegistryAuthId']
    return None, None


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
