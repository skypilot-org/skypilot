"""Kubernetes utilities for SkyPilot."""
from typing import Optional, Set, Tuple

from sky.adaptors import kubernetes
from sky.utils import common_utils

DEFAULT_NAMESPACE = 'default'


class GPULabelFormatter:
    @classmethod
    def get_label_key(cls) -> str:
        raise NotImplementedError

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        raise NotImplementedError


def get_gke_eks_accelerator_name(accelerator: str) -> str:
    """Returns the accelerator name for GKE and EKS clusters

    Both use the same format - nvidia-tesla-<accelerator>.
    A100-80GB and L4 are an exception - they use nvidia-<accelerator>.
    """
    if accelerator in ('A100-80GB', 'L4'):
        # A100-80GB and L4 have a different name pattern.
        return 'nvidia-{}'.format(accelerator.lower())
    else:
        return 'nvidia-tesla-{}'.format(
            accelerator.lower())


class GKELabelFormatter(GPULabelFormatter):
    @classmethod
    def get_label_key(cls) -> str:
        return 'cloud.google.com/gke-accelerator'

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        return get_gke_eks_accelerator_name(accelerator)


class EKSLabelFormatter(GPULabelFormatter):
    @classmethod
    def get_label_key(cls) -> str:
        return 'k8s.amazonaws.com/accelerator'

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        return get_gke_eks_accelerator_name(accelerator)


class SkyPilotLabelFormatter(GPULabelFormatter):
    @classmethod
    def get_label_key(cls) -> str:
        return 'skypilot.co/accelerator'

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        # For SkyPilot formatter, we adopt GKE/EKS accelerator format.
        return get_gke_eks_accelerator_name(accelerator)


# LABEL_FORMATTER_REGISTRY stores the label formats SkyPilot will try to
# discover the accelerator type from. The order of the list is important, as
# it will be used to determine the priority of the label formats.
LABEL_FORMATTER_REGISTRY = [SkyPilotLabelFormatter, GKELabelFormatter,
                            EKSLabelFormatter]


def detect_gpu_label_formatter() -> Tuple[Optional[GPULabelFormatter],
Set[str]]:
    # Get the set of labels across all nodes
    # TODO(romilb): This is not efficient. We should cache the node labels
    node_labels: Set[str] = set()
    for node in kubernetes.core_api().list_node().items:
        node_labels.update(node.metadata.labels.keys())

    # Check if the node labels contain any of the GPU label prefixes
    for label_formatter in LABEL_FORMATTER_REGISTRY:
        if label_formatter.get_label_key() in node_labels:
            return label_formatter(), node_labels
    return None, node_labels


def get_head_ssh_port(cluster_name: str, namespace: str) -> int:
    svc_name = f'{cluster_name}-ray-head-ssh'
    return get_port(svc_name, namespace)


def get_port(svc_name: str, namespace: str) -> int:
    """
    Gets the nodeport of the specified service.

    Args:
        svc_name (str): Name of the kubernetes service. Note that this may be
            different from the cluster name.
        namespace (str): Kubernetes namespace to look for the service in.
    """
    head_service = kubernetes.core_api().read_namespaced_service(
        svc_name, namespace)
    return head_service.spec.ports[0].node_port


def check_credentials(timeout: int = kubernetes.API_TIMEOUT) -> \
        Tuple[bool, Optional[str]]:
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
        return False, '`kubernetes` package is not installed. ' \
                      'Install it with: pip install kubernetes'
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
        return False, common_utils.format_exception(e)
    except Exception as e: # pylint: disable=broad-except
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
        if 'namespace' in current_context['context']:
            return current_context['context']['namespace']
        else:
            return DEFAULT_NAMESPACE
    except k8s.config.config_exception.ConfigException:
        return DEFAULT_NAMESPACE
