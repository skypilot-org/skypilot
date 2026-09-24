"""RunPod library wrapper for SkyPilot.

Pods, templates and registry credentials are managed through the RunPod REST
API v2 (see ``sky.adaptors.runpod``). Spot pods have no v2 endpoint yet and
still go through the GraphQL API (``sky.provision.runpod.api``).
"""

import base64
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.adaptors import runpod
from sky.provision import docker_utils
from sky.provision.runpod.api import commands as runpod_commands
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

GPU_NAME_MAP = {
    # AMD
    'MI300X': 'AMD Instinct MI300X OAM',

    # NVIDIA A-series
    'A100-80GB': 'NVIDIA A100 80GB PCIe',
    'A100-80GB-SXM': 'NVIDIA A100-SXM4-80GB',
    'A30': 'NVIDIA A30',
    'A40': 'NVIDIA A40',

    # NVIDIA B-series
    'B200': 'NVIDIA B200',

    # GeForce
    'RTX3070': 'NVIDIA GeForce RTX 3070',
    'RTX3080': 'NVIDIA GeForce RTX 3080',
    'RTX3080Ti': 'NVIDIA GeForce RTX 3080 Ti',
    'RTX3090': 'NVIDIA GeForce RTX 3090',
    'RTX3090Ti': 'NVIDIA GeForce RTX 3090 Ti',
    'RTX4070Ti': 'NVIDIA GeForce RTX 4070 Ti',
    'RTX4080': 'NVIDIA GeForce RTX 4080',
    'RTX4080SUPER': 'NVIDIA GeForce RTX 4080 SUPER',
    'RTX4090': 'NVIDIA GeForce RTX 4090',
    'RTX5080': 'NVIDIA GeForce RTX 5080',
    'RTX5090': 'NVIDIA GeForce RTX 5090',

    # NVIDIA H100/H200
    # Following instance is displayed as SXM at the console
    # but the ID from the API appears as HBM
    'H100-SXM': 'NVIDIA H100 80GB HBM3',
    'H100-NVL': 'NVIDIA H100 NVL',
    'H100': 'NVIDIA H100 PCIe',
    'H200-SXM': 'NVIDIA H200',

    # NVIDIA L-series
    'L4': 'NVIDIA L4',
    'L40': 'NVIDIA L40',
    'L40S': 'NVIDIA L40S',

    # Ada generation (GeForce & RTX A)
    'RTX2000-Ada': 'NVIDIA RTX 2000 Ada Generation',
    'RTX4000-Ada': 'NVIDIA RTX 4000 Ada Generation',
    'RTX4000-Ada-SFF': 'NVIDIA RTX 4000 SFF Ada Generation',
    'RTX5000-Ada': 'NVIDIA RTX 5000 Ada Generation',
    'RTX6000-Ada': 'NVIDIA RTX 6000 Ada Generation',

    # NVIDIA RTX A-series
    'RTXA2000': 'NVIDIA RTX A2000',
    'RTXA4000': 'NVIDIA RTX A4000',
    'RTXA4500': 'NVIDIA RTX A4500',
    'RTXA5000': 'NVIDIA RTX A5000',
    'RTXA6000': 'NVIDIA RTX A6000',

    # NVIDIA RTX PRO (Blackwell)
    'RTXPRO4500': 'NVIDIA RTX PRO 4500 Blackwell',
    'RTXPRO6000': 'NVIDIA RTX PRO 6000 Blackwell Server Edition',
    'RTXPRO6000-WK': 'NVIDIA RTX PRO 6000 Blackwell Workstation Edition',

    # Tesla V100 variants
    'V100-16GB-FHHL': 'Tesla V100-FHHL-16GB',
    'V100-16GB-SXM2': 'Tesla V100-SXM2-16GB',
    'V100-32GB-SXM2': 'Tesla V100-SXM2-32GB',
    'V100-16GB-PCIe': 'Tesla V100-PCIE-16GB',
}


def _construct_docker_login_template_name(cluster_name: str) -> str:
    """Constructs the registry auth template name."""
    return f'{cluster_name}-docker-login-template'


def _construct_registry_auth_name(cluster_name: str) -> str:
    return f'{cluster_name}-registry-auth'


def _list_pods() -> List[Dict[str, Any]]:
    return runpod.rest_list('/pods', 'pods')


def _list_templates() -> List[Dict[str, Any]]:
    return runpod.rest_list('/templates', 'templates')


