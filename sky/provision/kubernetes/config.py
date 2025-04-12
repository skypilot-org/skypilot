"""Kubernetes-specific configuration for the provisioner."""
import copy
import logging
import math
import os
import typing
from typing import Any, Dict, Optional, Union

from sky.adaptors import common as adaptors_common
from sky.adaptors import kubernetes
from sky.provision import common
from sky.provision.kubernetes import network_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import kubernetes_enums

if typing.TYPE_CHECKING:
    import yaml
else:
    yaml = adaptors_common.LazyImport('yaml')

logger = logging.getLogger(__name__)

# Timeout for deleting a Kubernetes resource (in seconds).
DELETION_TIMEOUT = 90


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    del region, cluster_name  # unused
    namespace = kubernetes_utils.get_namespace_from_config(
        config.provider_config)
    context = kubernetes_utils.get_context_from_config(config.provider_config)

    _configure_services(namespace, context, config.provider_config)

    networking_mode = network_utils.get_networking_mode(
        config.provider_config.get('networking_mode'))
    if networking_mode == kubernetes_enums.KubernetesNetworkingMode.NODEPORT:
        config = _configure_ssh_jump(namespace, context, config)

    requested_service_account = config.node_config['spec']['serviceAccountName']
    if (requested_service_account ==
            kubernetes_utils.DEFAULT_SERVICE_ACCOUNT_NAME):
        # If the user has requested a different service account (via pod_config
        # in ~/.sky/skyconfig.yaml), we assume they have already set up the
        # necessary roles and role bindings.
        # If not, set up the roles and bindings for skypilot-service-account
        # here.
        _configure_autoscaler_service_account(namespace, context,
                                              config.provider_config)
        _configure_autoscaler_role(namespace,
                                   context,
                                   config.provider_config,
                                   role_field='autoscaler_role')
        _configure_autoscaler_role_binding(
            namespace,
            context,
            config.provider_config,
            binding_field='autoscaler_role_binding')
        _configure_autoscaler_cluster_role(namespace, context,
                                           config.provider_config)
        _configure_autoscaler_cluster_role_binding(namespace, context,
                                                   config.provider_config)
        # SkyPilot system namespace is required for FUSE mounting. Here we just
        # create the namespace and set up the necessary permissions.
        #
        # We need to setup the namespace outside the
        # if config.provider_config.get('fuse_device_required') block below
        # because if we put in the if block, the following happens:
        # 1. User launches job controller on Kubernetes with SERVICE_ACCOUNT. No
        #    namespace is created at this point since the controller does not
        #    require FUSE.
        # 2. User submits a job requiring FUSE.
        # 3. The namespace is created here, but since the job controller is
        #    using DEFAULT_SERVICE_ACCOUNT_NAME, it does not have the necessary
        #    permissions to create a role for itself to create the FUSE manager.
        # 4. The job fails to launch.
        _configure_skypilot_system_namespace(config.provider_config)
        if config.provider_config.get('port_mode', 'loadbalancer') == 'ingress':
            logger.info('Port mode is set to ingress, setting up ingress role '
                        'and role binding.')
            try:
                _configure_autoscaler_role(namespace,
                                           context,
                                           config.provider_config,
                                           role_field='autoscaler_ingress_role')
                _configure_autoscaler_role_binding(
                    namespace,
                    context,
                    config.provider_config,
                    binding_field='autoscaler_ingress_role_binding')
            except kubernetes.api_exception() as e:
                # If namespace is not found, we will ignore the error
                if e.status == 404:
                    logger.info(
                        'Namespace not found - is your nginx ingress installed?'
                        ' Skipping ingress role and role binding setup.')
                else:
                    raise e

    elif requested_service_account != 'default':
        logger.info(f'Using service account {requested_service_account!r}, '
                    'skipping role and role binding setup.')
    if config.provider_config.get('fuse_device_required', False):
        _configure_fuse_mounting(config.provider_config)
    return config


