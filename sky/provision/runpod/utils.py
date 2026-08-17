"""RunPod library wrapper for SkyPilot."""

import base64
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

from sky import sky_logging
from sky.adaptors import runpod
from sky.provision import constants as provision_constants
from sky.provision import docker_utils
from sky.provision.runpod.api import commands as runpod_commands
from sky.utils import common_utils
from sky.utils import resources_utils

logger = sky_logging.init_logger(__name__)

_REST_API_BASE_URL = 'https://rest.runpod.io/v1'
_REST_API_TIMEOUT_SECONDS = 120

_rest_data_center_ids: Optional[set] = None


def _rest_launchable_data_center_ids() -> Optional[set]:
    """Data center ids accepted by the REST create-pod API, or None if unknown.

    The REST enum is a strict subset of both GraphQL's dataCenters list and
    the catalog's region/zone table. One unknown id rejects the whole create
    request with a 400, so ids must be filtered against this list first.
    """
    global _rest_data_center_ids
    if _rest_data_center_ids is not None:
        return _rest_data_center_ids
    try:
        response = requests.get(f'{_REST_API_BASE_URL}/openapi.json',
                                timeout=_REST_API_TIMEOUT_SECONDS)
        response.raise_for_status()
        properties = (response.json()['components']['schemas']['PodCreateInput']
                      ['properties'])
        _rest_data_center_ids = set(
            properties['dataCenterIds']['items']['enum'])
    except (requests.RequestException, KeyError, TypeError, ValueError) as e:
        logger.warning('Could not fetch the RunPod REST data center list; '
                       f'sending zone ids unfiltered: {e}')
        return None
    return _rest_data_center_ids


_GPU_AVAILABILITY_TTL_SECONDS = 20
_gpu_availability_cache: Dict[Tuple[str, int, bool], Tuple[float, set]] = {}


def _available_data_center_ids(gpu_type_id: str, gpu_count: int,
                               secure_cloud: bool) -> Optional[set]:
    """Data centers currently reporting stock for the GPU, or None if unknown.

    Advisory pre-check so region attempts skip data centers with no stock
    instead of paying a create-and-fail round trip. Marketplace stock moves
    fast, so results are cached only briefly, and any query failure falls
    back to attempting the create anyway.
    """
    cache_key = (gpu_type_id, gpu_count, secure_cloud)
    cached = _gpu_availability_cache.get(cache_key)
    if cached is not None and (time.time() - cached[0] <
                               _GPU_AVAILABILITY_TTL_SECONDS):
        return cached[1]
    secure_literal = 'true' if secure_cloud else 'false'
    query = ('query { dataCenters { id gpuAvailability(input: '
             f'{{gpuCount: {gpu_count}, secureCloud: {secure_literal}}}) '
             '{ gpuTypeId available } } }')
    try:
        _ensure_api_key_configured()
        result = runpod.runpod.api.graphql.run_graphql_query(query)
        data_centers = result['data']['dataCenters']
    except Exception as e:  # pylint: disable=broad-except
        logger.warning('Could not query RunPod GPU availability; '
                       f'attempting creation without the pre-check: {e}')
        return None
    available = {
        data_center['id']
        for data_center in data_centers
        for entry in (data_center.get('gpuAvailability') or [])
        if entry.get('gpuTypeId') == gpu_type_id and entry.get('available')
    }
    _gpu_availability_cache[cache_key] = (time.time(), available)
    return available


def available_data_center_ids_for_instance_type(
        instance_type: str) -> Optional[set]:
    """Return live RunPod zones for a catalog instance type.

    ``None`` means that the availability API could not be queried and the
    catalog should remain usable as a fallback.  An empty set is authoritative
    and means that the instance type currently has no capacity anywhere.
    """
    if instance_type.startswith('cpu'):
        return None
    try:
        count_text, gpu_name, cloud_type = instance_type.split('_')
        gpu_count = int(
            count_text[:-1] if count_text.endswith('x') else count_text)
        gpu_type_id = GPU_NAME_MAP[gpu_name]
    except (KeyError, TypeError, ValueError):
        logger.debug('Could not derive live availability query from %s',
                     instance_type)
        return None
    return _available_data_center_ids(gpu_type_id, gpu_count,
                                      cloud_type == 'SECURE')


def _ensure_api_key_configured() -> None:
    """Load the default RunPod credential into the SDK when needed."""
    sdk = runpod.runpod.load_module()
    if getattr(sdk, 'api_key', None):
        return

    credential_file = os.path.expanduser('~/.runpod/config.toml')
    try:
        with open(credential_file, 'rb') as credential_stream:
            config = tomllib.load(credential_stream)
    except FileNotFoundError:
        return
    except (OSError, TypeError, ValueError) as error:
        logger.warning('Failed to load RunPod credentials: %s', error)
        return

    api_key = config.get('default', {}).get('api_key')
    if isinstance(api_key, str) and api_key:
        sdk.api_key = api_key


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


