"""Kubernetes pvc provisioning."""
from typing import Any, Dict, List, Optional, Set, Tuple

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.adaptors import kubernetes
from sky.provision import constants
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import resources_utils
from sky.utils import volume as volume_lib

logger = sky_logging.init_logger(__name__)


def _get_context_namespace(config: models.VolumeConfig) -> Tuple[str, str]:
    """Gets the context and namespace of a volume."""
    if config.region is None:
        context = kubernetes_utils.get_current_kube_config_context_name()
        config.region = context
    else:
        context = config.region
    namespace = config.config.get('namespace')
    if namespace is None:
        namespace = kubernetes_utils.get_kube_config_context_namespace(context)
        config.config['namespace'] = namespace
    return context, namespace


def check_pvc_usage_for_pod(context: Optional[str], namespace: str,
                            pod_spec: Dict[str, Any]) -> None:
    """Checks if the PVC is used by any pod in the namespace."""
    volumes = pod_spec.get('spec', {}).get('volumes', [])
    if not volumes:
        return
    once_modes = [
        volume_lib.VolumeAccessMode.READ_WRITE_ONCE.value,
        volume_lib.VolumeAccessMode.READ_WRITE_ONCE_POD.value
    ]
    for volume in volumes:
        pvc_name = volume.get('persistentVolumeClaim', {}).get('claimName')
        if not pvc_name:
            continue
        pvc = kubernetes.core_api(
            context).read_namespaced_persistent_volume_claim(
                name=pvc_name, namespace=namespace)
        access_mode = pvc.spec.access_modes[0]
        if access_mode not in once_modes:
            continue
        usedby_pods, _ = _get_volume_usedby(context, namespace, pvc_name)
        if usedby_pods:
            raise config_lib.KubernetesError(f'Volume {pvc_name} with access '
                                             f'mode {access_mode} is already '
                                             f'in use by Pods {usedby_pods}.')


def apply_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Creates or registers a volume."""
    context, namespace = _get_context_namespace(config)
    pvc_spec = _get_pvc_spec(namespace, config)
    # Check if the storage class exists
    storage_class_name = pvc_spec['spec'].get('storageClassName')
    if storage_class_name is not None:
        try:
            kubernetes.storage_api(context).read_storage_class(
                name=storage_class_name)
        except kubernetes.api_exception() as e:
            raise config_lib.KubernetesError(
                f'Check storage class {storage_class_name} error: {e}')
    create_persistent_volume_claim(namespace, context, pvc_spec, config)
    return config


def delete_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Deletes a volume."""
    context, namespace = _get_context_namespace(config)
    pvc_name = config.name_on_cloud
    kubernetes_utils.delete_k8s_resource_with_retry(
        delete_func=lambda pvc_name=pvc_name: kubernetes.core_api(
            context).delete_namespaced_persistent_volume_claim(
                name=pvc_name,
                namespace=namespace,
                _request_timeout=config_lib.DELETION_TIMEOUT),
        resource_type='pvc',
        resource_name=pvc_name)
    logger.info(f'Deleted PVC {pvc_name} in namespace {namespace}')
    return config


def _get_volume_usedby(
    context: Optional[str],
    namespace: str,
    pvc_name: str,
) -> Tuple[List[str], List[str]]:
    """Gets the usedby resources of a volume.

    This function returns the pods and clusters that are using the volume.
    The usedby_pods is accurate, which also includes the Pods that are not
    managed by SkyPilot.

    Args:
        context: Kubernetes context
        namespace: Kubernetes namespace
        pvc_name: PVC name

    Returns:
        usedby_pods: List of pods using the volume. These may include pods
                     not created by SkyPilot.
        usedby_clusters: List of clusters using the volume.
    """
    usedby_pods = []
    usedby_clusters = []
    field_selector = ','.join([
        f'status.phase!={phase}'
        for phase in k8s_constants.PVC_NOT_HOLD_POD_PHASES
    ])
    cloud_to_name_map = _get_cluster_name_on_cloud_to_cluster_name_map()
    # Get all pods in the namespace
    pods = kubernetes.core_api(context).list_namespaced_pod(
        namespace=namespace, field_selector=field_selector)
    for pod in pods.items:
        if pod.spec.volumes is None:
            continue
        for volume in pod.spec.volumes:
            if volume.persistent_volume_claim is None:
                continue
            if volume.persistent_volume_claim.claim_name == pvc_name:
                usedby_pods.append(pod.metadata.name)
                # Get the real cluster name
                cluster_name_on_cloud = pod.metadata.labels.get(
                    constants.TAG_SKYPILOT_CLUSTER_NAME)
                if cluster_name_on_cloud is None:
                    continue
                cluster_name = cloud_to_name_map.get(cluster_name_on_cloud)
                if cluster_name is not None:
                    usedby_clusters.append(cluster_name)
    if usedby_pods:
        logger.debug(f'Volume {pvc_name} is used by Pods {usedby_pods}'
                     f' and clusters {usedby_clusters}')
    return usedby_pods, usedby_clusters