class InvalidNamespaceError(ValueError):

    def __init__(self, field_name: str, namespace: str):
        super().__init__(
            f'Namespace of {field_name} config does not match provided '
            f'namespace "{namespace}". Either set it to {namespace} or remove '
            'the field')


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
        # cf.create_node().
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
    if not matching_keys:
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
        namespace: str, context: Optional[str],
        provider_config: Dict[str, Any]) -> None:
    account_field = 'autoscaler_service_account'
    if account_field not in provider_config:
        logger.info('_configure_autoscaler_service_account: '
                    f'{not_provided_msg(account_field)}')
        return

    account = provider_config[account_field]
    if 'namespace' not in account['metadata']:
        account['metadata']['namespace'] = namespace
    elif account['metadata']['namespace'] != namespace:
        raise InvalidNamespaceError(account_field, namespace)

    name = account['metadata']['name']
    field_selector = f'metadata.name={name}'
    accounts = (kubernetes.core_api(context).list_namespaced_service_account(
        namespace, field_selector=field_selector).items)
    if accounts:
        assert len(accounts) == 1
        # Nothing to check for equality and patch here,
        # since the service_account.metadata.name is the only important
        # attribute, which is already filtered for above.
        logger.info('_configure_autoscaler_service_account: '
                    f'{using_existing_msg(account_field, name)}')
        return

    logger.info('_configure_autoscaler_service_account: '
                f'{not_found_msg(account_field, name)}')
    kubernetes.core_api(context).create_namespaced_service_account(
        namespace, account)
    logger.info('_configure_autoscaler_service_account: '
                f'{created_msg(account_field, name)}')


def _configure_autoscaler_role(namespace: str, context: Optional[str],
                               provider_config: Dict[str, Any],
                               role_field: str) -> None:
    """ Reads the role from the provider config, creates if it does not exist.

    Args:
        namespace: The namespace to create the role in.
        provider_config: The provider config.
        role_field: The field in the provider config that contains the role.
    """

    if role_field not in provider_config:
        logger.info('_configure_autoscaler_role: '
                    f'{not_provided_msg(role_field)}')
        return

    role = provider_config[role_field]
    if 'namespace' not in role['metadata']:
        role['metadata']['namespace'] = namespace
    else:
        namespace = role['metadata']['namespace']

    name = role['metadata']['name']
    field_selector = f'metadata.name={name}'
    roles = (kubernetes.auth_api(context).list_namespaced_role(
        namespace, field_selector=field_selector).items)
    if roles:
        assert len(roles) == 1
        existing_role = roles[0]
        # Convert to k8s object to compare
        new_role = kubernetes_utils.dict_to_k8s_object(role, 'V1Role')
        if new_role.rules == existing_role.rules:
            logger.info('_configure_autoscaler_role: '
                        f'{using_existing_msg(role_field, name)}')
            return
        logger.info('_configure_autoscaler_role: '
                    f'{updating_existing_msg(role_field, name)}')
        kubernetes.auth_api(context).patch_namespaced_role(
            name, namespace, role)
        return

    logger.info('_configure_autoscaler_role: '
                f'{not_found_msg(role_field, name)}')
    kubernetes.auth_api(context).create_namespaced_role(namespace, role)
    logger.info(f'_configure_autoscaler_role: {created_msg(role_field, name)}')