def retry(func):
    """Decorator to retry a function.

    Only retries on transient errors. Does not retry on authorization errors
    (Unauthorized, Forbidden) as these are not recoverable.
    """

    def wrapper(*args, **kwargs):
        """Wrapper for retrying a function."""
        cnt = 0
        while True:
            try:
                return func(*args, **kwargs)
            except runpod.runpod.error.QueryError as e:
                error_msg = str(e).lower()
                # Don't retry on authorization errors - these won't recover
                auth_keywords = ['unauthorized', 'forbidden', '401', '403']
                if any(keyword in error_msg for keyword in auth_keywords):
                    logger.error(f'RunPod authorization error (not retrying): '
                                 f'{common_utils.format_exception(e)}')
                    raise
                cnt += 1
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
    _ensure_api_key_configured()
    instances = _sky_get_pods()

    instance_dict: Dict[str, Dict[str, Any]] = {}
    for instance in instances:
        info = {}

        info['status'] = instance['desiredStatus']
        info['name'] = instance['name']
        info['vcpu_count'] = instance.get('vcpuCount')
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
    # Compute the fully-qualified image name (e.g. ghcr.io/org/image:tag)
    # before creating the template. Passing image_name=None caused Python to
    # serialize None as the literal string "None" in the GraphQL mutation
    # (imageName: "None"), which the RunPod API now rejects as invalid.
    # TODO(tian): Now we create a template and a registry auth for each cluster.
    # Consider create one for each server and reuse them. Challenges including
    # calculate the reference count and delete them when no longer needed.
    formatted_image = login_config.format_image(image_name)
    create_auth_resp = runpod.runpod.create_container_registry_auth(
        name=container_registry_auth_name,
        username=login_config.username,
        password=login_config.password,
    )
    registry_auth_id = create_auth_resp['id']
    create_template_resp = runpod.runpod.create_template(
        name=container_template_name,
        image_name=formatted_image,
        registry_auth_id=registry_auth_id,
    )
    return formatted_image, create_template_resp['id']


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
    network_tier: resources_utils.NetworkTier = (
        resources_utils.NetworkTier.STANDARD),
) -> str:
    """Launches an instance with the given parameters.

    For CPU instances, we directly use the instance_type for launching the
    instance.

    For GPU instances, we convert the instance_type to the RunPod GPU name,
    and finds the specs for the GPU, before launching the instance.

    Returns:
        instance_id: The instance ID.
    """
    sdk_version_error = runpod.get_sdk_version_error()
    if sdk_version_error is not None:
        raise RuntimeError(sdk_version_error)

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

    bootstrap_cmd = (f'echo {encoded} | base64 --decode > init.sh; '
                     'bash init.sh')
    # Spot and CPU pods are created through the GraphQL SDK, which can only
    # pass the bootstrap as the container CMD. It then runs through the
    # image's own ENTRYPOINT, so those images must keep a shell-compatible
    # entrypoint. On-demand GPU pods are created through the REST API below,
    # which overrides the entrypoint entirely.
    docker_args = f'bash -c \'{bootstrap_cmd}\''

    # Port 8081 is occupied for nginx in the base image.
    custom_ports_str = ''
    if ports is not None:
        custom_ports_str = ''.join([f'{p}/tcp,' for p in ports])
    # Only SSH and user-requested ports are published. The internal Ray
    # ports (6380 GCS, 8266 dashboard) are intentionally not exposed: the
    # dashboard binds to 127.0.0.1 and GCS speaks gRPC, so RunPod's HTTP
    # proxy can never serve them and only showed them as perpetually
    # "Initializing" in the console. All SkyPilot traffic uses the SSH
    # tunnel, and multi-node Ray connects through internal IPs from
    # get_cluster_info, so nothing consumes public mappings of those ports.
    ports_str = f'22/tcp,{custom_ports_str}'.rstrip(',')

    image_name_formatted, template_id = _create_template_for_docker_login(
        cluster_name, image_name, docker_login_config)

    params = {
        'name': name,
        'image_name': image_name_formatted,
        'container_disk_in_gb': disk_size,
        'country_code': region,
        'data_center_id': zone,
        'ports': ports_str,
        'support_public_ip': True,
        'docker_args': docker_args,
        'template_id': template_id,
    }

    if network_tier is resources_utils.NetworkTier.BEST:
        minimum_bandwidth = (
            provision_constants.MARKETPLACE_BEST_NETWORK_MIN_BANDWIDTH_MBPS)
        params['min_download'] = minimum_bandwidth
        params['min_upload'] = minimum_bandwidth

    # Optional network volume mount.
    if volume_mount_path is not None:
        params['volume_mount_path'] = volume_mount_path
    if network_volume_id is not None:
        params['network_volume_id'] = network_volume_id

    # GPU instance types start with f'{gpu_count}x',
    # CPU instance types start with 'cpu'.
    is_cpu_instance = instance_type.startswith('cpu')
    if is_cpu_instance:
        # RunPod CPU instances can be uniquely identified by the instance_id.
        params.update({
            'instance_id': instance_type,
        })
    else:
        gpu_type = GPU_NAME_MAP[instance_type.split('_')[1]]
        gpu_quantity = int(instance_type.split('_')[0].replace('x', ''))
        cloud_type = instance_type.split('_')[2]
        gpu_specs = runpod.runpod.get_gpu(gpu_type)
        params.update({
            'gpu_type_id': gpu_type,
            'cloud_type': cloud_type,
            'min_vcpu_count': 4 * gpu_quantity,
            'min_memory_in_gb': gpu_specs['memoryInGb'] * gpu_quantity,
            'gpu_count': gpu_quantity,
        })

    if preemptible is None or not preemptible:
        if is_cpu_instance:
            new_instance = runpod.runpod.create_pod(**params)
        else:
            new_instance = _create_pod_via_rest(
                _rest_pod_create_params(params, bootstrap_cmd))
    else:
        gpu_type_id = params.get('gpu_type_id')
        gpu_count = params.get('gpu_count')
        data_center_id = params.get('data_center_id')
        if (not is_cpu_instance and isinstance(gpu_type_id, str) and
                isinstance(gpu_count, int) and isinstance(data_center_id, str)):
            available = _available_data_center_ids(
                gpu_type_id, gpu_count, params['cloud_type'] == 'SECURE')
            if (available is not None and data_center_id not in available):
                raise RuntimeError(
                    f'No {gpu_type_id} capacity currently reported in data '
                    f'center {data_center_id}.')
        new_instance = runpod_commands.create_spot_pod(
            bid_per_gpu=bid_per_gpu,
            **params,  # type: ignore[arg-type]
        )

    return new_instance['id']


