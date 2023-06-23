from typing import List, Tuple, Optional

from sky import status_lib
from sky.adaptors import kubernetes


def get_head_ssh_port(cluster_name, namespace):
    svc_name = f'{cluster_name}-ray-head-ssh'
    return get_port(svc_name, namespace)


def get_port(svc_name, namespace):
    head_service = kubernetes.core_api().read_namespaced_service(
        svc_name, namespace)
    return head_service.spec.ports[0].node_port


def check_credentials() -> Tuple[bool, Optional[str]]:
    """
    Check if the credentials in kubeconfig file are valid

    Returns:
        bool: True if credentials are valid, False otherwise
        str: Error message if credentials are invalid, None otherwise
    """
    try:
        kubernetes.core_api().list_namespace()
        return True, None
    except kubernetes.api_exception() as e:
        # Check if the error is due to invalid credentials
        if e.status == 401:
            return False, 'Invalid credentials - do you have permission ' \
                          'to access the cluster?'
        else:
            return False, f'Failed to communicate with the cluster: {str(e)}'
    except kubernetes.config_exception() as e:
        return False, f'Invalid configuration file: {str(e)}'
    except Exception as e:
        return False, f'An error occurred: {str(e)}'


def get_cluster_status(cluster_name: str,
                       namespace: str) -> List[status_lib.ClusterStatus]:
    # Get all the pods with the label skypilot-cluster: <cluster_name>
    pods = kubernetes.core_api().list_namespaced_pod(
        namespace, label_selector=f'skypilot-cluster={cluster_name}').items

    # Check if the pods are running or pending
    cluster_status = []
    for pod in pods:
        if pod.status.phase == 'Running':
            cluster_status.append(
                status_lib.ClusterStatus(cluster_name,
                                         status_lib.ClusterStatus.UP))
        elif pod.status.phase == 'Pending':
            cluster_status.append(
                status_lib.ClusterStatus(cluster_name,
                                         status_lib.ClusterStatus.INIT))
    # If pods are not found, we don't add them to the return list
    return cluster_status