def _configure_autoscaler_role_binding(
        namespace: str,
        context: Optional[str],
        provider_config: Dict[str, Any],
        binding_field: str,
        override_name: Optional[str] = None,
        override_subject_namespace: Optional[str] = None) -> None:
    """ Reads the role binding from the config, creates if it does not exist.

    Args:
        namespace: The namespace to create the role binding in.
        provider_config: The provider config.
        binding_field: The field in the provider config that contains the role
    """

    if binding_field not in provider_config:
        logger.info('_configure_autoscaler_role_binding: '
                    f'{not_provided_msg(binding_field)}')
        return

    binding = provider_config[binding_field]
    if 'namespace' not in binding['metadata']:
        binding['metadata']['namespace'] = namespace
        rb_namespace = namespace
    else:
        rb_namespace = binding['metadata']['namespace']

    # If override_subject_namespace is provided, we will use that
    # namespace for the subject. Otherwise, we will raise an error.
    subject_namespace = override_subject_namespace or namespace
    for subject in binding['subjects']:
        if 'namespace' not in subject:
            subject['namespace'] = subject_namespace
        elif subject['namespace'] != subject_namespace:
            subject_name = subject['name']
            raise InvalidNamespaceError(
                binding_field + f' subject {subject_name}', namespace)

    # Override name if provided
    binding['metadata']['name'] = override_name or binding['metadata']['name']
    name = binding['metadata']['name']

    field_selector = f'metadata.name={name}'
    role_bindings = (kubernetes.auth_api(context).list_namespaced_role_binding(
        rb_namespace, field_selector=field_selector).items)
    if role_bindings:
        assert len(role_bindings) == 1
        existing_binding = role_bindings[0]
        new_rb = kubernetes_utils.dict_to_k8s_object(binding, 'V1RoleBinding')
        if (new_rb.role_ref == existing_binding.role_ref and
                new_rb.subjects == existing_binding.subjects):
            logger.info('_configure_autoscaler_role_binding: '
                        f'{using_existing_msg(binding_field, name)}')
            return
        logger.info('_configure_autoscaler_role_binding: '
                    f'{updating_existing_msg(binding_field, name)}')
        kubernetes.auth_api(context).patch_namespaced_role_binding(
            name, rb_namespace, binding)
        return

    logger.info('_configure_autoscaler_role_binding: '
                f'{not_found_msg(binding_field, name)}')
    kubernetes.auth_api(context).create_namespaced_role_binding(
        rb_namespace, binding)
    logger.info('_configure_autoscaler_role_binding: '
                f'{created_msg(binding_field, name)}')


def _configure_autoscaler_cluster_role(namespace, context,
                                       provider_config: Dict[str, Any]) -> None:
    role_field = 'autoscaler_cluster_role'
    if role_field not in provider_config:
        logger.info('_configure_autoscaler_cluster_role: '
                    f'{not_provided_msg(role_field)}')
        return

    role = provider_config[role_field]
    if 'namespace' not in role['metadata']:
        role['metadata']['namespace'] = namespace
    elif role['metadata']['namespace'] != namespace:
        raise InvalidNamespaceError(role_field, namespace)

    name = role['metadata']['name']
    field_selector = f'metadata.name={name}'
    cluster_roles = (kubernetes.auth_api(context).list_cluster_role(
        field_selector=field_selector).items)
    if cluster_roles:
        assert len(cluster_roles) == 1
        existing_cr = cluster_roles[0]
        new_cr = kubernetes_utils.dict_to_k8s_object(role, 'V1ClusterRole')
        if new_cr.rules == existing_cr.rules:
            logger.info('_configure_autoscaler_cluster_role: '
                        f'{using_existing_msg(role_field, name)}')
            return
        logger.info('_configure_autoscaler_cluster_role: '
                    f'{updating_existing_msg(role_field, name)}')
        kubernetes.auth_api(context).patch_cluster_role(name, role)
        return

    logger.info('_configure_autoscaler_cluster_role: '
                f'{not_found_msg(role_field, name)}')
    kubernetes.auth_api(context).create_cluster_role(role)
    logger.info(
        f'_configure_autoscaler_cluster_role: {created_msg(role_field, name)}')


