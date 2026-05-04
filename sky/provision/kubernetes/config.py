"""Kubernetes-specific configuration for the provisioner."""
from concurrent import futures
import copy
import hashlib
import json
import logging
import math
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from sky.adaptors import kubernetes
from sky.provision import common
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import yaml_utils
from sky.utils.db import kv_cache

logger = logging.getLogger(__name__)

# Timeout for deleting a Kubernetes resource (in seconds).
DELETION_TIMEOUT = 90

# How long an RBAC verification stays cached. Bounds staleness if an admin
# deletes a SkyPilot-managed role/binding outside of SkyPilot. Short enough
# that the next launch will re-verify, long enough to cover bursts of
# back-to-back launches.
_RBAC_CACHE_TTL_SECONDS = 5 * 60

# Fields in provider_config that influence the RBAC + system-namespace setup.
# A change in any of these invalidates the cache via the content hash.
# Cluster-specific fields (e.g. 'services') are deliberately excluded so
# launching a new cluster name doesn't force a cache miss.
_RBAC_FINGERPRINT_FIELDS: Tuple[str, ...] = (
    'autoscaler_service_account',
    'autoscaler_role',
    'autoscaler_role_binding',
    'autoscaler_cluster_role',
    'autoscaler_cluster_role_binding',
    'autoscaler_skypilot_system_role',
    'autoscaler_skypilot_system_role_binding',
    'autoscaler_ingress_role',
    'autoscaler_ingress_role_binding',
    'skypilot_system_namespace',
    'port_mode',
)


def _rbac_cache_key(context: Optional[str], namespace: str,
                    provider_config: Dict[str, Any]) -> str:
    """Builds the kv_cache key for the RBAC + system-namespace setup.

    The key fingerprints (context, user namespace, all RBAC inputs). Any
    change to a cached resource definition or to which namespace the SA
    lives in causes the next launch to miss and re-verify.
    """
    payload: Dict[str, Any] = {
        f: provider_config.get(f) for f in _RBAC_FINGERPRINT_FIELDS
    }
    payload['namespace'] = namespace
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True,
                   default=str).encode('utf-8')).hexdigest()
    return f'k8s_provision_rbac:{context or ""}:{fingerprint}'


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    del region, cluster_name  # unused
    namespace = kubernetes_utils.get_namespace_from_config(
        config.provider_config)
    context = kubernetes_utils.get_context_from_config(config.provider_config)

    # Each step below is independent at the K8s API level (RBAC references are
    # resolved at evaluation time, not creation time), so we fan them out in a
    # thread pool. The kubernetes client's underlying urllib3 pool is
    # thread-safe.
    tasks: List[Callable[[], None]] = []

    tasks.append(
        lambda: _configure_services(namespace, context, config.provider_config))

    requested_service_account = config.node_config['spec']['serviceAccountName']
    setup_rbac = (requested_service_account ==
                  kubernetes_utils.DEFAULT_SERVICE_ACCOUNT_NAME)

    rbac_cache_key: Optional[str] = None
    rbac_cache_hit = False
    if setup_rbac:
        rbac_cache_key = _rbac_cache_key(context, namespace,
                                         config.provider_config)
        rbac_cache_hit = kv_cache.get_cache_entry(rbac_cache_key) is not None
        if rbac_cache_hit:
            logger.debug(
                f'bootstrap_instances: RBAC setup recently verified for '
                f'context={context!r} namespace={namespace!r}, skipping '
                f'{len(_RBAC_FINGERPRINT_FIELDS)} K8s reads.')

    if setup_rbac and not rbac_cache_hit:
        # If the user has requested a different service account (via pod_config
        # in ~/.sky/config.yaml), we assume they have already set up the
        # necessary roles and role bindings.
        # If not, set up the roles and bindings for skypilot-service-account
        # here.
        tasks.append(lambda: _configure_autoscaler_service_account(
            namespace, context, config.provider_config))
        tasks.append(lambda: _configure_autoscaler_role(namespace,
                                                        context,
                                                        config.provider_config,
                                                        resource_field=
                                                        'autoscaler_role'))
        tasks.append(lambda: _configure_autoscaler_role_binding(
            namespace,
            context,
            config.provider_config,
            resource_field='autoscaler_role_binding'))
        tasks.append(lambda: _configure_autoscaler_cluster_role(
            namespace, context, config.provider_config))
        tasks.append(lambda: _configure_autoscaler_cluster_role_binding(
            namespace, context, config.provider_config))
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
        tasks.append(lambda: _configure_skypilot_system_namespace(
            config.provider_config))
        if config.provider_config.get('port_mode', 'loadbalancer') == 'ingress':
            logger.info('Port mode is set to ingress, setting up ingress role '
                        'and role binding.')

            def _configure_ingress() -> None:
                try:
                    _configure_autoscaler_role(
                        namespace,
                        context,
                        config.provider_config,
                        resource_field='autoscaler_ingress_role')
                    _configure_autoscaler_role_binding(
                        namespace,
                        context,
                        config.provider_config,
                        resource_field='autoscaler_ingress_role_binding')
                except kubernetes.api_exception() as e:
                    # If namespace is not found, we will ignore the error
                    if e.status == 404:
                        logger.info(
                            'Namespace not found - is your nginx ingress '
                            'installed? Skipping ingress role and role '
                            'binding setup.')
                    else:
                        raise e

            tasks.append(_configure_ingress)

    elif not setup_rbac and requested_service_account != 'default':
        logger.info(f'Using service account {requested_service_account!r}, '
                    'skipping role and role binding setup.')

    if config.provider_config.get('fuse_device_required', False):
        tasks.append(lambda: _configure_fuse_mounting(config.provider_config))

    if len(tasks) == 1:
        tasks[0]()
    else:
        with futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            # list() forces all futures to materialize so exceptions surface;
            # the context manager waits for completion before returning.
            for future in futures.as_completed(
                [executor.submit(task) for task in tasks]):
                future.result()

    # Only mark verified after the RBAC fanout completes without raising —
    # if any task fails the executor re-raises and we never reach this line.
    if setup_rbac and not rbac_cache_hit and rbac_cache_key is not None:
        kv_cache.add_or_update_cache_entry(
            rbac_cache_key, '1',
            time.time() + _RBAC_CACHE_TTL_SECONDS)

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


