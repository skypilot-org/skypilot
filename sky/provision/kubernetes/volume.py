"""Kubernetes pvc provisioning."""
from typing import Any, Dict, List, Optional, Tuple

from sky import models
from sky import sky_logging
from sky.adaptors import kubernetes
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import utils as kubernetes_utils

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


def apply_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Creates or registers a volume."""
    context, namespace = _get_context_namespace(config)
    pvc_spec = _get_pvc_spec(namespace, config)
    create_persistent_volume_claim(namespace, context, pvc_spec)
    return config


def delete_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Deletes a volume."""
    context, namespace = _get_context_namespace(config)
    pvc_name = config.name_on_cloud
    logger.info(f'Deleting PVC: {pvc_name}')
    kubernetes_utils.delete_k8s_resource_with_retry(
        delete_func=lambda pvc_name=pvc_name: kubernetes.core_api(
            context).delete_namespaced_persistent_volume_claim(
                name=pvc_name,
                namespace=namespace,
                _request_timeout=config_lib.DELETION_TIMEOUT),
        resource_type='pvc',
        resource_name=pvc_name)
    return config


def get_volume_usedby(config: models.VolumeConfig) -> List[str]:
    """Gets the usedby resources of a volume."""
    context, namespace = _get_context_namespace(config)
    pvc_name = config.name_on_cloud
    usedby = []
    # Get all pods in the namespace
    pods = kubernetes.core_api(context).list_namespaced_pod(namespace=namespace)
    for pod in pods.items:
        if pod.spec.volumes is not None:
            for volume in pod.spec.volumes:
                if volume.persistent_volume_claim is not None:
                    if volume.persistent_volume_claim.claim_name == pvc_name:
                        usedby.append(pod.metadata.name)
    return usedby


def create_persistent_volume_claim(namespace: str, context: Optional[str],
                                   pvc_spec: Dict[str, Any]) -> None:
    """Creates a persistent volume claim for SkyServe controller."""
    pvc_name = pvc_spec['metadata']['name']
    try:
        kubernetes.core_api(context).read_namespaced_persistent_volume_claim(
            name=pvc_name, namespace=namespace)
        logger.debug(f'PVC {pvc_name} already exists')
        return
    except kubernetes.api_exception() as e:
        if e.status != 404:  # Not found
            raise
    logger.info(f'Creating PVC: {pvc_name}')
    kubernetes.core_api(context).create_namespaced_persistent_volume_claim(
        namespace=namespace, body=pvc_spec)


def _get_pvc_spec(namespace: str,
                  config: models.VolumeConfig) -> Dict[str, Any]:
    """Gets the PVC spec for the given storage config."""
    access_mode = config.config.get('access_mode')
    size = config.size
    # The previous code assumes that the access_mode and size are always set.
    assert access_mode is not None
    assert size is not None
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
            'resources': {
                'requests': {
                    'storage': f'{size}Gi'
                }
            },
        }
    }
    storage_class = config.config.get('storage_class_name')
    if storage_class is not None:
        pvc_spec['spec']['storageClassName'] = storage_class
    return pvc_spec