def _configure_autoscaler_cluster_role_binding(
        namespace, context, provider_config: Dict[str, Any]) -> None:
    binding_field = 'autoscaler_cluster_role_binding'
    if binding_field not in provider_config:
        logger.info('_configure_autoscaler_cluster_role_binding: '
                    f'{not_provided_msg(binding_field)}')
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
    cr_bindings = (kubernetes.auth_api(context).list_cluster_role_binding(
        field_selector=field_selector).items)
    if cr_bindings:
        assert len(cr_bindings) == 1
        existing_binding = cr_bindings[0]
        new_binding = kubernetes_utils.dict_to_k8s_object(
            binding, 'V1ClusterRoleBinding')
        if (new_binding.role_ref == existing_binding.role_ref and
                new_binding.subjects == existing_binding.subjects):
            logger.info('_configure_autoscaler_cluster_role_binding: '
                        f'{using_existing_msg(binding_field, name)}')
            return
        logger.info('_configure_autoscaler_cluster_role_binding: '
                    f'{updating_existing_msg(binding_field, name)}')
        kubernetes.auth_api(context).patch_cluster_role_binding(name, binding)
        return

    logger.info('_configure_autoscaler_cluster_role_binding: '
                f'{not_found_msg(binding_field, name)}')
    kubernetes.auth_api(context).create_cluster_role_binding(binding)
    logger.info('_configure_autoscaler_cluster_role_binding: '
                f'{created_msg(binding_field, name)}')


def _configure_ssh_jump(namespace, context, config: common.ProvisionConfig):
    """Creates a SSH jump pod to connect to the cluster.

    Also updates config['auth']['ssh_proxy_command'] to use the newly created
    jump pod.
    """
    provider_config = config.provider_config
    pod_cfg = config.node_config

    ssh_jump_name = pod_cfg['metadata']['labels']['skypilot-ssh-jump']
    ssh_jump_image = provider_config['ssh_jump_image']

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
                                        ssh_key_secret_name, namespace, context)
    return config


def _configure_skypilot_system_namespace(
        provider_config: Dict[str, Any]) -> None:
    """Creates the namespace for skypilot-system mounting if it does not exist.

    Also patches the SkyPilot service account to have the necessary permissions
    to manage resources in the namespace.
    """
    svc_account_namespace = provider_config['namespace']
    skypilot_system_namespace = provider_config['skypilot_system_namespace']
    context = kubernetes_utils.get_context_from_config(provider_config)
    kubernetes_utils.create_namespace(skypilot_system_namespace, context)

    # Note - this must be run only after the service account has been
    # created in the cluster (in bootstrap_instances).
    # Create the role in the skypilot-system namespace if it does not exist.
    _configure_autoscaler_role(skypilot_system_namespace,
                               context,
                               provider_config,
                               role_field='autoscaler_skypilot_system_role')
    # We must create a unique role binding per-namespace that SkyPilot is
    # running in, so we override the name with a unique name identifying
    # the namespace. This is required for multi-tenant setups where
    # different SkyPilot instances may be running in different namespaces.
    override_name = provider_config['autoscaler_skypilot_system_role_binding'][
        'metadata']['name'] + '-' + svc_account_namespace

    # Create the role binding in the skypilot-system namespace, and have
    # the subject namespace be the namespace that the SkyPilot service
    # account is created in.
    _configure_autoscaler_role_binding(
        skypilot_system_namespace,
        context,
        provider_config,
        binding_field='autoscaler_skypilot_system_role_binding',
        override_name=override_name,
        override_subject_namespace=svc_account_namespace)


