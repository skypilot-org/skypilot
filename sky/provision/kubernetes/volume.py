"""Kubernetes pvc provisioning."""
import copy
import json
import time
from typing import Any, Callable, Dict, List, Optional, Union
import uuid

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes
from sky.provision import common
from sky.provision import constants
from sky.provision import docker_utils
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import network_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import kubernetes_enums
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

def create_storage(config: common.VolumeConfig) -> common.VolumeConfig:
    """Creates a persistent volume claim for SkyServe controller."""
    if config.region is None:
        context = kubernetes_utils.get_current_kube_config_context_name()
        config.region=context
    else:
        context = config.region
    namespace=config.spec.get('namespace')
    if namespace is None:
        namespace=kubernetes_utils.get_kube_config_context_namespace(context)
        config.spec['namespace']=namespace
    
    pvc_spec=_get_pvc_spec(namespace, config)
    create_persistent_volume_claim(namespace, context, pvc_spec)
    return config

def create_persistent_volume_claim(namespace: str, context: Optional[str],
                                    pvc_spec: Dict[str, Any]) -> None:
    """Creates a persistent volume claim for SkyServe controller."""
    pvc_name=pvc_spec['metadata']['name']
    try:
        kubernetes.core_api(context).read_namespaced_persistent_volume_claim(
            name=pvc_name, namespace=namespace)
        logger.info(f'PVC {pvc_name} already exists')
        return
    except kubernetes.api_exception() as e:
        if e.status != 404:  # Not found
            raise
    logger.info(f'Creating PVC: {pvc_name}')
    kubernetes.core_api(context).create_namespaced_persistent_volume_claim(
        namespace=namespace, body=pvc_spec)
def _get_pvc_spec(namespace: str, config: common.VolumeConfig) -> Dict[str, Any]:
    """Gets the PVC spec for the given storage config."""
    pvc_spec={
        'metadata': {
            'name': config.name_on_cloud,
            'namespace': namespace,
        },
        'spec': {
            'accessModes': [config.spec.get('access_mode')],
            'resources': {'requests': {'storage': f'{config.size}Gi'}},
        }
    }
    storage_class=config.spec.get('storage_class_name')
    if storage_class is not None:
        pvc_spec['spec']['storageClassName'] = storage_class
    return pvc_spec
