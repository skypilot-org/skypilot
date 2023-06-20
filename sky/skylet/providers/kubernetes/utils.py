from typing import List

import kubernetes
from kubernetes.config.config_exception import ConfigException

from sky import status_lib

_configured = False
_core_api = None
_auth_api = None
_networking_api = None
_custom_objects_api = None

log_prefix = "KubernetesNodeProvider: "

def _load_config():
    global _configured
    if _configured:
        return
    try:
        kubernetes.config.load_incluster_config()
    except ConfigException:
        kubernetes.config.load_kube_config()
    _configured = True


def core_api():
    global _core_api
    if _core_api is None:
        _load_config()
        _core_api = kubernetes.client.CoreV1Api()

    return _core_api


def auth_api():
    global _auth_api
    if _auth_api is None:
        _load_config()
        _auth_api = kubernetes.client.RbacAuthorizationV1Api()

    return _auth_api


def networking_api():
    global _networking_api
    if _networking_api is None:
        _load_config()
        _networking_api = kubernetes.client.NetworkingV1Api()

    return _networking_api


def custom_objects_api():
    global _custom_objects_api
    if _custom_objects_api is None:
        _load_config()
        _custom_objects_api = kubernetes.client.CustomObjectsApi()

    return _custom_objects_api


def get_head_ssh_port(cluster_name, namespace):
    svc_name = f'{cluster_name}-ray-head-ssh'
    return get_port(svc_name, namespace)


def get_port(svc_name, namespace):
    head_service = core_api().read_namespaced_service(svc_name, namespace)
    return head_service.spec.ports[0].node_port


def get_cluster_status(cluster_name: str, namespace: str) -> List[status_lib.ClusterStatus]:
    # Get all the pods with the label skypilot-cluster: <cluster_name>
    pods = core_api().list_namespaced_pod(namespace, label_selector=f'skypilot-cluster={cluster_name}').items

    # Check if the pods are running or pending
    cluster_status = []
    for pod in pods:
        if pod.status.phase == 'Running':
            cluster_status.append(status_lib.ClusterStatus(cluster_name, status_lib.ClusterStatus.UP))
        elif pod.status.phase == 'Pending':
            cluster_status.append(status_lib.ClusterStatus(cluster_name, status_lib.ClusterStatus.INIT))
    # If pods are not found, we don't add them to the return list
    return cluster_status






class KubernetesError(Exception):
    pass
