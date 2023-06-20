from typing import List

from sky import status_lib
from sky.adaptors.kubernetes import core_api


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