def _parse_pod(pod: Dict[str, Any]) -> Dict[str, Any]:
    """Converts a v2 pod object into the provisioner's instance info."""
    info: Dict[str, Any] = {
        'status': pod['status'],
        'name': pod['name'],
        'vcpu_count': None,
        'port2endpoint': {},
    }
    compute = pod.get('gpu') or pod.get('cpu') or {}
    info['vcpu_count'] = compute.get('vcpuCount')

    if pod['status'] != 'RUNNING':
        return info
    # RunPod only publishes the port mappings once the pod is RUNNING, and
    # even then the ports may take a while to be assigned.
    runtime = pod.get('runtime') or {}
    for port in runtime.get('ports') or []:
        if (port.get('type') != 'tcp' or port.get('ip') is None or
                port.get('public') is None):
            continue
        info['port2endpoint'][port['private']] = {
            'host': port['ip'],
            'port': port['public'],
        }
    ssh = (pod.get('ssh') or {}).get('direct')
    if ssh is None and 22 in info['port2endpoint']:
        ssh = info['port2endpoint'][22]
    if ssh is not None:
        info['external_ip'] = ssh['host']
        info['ssh_port'] = ssh['port']
    return info


def list_instances() -> Dict[str, Dict[str, Any]]:
    """Lists instances associated with API key."""
    return {pod['id']: _parse_pod(pod) for pod in _list_pods()}


def delete_pod_template(template_id: str) -> None:
    """Deletes a pod template."""
    try:
        runpod.rest_request('DELETE',
                            f'/templates/{runpod.path_segment(template_id)}')
    except runpod.RunPodRestError as e:
        logger.warning(f'Failed to delete template {template_id}: {e} '
                       'Please delete it manually.')


def delete_register_auth(registry_auth_id: str) -> None:
    """Deletes a registry auth."""
    try:
        runpod.rest_request(
            'DELETE', f'/registries/{runpod.path_segment(registry_auth_id)}')
    except runpod.RunPodRestError as e:
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
    # TODO(tian): Now we create a template and a registry auth for each cluster.
    # Consider create one for each server and reuse them. Challenges including
    # calculate the reference count and delete them when no longer needed.
    formatted_image = login_config.format_image(image_name)
    create_auth_resp = runpod.rest_request(
        'POST',
        '/registries',
        json={
            'name': _construct_registry_auth_name(cluster_name),
            'username': login_config.username,
            'password': login_config.password,
        })
    registry_auth_id = create_auth_resp['id']
    create_template_resp = runpod.rest_request(
        'POST',
        '/templates',
        json={
            'name': _construct_docker_login_template_name(cluster_name),
            'image': formatted_image,
            'registry': registry_auth_id,
            'startJupyter': False,
        })
    return formatted_image, create_template_resp['id']


def _get_gpu_memory_gb(gpu_type: str) -> int:
    gpu_specs = runpod.rest_request(
        'GET', f'/catalog/gpus/{runpod.path_segment(gpu_type)}')
    return gpu_specs['memory']


def _parse_cpu_instance_type(instance_type: str) -> Dict[str, Any]:
    """Splits a '<flavor>-<vcpus>-<memory>' CPU instance type."""
    flavor, vcpu_count, _ = instance_type.rsplit('-', 2)
    return {'id': flavor, 'vcpuCount': int(vcpu_count)}