def _get_cluster_name_on_cloud_to_cluster_name_map() -> Dict[str, str]:
    """Gets the map from cluster name on cloud to cluster name."""
    clusters = global_user_state.get_clusters()
    cloud_to_name_map = {}
    for cluster in clusters:
        handle = cluster['handle']
        if handle is None:
            continue
        cloud_to_name_map[handle.cluster_name_on_cloud] = cluster['name']
    return cloud_to_name_map


def get_volume_usedby(
    config: models.VolumeConfig,) -> Tuple[List[str], List[str]]:
    """Gets the usedby resources of a volume."""
    context, namespace = _get_context_namespace(config)
    pvc_name = config.name_on_cloud
    return _get_volume_usedby(context, namespace, pvc_name)


def get_all_volumes_usedby(
    configs: List[models.VolumeConfig],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Gets the usedby resources of all volumes."""
    field_selector = ','.join([
        f'status.phase!={phase}'
        for phase in k8s_constants.PVC_NOT_HOLD_POD_PHASES
    ])
    label_selector = 'parent=skypilot'
    context_to_namespaces: Dict[str, Set[str]] = {}
    pvc_names = set()
    for config in configs:
        context, namespace = _get_context_namespace(config)
        if context not in context_to_namespaces:
            context_to_namespaces[context] = set()
        context_to_namespaces[context].add(namespace)
        pvc_names.add(config.name_on_cloud)
    cloud_to_name_map = _get_cluster_name_on_cloud_to_cluster_name_map()
    # Get all pods in the namespace
    used_by_pods: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
    used_by_clusters: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
    for context, namespaces in context_to_namespaces.items():
        used_by_pods[context] = {}
        used_by_clusters[context] = {}
        for namespace in namespaces:
            used_by_pods[context][namespace] = {}
            used_by_clusters[context][namespace] = {}
            pods = kubernetes.core_api(context).list_namespaced_pod(
                namespace=namespace,
                field_selector=field_selector,
                label_selector=label_selector)
            for pod in pods.items:
                if pod.spec.volumes is None:
                    continue
                for volume in pod.spec.volumes:
                    if volume.persistent_volume_claim is None:
                        continue
                    volume_name = volume.persistent_volume_claim.claim_name
                    if volume_name not in pvc_names:
                        continue
                    if volume_name not in used_by_pods[context][namespace]:
                        used_by_pods[context][namespace][volume_name] = []
                    used_by_pods[context][namespace][volume_name].append(
                        pod.metadata.name)
                    cluster_name_on_cloud = pod.metadata.labels.get(
                        constants.TAG_SKYPILOT_CLUSTER_NAME)
                    if cluster_name_on_cloud is None:
                        continue
                    cluster_name = cloud_to_name_map.get(cluster_name_on_cloud)
                    if cluster_name is None:
                        continue
                    if cluster_name not in used_by_clusters[context][namespace]:
                        used_by_clusters[context][namespace][cluster_name] = []
                    used_by_clusters[context][namespace][cluster_name].append(
                        cluster_name)
    return used_by_pods, used_by_clusters


def map_all_volumes_usedby(
        used_by_pods: Dict[str, Any], used_by_clusters: Dict[str, Any],
        config: models.VolumeConfig) -> Tuple[List[str], List[str]]:
    """Maps the usedby resources of a volume."""
    context, namespace = _get_context_namespace(config)
    pvc_name = config.name_on_cloud

    return (used_by_pods.get(context, {}).get(namespace, {}).get(pvc_name, []),
            used_by_clusters.get(context, {}).get(namespace,
                                                  {}).get(pvc_name, []))


def _populate_config_from_pvc(config: models.VolumeConfig,
                              pvc_obj: Any) -> None:
    """Populate missing fields in config from a PVC object.

    Args:
        config: VolumeConfig to populate
        pvc_obj: V1PersistentVolumeClaim object from kubernetes client
    """
    if pvc_obj is None:
        return
    pvc_name = pvc_obj.metadata.name

    # Populate storageClassName if not set
    if config.config.get('storage_class_name') is None:
        pvc_storage_class = getattr(pvc_obj.spec, 'storage_class_name', None)
        if pvc_storage_class:
            config.config['storage_class_name'] = pvc_storage_class

    # Populate size if not set (prefer bound capacity, fallback to requested)
    pvc_size = None
    size_quantity = None
    # Try status.capacity (dict) - actual bound size
    capacity = getattr(getattr(pvc_obj, 'status', None), 'capacity', None)
    if isinstance(capacity, dict) and 'storage' in capacity:
        size_quantity = capacity['storage']
    # Fallback to spec.resources.requests (dict) - requested size
    if size_quantity is None:
        requests = getattr(getattr(pvc_obj.spec, 'resources', None), 'requests',
                           None)
        if isinstance(requests, dict):
            size_quantity = requests.get('storage')
    # Parse and normalize the size if found
    if size_quantity:
        try:
            # Normalize to GB string (e.g., '20')
            pvc_size = resources_utils.parse_memory_resource(
                size_quantity, 'size', allow_rounding=True)
        except ValueError as e:
            # Just log the error since it is not critical.
            logger.warning(f'Failed to parse PVC size {size_quantity!r} '
                           f'for PVC {pvc_name}: {e}')
    if pvc_size is not None:
        if config.size is not None and config.size != pvc_size:
            logger.warning(f'PVC {pvc_name} has size {pvc_size} but config '
                           f'size is {config.size}, overriding the config size'
                           f' with the PVC size.')
        config.size = pvc_size


def create_persistent_volume_claim(
    namespace: str,
    context: Optional[str],
    pvc_spec: Dict[str, Any],
    config: Optional[models.VolumeConfig] = None,
) -> None:
    """Creates a persistent volume claim for SkyServe controller."""
    pvc_name = pvc_spec['metadata']['name']
    try:
        pvc = kubernetes.core_api(
            context).read_namespaced_persistent_volume_claim(
                name=pvc_name, namespace=namespace)
        if config is not None:
            _populate_config_from_pvc(config, pvc)
        logger.debug(f'PVC {pvc_name} already exists')
        return
    except kubernetes.api_exception() as e:
        if e.status != 404:  # Not found
            raise
    use_existing = config is not None and config.config.get('use_existing')
    if use_existing:
        raise ValueError(
            f'PVC {pvc_name} does not exist while use_existing is True.')
    pvc = kubernetes.core_api(
        context).create_namespaced_persistent_volume_claim(namespace=namespace,
                                                           body=pvc_spec)
    logger.info(f'Created PVC {pvc_name} in namespace {namespace}')
    if config is not None:
        _populate_config_from_pvc(config, pvc)


def _get_pvc_spec(namespace: str,
                  config: models.VolumeConfig) -> Dict[str, Any]:
    """Gets the PVC spec for the given storage config."""
    access_mode = config.config.get('access_mode')
    size = config.size
    # The previous code assumes that the access_mode and size are always set.
    assert access_mode is not None, f'access_mode is None for volume ' \
                                    f'{config.name_on_cloud}'
    pvc_spec: Dict[str, Any] = {
        'metadata': {
            'name': config.name_on_cloud,
            'namespace': namespace,
            'labels': {
                'parent': 'skypilot',
                'skypilot-name': config.name,
            }
        },
        'spec': {
            'accessModes': [access_mode],
        }
    }
    if size is not None:
        pvc_spec['spec']['resources'] = {'requests': {'storage': f'{size}Gi'}}
    if config.labels:
        pvc_spec['metadata']['labels'].update(config.labels)
    storage_class = config.config.get('storage_class_name')
    if storage_class is not None:
        pvc_spec['spec']['storageClassName'] = storage_class
    return pvc_spec