def _read_or_none(read_fn: Callable[[], Any]) -> Optional[Any]:
    """Calls a K8s read_* method, returning None on 404."""
    try:
        return read_fn()
    except kubernetes.api_exception() as e:
        if e.status == 404:
            return None
        raise


def _create_or_patch_resource(
    log_prefix: str,
    resource_field: str,
    name: str,
    create_fn: Callable[[], Any],
    read_fn: Callable[[], Any],
    patch_fn: Optional[Callable[[], None]],
    needs_update_fn: Optional[Callable[[Any], bool]],
) -> None:
    """Creates a K8s resource with upsert semantics and 409 race handling.

    If the resource doesn't exist, tries to create it. On 409 (concurrent
    creation), re-reads and falls through to compare/patch. If the resource
    exists (found initially or after 409), compares and patches if needed.

    `read_fn` should call a K8s `read_*` method (a single-object GET that
    raises 404 if missing); it's faster than `list_*` with a field_selector,
    which forces a LIST scan on the apiserver.
    """
    existing = _read_or_none(read_fn)

    if existing is None:
        logger.info(f'{log_prefix}: '
                    f'{not_found_msg(resource_field, name)}')
        try:
            create_fn()
        except kubernetes.api_exception() as e:
            if e.status != 409:
                raise
            # Concurrently created by another process; re-read
            # and fall through to compare/patch below.
            existing = read_fn()
        else:
            logger.info(f'{log_prefix}: '
                        f'{created_msg(resource_field, name)}')
            return

    # Resource exists (found initially or re-read after 409).
    # Compare and patch if needed.
    if needs_update_fn is None or not needs_update_fn(existing):
        logger.info(f'{log_prefix}: '
                    f'{using_existing_msg(resource_field, name)}')
        return
    logger.info(f'{log_prefix}: '
                f'{updating_existing_msg(resource_field, name)}')
    assert patch_fn is not None, ('patch_fn must be provided when '
                                  'needs_update_fn is provided')
    patch_fn()


def _configure_autoscaler_service_account(
        namespace: str, context: Optional[str],
        provider_config: Dict[str, Any]) -> None:
    log_prefix = '_configure_autoscaler_service_account'
    resource_field = 'autoscaler_service_account'
    if resource_field not in provider_config:
        logger.info(f'{log_prefix}: '
                    f'{not_provided_msg(resource_field)}')
        return

    account = provider_config[resource_field]
    if 'namespace' not in account['metadata']:
        account['metadata']['namespace'] = namespace
    elif account['metadata']['namespace'] != namespace:
        raise InvalidNamespaceError(resource_field, namespace)

    name = account['metadata']['name']

    _create_or_patch_resource(
        log_prefix=log_prefix,
        resource_field=resource_field,
        name=name,
        create_fn=lambda: kubernetes.core_api(
            context).create_namespaced_service_account(namespace, account),
        read_fn=lambda: kubernetes.core_api(context).
        read_namespaced_service_account(name, namespace),
        # Nothing to compare/patch for service accounts.
        patch_fn=None,
        needs_update_fn=None,
    )