def launch(
    cluster_name: str,
    node_type: str,
    instance_type: str,
    region: str,
    zone: str,
    disk_size: int,
    image_name: str,
    ports: Optional[List[int]],
    public_key: str,
    preemptible: Optional[bool],
    bid_per_gpu: float,
    docker_login_config: Optional[Dict[str, str]],
    *,
    network_volume_id: Optional[str] = None,
    volume_mount_path: Optional[str] = None,
) -> str:
    """Launches an instance with the given parameters.

    For CPU instances, we directly use the instance_type for launching the
    instance.

    For GPU instances, we convert the instance_type to the RunPod GPU name,
    and finds the specs for the GPU, before launching the instance.

    ``zone`` is a comma-separated list of RunPod data center ids.

    Returns:
        instance_id: The instance ID.
    """
    name = f'{cluster_name}-{node_type}'

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
        '$(prefix_cmd) export -p > ~/container_env_var.sh && '
        '$(prefix_cmd) '
        'mv ~/container_env_var.sh /etc/profile.d/container_env_var.sh; '
        '[ $(id -u) -eq 0 ] && echo alias sudo="" >> ~/.bashrc;sleep infinity')
    # Use base64 to deal with the tricky quoting issues caused by runpod API.
    encoded = base64.b64encode(setup_cmd.encode('utf-8')).decode('utf-8')

    docker_args = (f'bash -c \'echo {encoded} | base64 --decode > init.sh; '
                   f'bash init.sh\'')

    # Port 8081 is occupied for nginx in the base image.
    port_list = ['22/tcp']
    if ports is not None:
        port_list.extend(f'{p}/tcp' for p in ports)
    port_list.extend([
        f'{constants.SKY_REMOTE_RAY_DASHBOARD_PORT}/http',
        f'{constants.SKY_REMOTE_RAY_PORT}/http',
    ])

    image_name_formatted, template_id = _create_template_for_docker_login(
        cluster_name, image_name, docker_login_config)

    # GPU instance types start with f'{gpu_count}x',
    # CPU instance types start with 'cpu'.
    is_cpu_instance = instance_type.startswith('cpu')
    if not is_cpu_instance:
        gpu_type = GPU_NAME_MAP[instance_type.split('_')[1]]
        gpu_quantity = int(instance_type.split('_')[0].replace('x', ''))
        cloud_type = instance_type.split('_')[2]
        gpu_memory_gb = _get_gpu_memory_gb(gpu_type)

    if preemptible:
        if is_cpu_instance:
            raise ValueError('RunPod does not support spot CPU instances.')
        new_instance = runpod_commands.create_spot_pod(
            name=name,
            image_name=image_name_formatted,
            gpu_type_id=gpu_type,
            bid_per_gpu=bid_per_gpu,
            cloud_type=cloud_type,
            gpu_count=gpu_quantity,
            min_vcpu_count=4 * gpu_quantity,
            min_memory_in_gb=gpu_memory_gb * gpu_quantity,
            container_disk_in_gb=disk_size,
            country_code=region,
            data_center_id=zone,
            ports=','.join(port_list),
            docker_args=docker_args,
            template_id=template_id,
            network_volume_id=network_volume_id,
            volume_mount_path=(volume_mount_path if volume_mount_path
                               is not None else '/runpod-volume'),
        )
        return new_instance['id']

    body: Dict[str, Any] = {
        'name': name,
        'image': image_name_formatted,
        'disk': disk_size,
        'ports': port_list,
        'args': docker_args,
        'startSsh': True,
    }
    if template_id is not None:
        body['templateId'] = template_id
    if zone:
        body['dataCenterIds'] = zone.split(',')
    if network_volume_id is not None:
        if volume_mount_path is None:
            raise ValueError('volume_mount_path is required when mounting a '
                             'RunPod network volume.')
        body['mounts'] = {
            'network': [{
                'volumeId': network_volume_id,
                'path': volume_mount_path,
            }]
        }
    if is_cpu_instance:
        body['cpu'] = _parse_cpu_instance_type(instance_type)
    else:
        body['gpu'] = {
            'id': gpu_type,
            'count': gpu_quantity,
            'minVcpuCountPerGpu': 4,
            'minRamPerGpu': gpu_memory_gb,
        }
        # 'ALL' (any cloud) is expressed in v2 by omitting the field.
        if cloud_type in ('SECURE', 'COMMUNITY'):
            body['cloud'] = cloud_type
    new_instance = runpod.rest_request('POST', '/pods', json=body)
    return new_instance['id']


def get_registry_auth_resources(
        cluster_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Gets the docker login template and registry auth of a cluster.

    Returns:
        (template_id, registry_auth_id), both None if the cluster has no
        docker login template.
    """
    template_name = _construct_docker_login_template_name(cluster_name)
    for template in _list_templates():
        if template['name'] == template_name:
            return template['id'], template.get('registry')
    return None, None


def remove(instance_id: str) -> None:
    """Terminates the given instance."""
    runpod.rest_request('DELETE', f'/pods/{runpod.path_segment(instance_id)}')


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


def register_ssh_key(public_key: str) -> None:
    """Adds a public key to the account's SSH keys, unless already present.

    Keys are compared on type and material only, so a re-labeled copy of a
    registered key is not added twice.
    """
    resp = runpod.rest_request('GET', '/account/ssh-keys')
    current_keys: List[str] = list(resp.get('keys') or [])
    new_material = public_key.split()[:2]
    for key in current_keys:
        if key.split()[:2] == new_material:
            return
    runpod.rest_request('PUT',
                        '/account/ssh-keys',
                        json={'keys': current_keys + [public_key]})