def _configure_fuse_mounting(provider_config: Dict[str, Any]) -> None:
    """Creates the privileged daemonset required for FUSE mounting.

    FUSE mounting in Kubernetes without privileged containers requires us to
    run a privileged daemonset which accepts fusermount requests via unix
    domain socket and perform the mount/unmount operations on the host /dev/fuse
    device.

    We create this daemonset in the skypilot_system_namespace, which is
    configurable in the provider config. This allows the daemonset to be
    shared across multiple tenants. The default namespace is
    'skypilot-system' (populated in clouds.Kubernetes).

    For legacy smarter-device-manager daemonset, we keep it as is since it may
    still be used by other tenants.
    """

    logger.info(
        '_configure_fuse_mounting: Setting up fusermount-server daemonset.')

    fuse_proxy_namespace = provider_config['skypilot_system_namespace']
    context = kubernetes_utils.get_context_from_config(provider_config)

    # Read the YAMLs from the manifests directory
    root_dir = os.path.dirname(os.path.dirname(__file__))

    # Load and create the DaemonSet
    # TODO(aylei): support customize and upgrade the fusermount-server image
    logger.info('_configure_fuse_mounting: Creating daemonset.')
    daemonset_path = os.path.join(
        root_dir, 'kubernetes/manifests/fusermount-server-daemonset.yaml')
    with open(daemonset_path, 'r', encoding='utf-8') as file:
        daemonset = yaml.safe_load(file)
    kubernetes_utils.merge_custom_metadata(daemonset['metadata'])
    try:
        kubernetes.apps_api(context).create_namespaced_daemon_set(
            fuse_proxy_namespace, daemonset)
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.info('_configure_fuse_mounting: DaemonSet already exists '
                        f'in namespace {fuse_proxy_namespace!r}')
            existing_ds = kubernetes.apps_api(
                context).read_namespaced_daemon_set(
                    daemonset['metadata']['name'], fuse_proxy_namespace)
            ds_image = daemonset['spec']['template']['spec']['containers'][0][
                'image']
            if existing_ds.spec.template.spec.containers[0].image != ds_image:
                logger.info(
                    '_configure_fuse_mounting: Updating DaemonSet image.')
                kubernetes.apps_api(context).patch_namespaced_daemon_set(
                    daemonset['metadata']['name'], fuse_proxy_namespace,
                    daemonset)
        elif e.status == 403 or e.status == 401:
            logger.error('SkyPilot does not have permission to create '
                         'fusermount-server DaemonSet in namespace '
                         f'{fuse_proxy_namespace!r}, Error: {e.reason}. '
                         'Please check the permissions of the SkyPilot service '
                         'account or contact your cluster admin to create the '
                         'DaemonSet manually. '
                         'Reference: https://docs.skypilot.co/reference/kubernetes/kubernetes-setup.html#kubernetes-setup-fuse')  # pylint: disable=line-too-long
            raise
        else:
            raise
    else:
        logger.info('_configure_fuse_mounting: DaemonSet created '
                    f'in namespace {fuse_proxy_namespace!r}')

    logger.info('fusermount-server daemonset setup complete '
                f'in namespace {fuse_proxy_namespace!r}')


def _configure_services(namespace: str, context: Optional[str],
                        provider_config: Dict[str, Any]) -> None:
    service_field = 'services'
    if service_field not in provider_config:
        logger.info(f'_configure_services: {not_provided_msg(service_field)}')
        return

    services = provider_config[service_field]
    for service in services:
        if 'namespace' not in service['metadata']:
            service['metadata']['namespace'] = namespace
        elif service['metadata']['namespace'] != namespace:
            raise InvalidNamespaceError(service_field, namespace)

        name = service['metadata']['name']
        field_selector = f'metadata.name={name}'
        services = (kubernetes.core_api(context).list_namespaced_service(
            namespace, field_selector=field_selector).items)
        if services:
            assert len(services) == 1
            existing_service = services[0]
            # Convert to k8s object to compare
            new_svc = kubernetes_utils.dict_to_k8s_object(service, 'V1Service')
            if new_svc.spec.ports == existing_service.spec.ports:
                logger.info('_configure_services: '
                            f'{using_existing_msg("service", name)}')
                return
            else:
                logger.info('_configure_services: '
                            f'{updating_existing_msg("service", name)}')
                kubernetes.core_api(context).patch_namespaced_service(
                    name, namespace, service)
        else:
            logger.info(
                f'_configure_services: {not_found_msg("service", name)}')
            kubernetes.core_api(context).create_namespaced_service(
                namespace, service)
            logger.info(f'_configure_services: {created_msg("service", name)}')


class KubernetesError(Exception):
    pass