def _configure_autoscaler_role(namespace: str, context: Optional[str],
                               provider_config: Dict[str, Any],
                               resource_field: str) -> None:
    """ Reads the role from the provider config, creates if it does not exist.

    Args:
        namespace: The namespace to create the role in.
        provider_config: The provider config.
        role_field: The field in the provider config that contains the role.
    """
    log_prefix = '_configure_autoscaler_role'
    if resource_field not in provider_config:
        logger.info(f'{log_prefix}: '
                    f'{not_provided_msg(resource_field)}')
        return

    resource = provider_config[resource_field]
    if 'namespace' not in resource['metadata']:
        resource['metadata']['namespace'] = namespace
    else:
        namespace = resource['metadata']['namespace']

    name = resource['metadata']['name']
    new_role = kubernetes_utils.dict_to_k8s_object(resource, 'V1Role')

    _create_or_patch_resource(
        log_prefix=log_prefix,
        resource_field=resource_field,
        name=name,
        create_fn=lambda: kubernetes.auth_api(context).create_namespaced_role(
            namespace, resource),
        read_fn=lambda: kubernetes.auth_api(context).read_namespaced_role(
            name, namespace),
        patch_fn=lambda: kubernetes.auth_api(context).patch_namespaced_role(
            name, namespace, resource),
        needs_update_fn=lambda existing: new_role.rules != existing.rules,
    )


def _configure_autoscaler_role_binding(
        namespace: str,
        context: Optional[str],
        provider_config: Dict[str, Any],
        resource_field: str,
        override_name: Optional[str] = None,
        override_subject_namespace: Optional[str] = None) -> None:
    """ Reads the role binding from the config, creates if it does not exist.

    Args:
        namespace: The namespace to create the role binding in.
        provider_config: The provider config.
        binding_field: The field in the provider config that contains the role
    """
    log_prefix = '_configure_autoscaler_role_binding'
    if resource_field not in provider_config:
        logger.info(f'{log_prefix}: '
                    f'{not_provided_msg(resource_field)}')
        return

    resource = provider_config[resource_field]
    if 'namespace' not in resource['metadata']:
        resource['metadata']['namespace'] = namespace
        rb_namespace = namespace
    else:
        rb_namespace = resource['metadata']['namespace']

    # If override_subject_namespace is provided, we will use that
    # namespace for the subject. Otherwise, we will raise an error.
    subject_namespace = override_subject_namespace or namespace
    for subject in resource['subjects']:
        if 'namespace' not in subject:
            subject['namespace'] = subject_namespace
        elif subject['namespace'] != subject_namespace:
            subject_name = subject['name']
            raise InvalidNamespaceError(
                resource_field + f' subject {subject_name}', namespace)

    # Override name if provided
    resource['metadata']['name'] = override_name or resource['metadata']['name']
    name = resource['metadata']['name']
    new_rb = kubernetes_utils.dict_to_k8s_object(resource, 'V1RoleBinding')

    _create_or_patch_resource(
        log_prefix=log_prefix,
        resource_field=resource_field,
        name=name,
        create_fn=lambda: kubernetes.auth_api(
            context).create_namespaced_role_binding(rb_namespace, resource),
        read_fn=lambda: kubernetes.auth_api(
            context).read_namespaced_role_binding(name, rb_namespace),
        patch_fn=lambda: kubernetes.auth_api(context).
        patch_namespaced_role_binding(name, rb_namespace, resource),
        needs_update_fn=lambda existing:
        (new_rb.role_ref != existing.role_ref or new_rb.subjects != existing.
         subjects),
    )


def _configure_autoscaler_cluster_role(namespace, context,
                                       provider_config: Dict[str, Any]) -> None:
    log_prefix = '_configure_autoscaler_cluster_role'
    resource_field = 'autoscaler_cluster_role'
    if resource_field not in provider_config:
        logger.info(f'{log_prefix}: '
                    f'{not_provided_msg(resource_field)}')
        return

    resource = provider_config[resource_field]
    if 'namespace' not in resource['metadata']:
        resource['metadata']['namespace'] = namespace
    elif resource['metadata']['namespace'] != namespace:
        raise InvalidNamespaceError(resource_field, namespace)

    name = resource['metadata']['name']
    new_cr = kubernetes_utils.dict_to_k8s_object(resource, 'V1ClusterRole')

    _create_or_patch_resource(
        log_prefix=log_prefix,
        resource_field=resource_field,
        name=name,
        create_fn=lambda: kubernetes.auth_api(context).create_cluster_role(
            resource),
        read_fn=lambda: kubernetes.auth_api(context).read_cluster_role(name),
        patch_fn=lambda: kubernetes.auth_api(context).patch_cluster_role(
            name, resource),
        needs_update_fn=lambda existing: new_cr.rules != existing.rules,
    )


