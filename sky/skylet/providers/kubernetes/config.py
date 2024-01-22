import copy
import logging
import math
from typing import Any, Dict, Union

from sky.adaptors import kubernetes
from sky.utils import kubernetes_utils

logger = logging.getLogger(__name__)

log_prefix = 'KubernetesNodeProvider: '

# Timeout for deleting a Kubernetes resource (in seconds).
DELETION_TIMEOUT = 90


class InvalidNamespaceError(ValueError):

    def __init__(self, field_name: str, namespace: str):
        self.message = (
            f'Namespace of {field_name} config does not match provided '
            f'namespace "{namespace}". Either set it to {namespace} or remove the '
            'field')

    def __str__(self) -> str:
        return self.message


def using_existing_msg(resource_type: str, name: str) -> str:
    return f'using existing {resource_type} "{name}"'


def updating_existing_msg(resource_type: str, name: str) -> str:
    return f'updating existing {resource_type} "{name}"'


def not_found_msg(resource_type: str, name: str) -> str:
    return f'{resource_type} "{name}" not found, attempting to create it'


def not_checking_msg(resource_type: str, name: str) -> str:
    return f'not checking if {resource_type} "{name}" exists'


def created_msg(resource_type: str, name: str) -> str:
    return f'successfully created {resource_type} "{name}"'


def not_provided_msg(resource_type: str) -> str:
    return f'no {resource_type} config provided, must already exist'


def bootstrap_kubernetes(config: Dict[str, Any]) -> Dict[str, Any]:
    namespace = kubernetes_utils.get_current_kube_config_context_namespace()

    _configure_services(namespace, config['provider'])

    config = _configure_ssh_jump(namespace, config)

    if not config['provider'].get('_operator'):
        # These steps are unecessary when using the Operator.
        _configure_autoscaler_service_account(namespace, config['provider'])
        _configure_autoscaler_role(namespace, config['provider'])
        _configure_autoscaler_role_binding(namespace, config['provider'])

    return config


def fillout_resources_kubernetes(config: Dict[str, Any]) -> Dict[str, Any]:
    """Fills CPU and GPU resources in the ray cluster config.

    For each node type and each of CPU/GPU, looks at container's resources
    and limits, takes min of the two.
    """
    if 'available_node_types' not in config:
        return config
    node_types = copy.deepcopy(config['available_node_types'])
    head_node_type = config['head_node_type']
    for node_type in node_types:

        node_config = node_types[node_type]['node_config']
        # The next line is for compatibility with configs which define pod specs
        # cf. KubernetesNodeProvider.create_node().
        pod = node_config.get('pod', node_config)
        container_data = pod['spec']['containers'][0]

        autodetected_resources = get_autodetected_resources(container_data)
        if node_types == head_node_type:
            # we only autodetect worker type node memory resource
            autodetected_resources.pop('memory')
        if 'resources' not in config['available_node_types'][node_type]:
            config['available_node_types'][node_type]['resources'] = {}
        autodetected_resources.update(
            config['available_node_types'][node_type]['resources'])
        config['available_node_types'][node_type][
            'resources'] = autodetected_resources
        logger.debug(f'Updating the resources of node type {node_type} '
                     f'to include {autodetected_resources}.')
    return config


def get_autodetected_resources(
        container_data: Dict[str, Any]) -> Dict[str, Any]:
    container_resources = container_data.get('resources', None)
    if container_resources is None:
        return {'CPU': 0, 'GPU': 0}

    node_type_resources = {
        resource_name.upper(): get_resource(container_resources, resource_name)
        for resource_name in ['cpu', 'gpu']
    }

    memory_limits = get_resource(container_resources, 'memory')
    node_type_resources['memory'] = memory_limits

    return node_type_resources


