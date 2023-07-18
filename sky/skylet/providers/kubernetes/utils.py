from typing import List, Tuple, Optional, Union

from sky import status_lib
from sky.adaptors import kubernetes


def get_head_ssh_port(cluster_name, namespace):
    svc_name = f'{cluster_name}-ray-head-ssh'
    return get_port(svc_name, namespace)


def get_port(svc_name, namespace):
    head_service = kubernetes.core_api().read_namespaced_service(
        svc_name, namespace)
    return head_service.spec.ports[0].node_port


def check_credentials(timeout: int = 3) -> Tuple[bool, Optional[str]]:
    """
    Check if the credentials in kubeconfig file are valid

    Args:
        timeout (int): Timeout in seconds for the test API call

    Returns:
        bool: True if credentials are valid, False otherwise
        str: Error message if credentials are invalid, None otherwise
    """
    try:
        kubernetes.core_api().list_namespace(_request_timeout=timeout)
        return True, None
    except ImportError:
        # TODO(romilb): Update these error strs to also include link to docs
        #  when docs are ready.
        return False, f'`kubernetes` package is not installed. ' \
                      f'Install it with: pip install kubernetes'
    except kubernetes.api_exception() as e:
        # Check if the error is due to invalid credentials
        if e.status == 401:
            return False, 'Invalid credentials - do you have permission ' \
                          'to access the cluster?'
        else:
            return False, f'Failed to communicate with the cluster: {str(e)}'
    except kubernetes.config_exception() as e:
        return False, f'Invalid configuration file: {str(e)}'
    except kubernetes.max_retry_error():
        return False, 'Failed to communicate with the cluster - timeout. ' \
                      'Check if your cluster is running and your network ' \
                      'is stable.'
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
            cluster_status.append(status_lib.ClusterStatus.UP)
        elif pod.status.phase == 'Pending':
            cluster_status.append(status_lib.ClusterStatus.INIT)
    # If pods are not found, we don't add them to the return list
    return cluster_status


def get_current_kube_config_context() -> Union[str, None]:
    """
    Get the current kubernetes context from the kubeconfig file

    Returns:
        str | None: The current kubernetes context if it exists, None otherwise
    """
    k8s = kubernetes.get_kubernetes()
    try:
        _, current_context = k8s.config.list_kube_config_contexts()
        return current_context['name']
    except k8s.config.config_exception.ConfigException:
        return None