def _configure_autoscaler_cluster_role_binding(
        namespace, context, provider_config: Dict[str, Any]) -> None:
    log_prefix = '_configure_autoscaler_cluster_role_binding'
    resource_field = 'autoscaler_cluster_role_binding'
    if resource_field not in provider_config:
        logger.info(f'{log_prefix}: '
                    f'{not_provided_msg(resource_field)}')
        return

    resource = provider_config[resource_field]
    if 'namespace' not in resource['metadata']:
        resource['metadata']['namespace'] = namespace
    elif resource['metadata']['namespace'] != namespace:
        raise InvalidNamespaceError(resource_field, namespace)
    for subject in resource['subjects']:
        if 'namespace' not in subject:
            subject['namespace'] = namespace
        elif subject['namespace'] != namespace:
            subject_name = subject['name']
            raise InvalidNamespaceError(
                resource_field + f' subject {subject_name}', namespace)

    name = resource['metadata']['name']
    new_binding = kubernetes_utils.dict_to_k8s_object(resource,
                                                      'V1ClusterRoleBinding')

    _create_or_patch_resource(
        log_prefix=log_prefix,
        resource_field=resource_field,
        name=name,
        create_fn=lambda: kubernetes.auth_api(
            context).create_cluster_role_binding(resource),
        read_fn=lambda: kubernetes.auth_api(context).read_cluster_role_binding(
            name),
        patch_fn=lambda: kubernetes.auth_api(
            context).patch_cluster_role_binding(name, resource),
        needs_update_fn=lambda existing:
        (new_binding.role_ref != existing.role_ref or new_binding.subjects !=
         existing.subjects),
    )


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

    # We must create a unique role binding per-namespace that SkyPilot is
    # running in, so we override the name with a unique name identifying
    # the namespace. This is required for multi-tenant setups where
    # different SkyPilot instances may be running in different namespaces.
    override_name = provider_config['autoscaler_skypilot_system_role_binding'][
        'metadata']['name'] + '-' + svc_account_namespace

    # The role and role binding are independent K8s resources (the binding
    # references the role by name; K8s does not require the role to exist at
    # binding-creation time), so we create them in parallel.
    with futures.ThreadPoolExecutor(max_workers=2) as executor:
        role_future = executor.submit(
            _configure_autoscaler_role,
            skypilot_system_namespace,
            context,
            provider_config,
            resource_field='autoscaler_skypilot_system_role')
        # Create the role binding in the skypilot-system namespace, and have
        # the subject namespace be the namespace that the SkyPilot service
        # account is created in.
        binding_future = executor.submit(
            _configure_autoscaler_role_binding,
            skypilot_system_namespace,
            context,
            provider_config,
            resource_field='autoscaler_skypilot_system_role_binding',
            override_name=override_name,
            override_subject_namespace=svc_account_namespace)
        role_future.result()
        binding_future.result()


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
        daemonset = yaml_utils.safe_load(file)
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

    # Validate namespaces synchronously so InvalidNamespaceError still surfaces
    # cleanly without thread-pool wrapping.
    for service in services:
        if 'namespace' not in service['metadata']:
            service['metadata']['namespace'] = namespace
        elif service['metadata']['namespace'] != namespace:
            raise InvalidNamespaceError(service_field, namespace)

    def _reconcile(service: Dict[str, Any]) -> None:
        name = service['metadata']['name']
        existing_service = _read_or_none(lambda: kubernetes.core_api(
            context).read_namespaced_service(name, namespace))
        if existing_service is not None:
            new_svc = kubernetes_utils.dict_to_k8s_object(service, 'V1Service')
            if new_svc.spec.ports == existing_service.spec.ports:
                logger.info('_configure_services: '
                            f'{using_existing_msg("service", name)}')
                return
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

    if len(services) <= 1:
        for service in services:
            _reconcile(service)
        return

    with futures.ThreadPoolExecutor(max_workers=len(services)) as executor:
        for future in futures.as_completed(
            [executor.submit(_reconcile, svc) for svc in services]):
            future.result()


class KubernetesError(Exception):

    def __init__(self,
                 *args,
                 insufficent_resources: Optional[List[str]] = None):
        self.insufficent_resources = insufficent_resources
        super().__init__(*args)