def get_resource(container_resources: Dict[str, Any],
                 resource_name: str) -> int:
    request = _get_resource(container_resources,
                            resource_name,
                            field_name='requests')
    limit = _get_resource(container_resources,
                          resource_name,
                          field_name='limits')
    # Use request if limit is not set, else use limit.
    # float('inf') means there's no limit set
    res_count = request if limit == float('inf') else limit
    # Convert to int since Ray autoscaler expects int.
    # We also round up the resource count to the nearest integer to provide the
    # user at least the amount of resource they requested.
    rounded_count = math.ceil(res_count)
    if resource_name == 'cpu':
        # For CPU, we set minimum count to 1 because if CPU count is set to 0,
        # (e.g. when the user sets --cpu 0.5), ray will not be able to schedule
        # any tasks.
        return max(1, rounded_count)
    else:
        # For GPU and memory, return the rounded count.
        return rounded_count


def _get_resource(container_resources: Dict[str, Any], resource_name: str,
                  field_name: str) -> Union[int, float]:
    """Returns the resource quantity.

    The amount of resource is rounded up to nearest integer.
    Returns float("inf") if the resource is not present.

    Args:
        container_resources: Container's resource field.
        resource_name: One of 'cpu', 'gpu' or 'memory'.
        field_name: One of 'requests' or 'limits'.

    Returns:
        Union[int, float]: Detected resource quantity.
    """
    if field_name not in container_resources:
        # No limit/resource field.
        return float('inf')
    resources = container_resources[field_name]
    # Look for keys containing the resource_name. For example,
    # the key 'nvidia.com/gpu' contains the key 'gpu'.
    matching_keys = [key for key in resources if resource_name in key.lower()]
    if len(matching_keys) == 0:
        return float('inf')
    if len(matching_keys) > 1:
        # Should have only one match -- mostly relevant for gpu.
        raise ValueError(f'Multiple {resource_name} types not supported.')
    # E.g. 'nvidia.com/gpu' or 'cpu'.
    resource_key = matching_keys.pop()
    resource_quantity = resources[resource_key]
    if resource_name == 'memory':
        return kubernetes_utils.parse_memory_resource(resource_quantity)
    else:
        return kubernetes_utils.parse_cpu_or_gpu_resource(resource_quantity)


def _configure_autoscaler_service_account(
        namespace: str, provider_config: Dict[str, Any]) -> None:
    account_field = 'autoscaler_service_account'
    if account_field not in provider_config:
        logger.info(log_prefix + not_provided_msg(account_field))
        return

    account = provider_config[account_field]
    if 'namespace' not in account['metadata']:
        account['metadata']['namespace'] = namespace
    elif account['metadata']['namespace'] != namespace:
        raise InvalidNamespaceError(account_field, namespace)

    name = account['metadata']['name']
    field_selector = f'metadata.name={name}'
    accounts = (kubernetes.core_api().list_namespaced_service_account(
        namespace, field_selector=field_selector).items)
    if len(accounts) > 0:
        assert len(accounts) == 1
        logger.info(log_prefix + using_existing_msg(account_field, name))
        return

    logger.info(log_prefix + not_found_msg(account_field, name))
    kubernetes.core_api().create_namespaced_service_account(namespace, account)
    logger.info(log_prefix + created_msg(account_field, name))


def _configure_autoscaler_role(namespace: str,
                               provider_config: Dict[str, Any]) -> None:
    role_field = 'autoscaler_role'
    if role_field not in provider_config:
        logger.info(log_prefix + not_provided_msg(role_field))
        return

    role = provider_config[role_field]
    if 'namespace' not in role['metadata']:
        role['metadata']['namespace'] = namespace
    elif role['metadata']['namespace'] != namespace:
        raise InvalidNamespaceError(role_field, namespace)

    name = role['metadata']['name']
    field_selector = f'metadata.name={name}'
    accounts = (kubernetes.auth_api().list_namespaced_role(
        namespace, field_selector=field_selector).items)
    if len(accounts) > 0:
        assert len(accounts) == 1
        logger.info(log_prefix + using_existing_msg(role_field, name))
        return

    logger.info(log_prefix + not_found_msg(role_field, name))
    kubernetes.auth_api().create_namespaced_role(namespace, role)
    logger.info(log_prefix + created_msg(role_field, name))