def _rest_pod_create_params(params: Dict[str, Any],
                            bootstrap_cmd: str) -> Dict[str, Any]:
    """Translate SDK create_pod kwargs into REST PodCreateInput fields.

    Only the REST API can override the image ENTRYPOINT via
    dockerEntrypoint/dockerStartCmd. The GraphQL API used by the runpod SDK
    passes dockerArgs through the image's own ENTRYPOINT, which breaks any
    image whose entrypoint is not a shell (e.g. a CLI entrypoint would
    receive `bash -c ...` as its arguments). Every other SkyPilot
    provisioner already bypasses image entrypoints; see
    `--entrypoint=/bin/bash` in sky/provision/docker_utils.py.
    """
    zone = params.get('data_center_id')
    region = params.get('country_code')
    gpu_count = params['gpu_count']
    rest_params: Dict[str, Any] = {
        'name': params['name'],
        'imageName': params['image_name'],
        'containerDiskInGb': params['container_disk_in_gb'],
        'ports': [port for port in params['ports'].split(',') if port],
        'supportPublicIp': params['support_public_ip'],
        'computeType': 'GPU',
        'cloudType': params['cloud_type'],
        'gpuTypeIds': [params['gpu_type_id']],
        'gpuCount': gpu_count,
        'minVCPUPerGPU': int(params['min_vcpu_count'] // gpu_count),
        'minRAMPerGPU': int(params['min_memory_in_gb'] // gpu_count),
        'dockerEntrypoint': ['bash', '-c'],
        'dockerStartCmd': [bootstrap_cmd],
    }
    if zone:
        zone_ids = zone.split(',')
        launchable = _rest_launchable_data_center_ids()
        if launchable is not None:
            supported_zone_ids = [z for z in zone_ids if z in launchable]
            dropped = sorted(set(zone_ids) - set(supported_zone_ids))
            if dropped:
                logger.debug('Dropping data center ids the RunPod REST API '
                             f'does not accept: {", ".join(dropped)}')
            if not supported_zone_ids:
                raise RuntimeError(
                    'RunPod REST API does not support launching in any of '
                    f'the requested data centers ({zone}).')
            zone_ids = supported_zone_ids
        available = _available_data_center_ids(params['gpu_type_id'], gpu_count,
                                               params['cloud_type'] == 'SECURE')
        if available is not None:
            stocked_zone_ids = [z for z in zone_ids if z in available]
            if not stocked_zone_ids:
                raise RuntimeError(
                    f'No {params["gpu_type_id"]} capacity currently '
                    f'reported in data center(s) {",".join(zone_ids)}.')
            zone_ids = stocked_zone_ids
        rest_params['dataCenterIds'] = zone_ids
    elif region:
        rest_params['countryCodes'] = [region]
    if params.get('template_id'):
        rest_params['templateId'] = params['template_id']
    if params.get('min_download'):
        rest_params['minDownloadMbps'] = params['min_download']
    if params.get('min_upload'):
        rest_params['minUploadMbps'] = params['min_upload']
    if params.get('network_volume_id'):
        rest_params['networkVolumeId'] = params['network_volume_id']
    if params.get('volume_mount_path'):
        rest_params['volumeMountPath'] = params['volume_mount_path']
    return rest_params


def _create_pod_via_rest(create_params: Dict[str, Any]) -> Dict[str, Any]:
    """Create an on-demand pod through RunPod's REST API."""
    _ensure_api_key_configured()
    api_key = getattr(runpod.runpod, 'api_key', None)
    response = requests.post(
        f'{_REST_API_BASE_URL}/pods',
        headers={'Authorization': f'Bearer {api_key}'},
        json=create_params,
        timeout=_REST_API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError('RunPod REST pod creation failed with status '
                           f'{response.status_code}.')
    return response.json()


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
