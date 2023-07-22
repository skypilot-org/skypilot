from typing import List, Tuple, Optional

from urllib.parse import urlparse

from sky import status_lib
from sky.adaptors import kubernetes

DEFAULT_NAMESPACE = 'default'


def get_head_ssh_port(cluster_name, namespace):
    svc_name = f'{cluster_name}-ray-head-ssh'
    return get_port(svc_name, namespace)


def get_port(svc_name, namespace):
    head_service = kubernetes.core_api().read_namespaced_service(
        svc_name, namespace)
    return head_service.spec.ports[0].node_port


def get_external_ip():
    # Return the IP address of the first node with an external IP
    nodes = kubernetes.core_api().list_node().items
    for node in nodes:
        if node.status.addresses:
            for address in node.status.addresses:
                if address.type == 'ExternalIP':
                    return address.address
    # If no external IP is found, use the API server IP
    api_host = kubernetes.core_api().api_client.configuration.host
    parsed_url = urlparse(api_host)
    return parsed_url.hostname


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
        ns = get_current_kube_config_context_namespace()
        kubernetes.core_api().list_namespaced_pod(ns, _request_timeout=timeout)
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
    except ValueError as e:
        return False, str(e)
    except Exception as e:
        return False, f'An error occurred: {str(e)}'


def get_current_kube_config_context_name() -> Optional[str]:
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


def get_current_kube_config_context_namespace() -> str:
    """
    Get the current kubernetes context namespace from the kubeconfig file

    Returns:
        str | None: The current kubernetes context namespace if it exists, else
            the default namespace.
    """
    k8s = kubernetes.get_kubernetes()
    try:
        _, current_context = k8s.config.list_kube_config_contexts()
        if 'namespace' in current_context:
            return current_context['namespace']
        else:
            return DEFAULT_NAMESPACE
    except k8s.config.config_exception.ConfigException:
        return DEFAULT_NAMESPACE