def _configure_autoscaler_role_binding(namespace: str,
                                       provider_config: Dict[str, Any]) -> None:
    binding_field = 'autoscaler_role_binding'
    if binding_field not in provider_config:
        logger.info(log_prefix + not_provided_msg(binding_field))
        return

    binding = provider_config[binding_field]
    if 'namespace' not in binding['metadata']:
        binding['metadata']['namespace'] = namespace
    elif binding['metadata']['namespace'] != namespace:
        raise InvalidNamespaceError(binding_field, namespace)
    for subject in binding['subjects']:
        if 'namespace' not in subject:
            subject['namespace'] = namespace
        elif subject['namespace'] != namespace:
            subject_name = subject['name']
            raise InvalidNamespaceError(
                binding_field + f' subject {subject_name}', namespace)

    name = binding['metadata']['name']
    field_selector = f'metadata.name={name}'
    accounts = (kubernetes.auth_api().list_namespaced_role_binding(
        namespace, field_selector=field_selector).items)
    if len(accounts) > 0:
        assert len(accounts) == 1
        logger.info(log_prefix + using_existing_msg(binding_field, name))
        return

    logger.info(log_prefix + not_found_msg(binding_field, name))
    kubernetes.auth_api().create_namespaced_role_binding(namespace, binding)
    logger.info(log_prefix + created_msg(binding_field, name))


def _configure_ssh_jump(namespace, config):
    """Creates a SSH jump pod to connect to the cluster.

    Also updates config['auth']['ssh_proxy_command'] to use the newly created
    jump pod.
    """
    pod_cfg = config['available_node_types']['ray_head_default']['node_config']

    ssh_jump_name = pod_cfg['metadata']['labels']['skypilot-ssh-jump']
    ssh_jump_image = config['provider']['ssh_jump_image']

    volumes = pod_cfg['spec']['volumes']
    # find 'secret-volume' and get the secret name
    secret_volume = next(filter(lambda x: x['name'] == 'secret-volume',
                                volumes))
    ssh_key_secret_name = secret_volume['secret']['secretName']

    # TODO(romilb): We currently split SSH jump pod and svc creation. Service
    #  is first created in authentication.py::setup_kubernetes_authentication
    #  and then SSH jump pod creation happens here. This is because we need to
    #  set the ssh_proxy_command in the ray YAML before we pass it to the
    #  autoscaler. If in the future if we can write the ssh_proxy_command to the
    #  cluster yaml through this method, then we should move the service
    #  creation here.

    # TODO(romilb): We should add a check here to make sure the service is up
    #  and available before we create the SSH jump pod. If for any reason the
    #  service is missing, we should raise an error.

    kubernetes_utils.setup_ssh_jump_pod(ssh_jump_name, ssh_jump_image,
                                        ssh_key_secret_name, namespace)
    return config


def _configure_services(namespace: str, provider_config: Dict[str,
                                                              Any]) -> None:
    service_field = 'services'
    if service_field not in provider_config:
        logger.info(log_prefix + not_provided_msg(service_field))
        return

    services = provider_config[service_field]
    for service in services:
        if 'namespace' not in service['metadata']:
            service['metadata']['namespace'] = namespace
        elif service['metadata']['namespace'] != namespace:
            raise InvalidNamespaceError(service_field, namespace)

        name = service['metadata']['name']
        field_selector = f'metadata.name={name}'
        services = (kubernetes.core_api().list_namespaced_service(
            namespace, field_selector=field_selector).items)
        if len(services) > 0:
            assert len(services) == 1
            existing_service = services[0]
            if service == existing_service:
                logger.info(log_prefix + using_existing_msg('service', name))
                return
            else:
                logger.info(log_prefix + updating_existing_msg('service', name))
                kubernetes.core_api().patch_namespaced_service(
                    name, namespace, service)
        else:
            logger.info(log_prefix + not_found_msg('service', name))
            kubernetes.core_api().create_namespaced_service(namespace, service)
            logger.info(log_prefix + created_msg('service', name))


class KubernetesError(Exception):
    pass
