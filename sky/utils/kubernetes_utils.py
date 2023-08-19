"""Kubernetes utilities for SkyPilot."""
from typing import Any, List, Optional, Set, Tuple

from sky import exceptions
from sky.adaptors import kubernetes
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import ux_utils

DEFAULT_NAMESPACE = 'default'


class GPULabelFormatter:
    """Base class to define a GPU label formatter for a Kubernetes cluster

    A GPU label formatter is a class that defines how to use GPU type labels in
    a Kubernetes cluster. It is used by the Kubernetes cloud class to pick the
    key:value pair to use as node selector for GPU nodes.
    """

    @classmethod
    def get_label_key(cls) -> str:
        """Returns the label key for GPU type used by the Kubernetes cluster"""
        raise NotImplementedError

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        """Given a GPU type, returns the label value to be used"""
        raise NotImplementedError


def get_gke_accelerator_name(accelerator: str) -> str:
    """Returns the accelerator name for GKE clusters

    Uses the format - nvidia-tesla-<accelerator>.
    A100-80GB and L4 are an exception - they use nvidia-<accelerator>.
    """
    if accelerator in ('A100-80GB', 'L4'):
        # A100-80GB and L4 have a different name pattern.
        return 'nvidia-{}'.format(accelerator.lower())
    else:
        return 'nvidia-tesla-{}'.format(accelerator.lower())


class GKELabelFormatter(GPULabelFormatter):
    """GKE label formatter

    GKE nodes by default are populated with `cloud.google.com/gke-accelerator`
    label, which is used to identify the GPU type.
    """

    LABEL_KEY = 'cloud.google.com/gke-accelerator'

    @classmethod
    def get_label_key(cls) -> str:
        return cls.LABEL_KEY

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        return get_gke_accelerator_name(accelerator)


class SkyPilotLabelFormatter(GPULabelFormatter):
    """Custom label formatter for SkyPilot

    Uses skypilot.co/accelerator as the key, and SkyPilot accelerator str as the
    value.
    """

    LABEL_KEY = 'skypilot.co/accelerator'

    @classmethod
    def get_label_key(cls) -> str:
        return cls.LABEL_KEY

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        # For SkyPilot formatter, we use the accelerator str directly.
        # See sky.utils.kubernetes.gpu_labeler.
        return accelerator.lower()


# LABEL_FORMATTER_REGISTRY stores the label formats SkyPilot will try to
# discover the accelerator type from. The order of the list is important, as
# it will be used to determine the priority of the label formats.
LABEL_FORMATTER_REGISTRY = [SkyPilotLabelFormatter, GKELabelFormatter]


def detect_gpu_label_formatter(
) -> Tuple[Optional[GPULabelFormatter], List[Tuple[str, str]]]:
    """Detects the GPU label formatter for the Kubernetes cluster

    Returns:
        GPULabelFormatter: The GPU label formatter for the cluster, if found.
        List[Tuple[str, str]]: The set of labels and values across all nodes.
    """
    # Get all labels across all nodes
    node_labels: List[Tuple[str, str]] = []
    nodes = get_kubernetes_nodes()
    for node in nodes:
        node_labels.extend(node.metadata.labels.items())

    label_formatter = None

    # Check if the node labels contain any of the GPU label prefixes
    for lf in LABEL_FORMATTER_REGISTRY:
        label_key = lf.get_label_key()
        for label, _ in node_labels:
            if label.startswith(label_key):
                label_formatter = lf()
                break

    return label_formatter, node_labels


def detect_gpu_resource() -> Tuple[bool, Set[str]]:
    """Checks if the Kubernetes cluster has nvidia.com/gpu resource.

    If nvidia.com/gpu resource is missing, that typically means that the
    Kubernetes cluster does not have GPUs or the nvidia GPU operator and/or
    device drivers are not installed.

    Returns:
        bool: True if the cluster has nvidia.com/gpu resource, False otherwise.
    """
    # Get the set of resources across all nodes
    cluster_resources: Set[str] = set()
    nodes = get_kubernetes_nodes()
    for node in nodes:
        cluster_resources.update(node.status.allocatable.keys())
    has_gpu = 'nvidia.com/gpu' in cluster_resources

    return has_gpu, cluster_resources


def get_kubernetes_nodes() -> List[Any]:
    # TODO(romilb): Calling kube API can take between 10-100ms depending on
    #  the control plane. Consider caching calls to this function (using
    #  kubecontext hash as key).
    try:
        nodes = kubernetes.core_api().list_node(
            _request_timeout=kubernetes.API_TIMEOUT).items
    except kubernetes.max_retry_error():
        raise exceptions.ResourcesUnavailableError(
            'Timed out when trying to get node info from Kubernetes cluster. '
            'Please check if the cluster is healthy and retry.') from None
    return nodes


def get_gpu_label_key_value(acc_type: str, check_mode=False) -> Tuple[str, str]:
    """Returns the label key and value for the given GPU type.

    Args:
        acc_type: The GPU type required by the task.
        check_mode: If True, only checks if the cluster has GPU resources and
            labels are setup on the cluster. acc_type is ignore does not return
            the label key and value. Useful for checking if GPUs are configured
            correctly on the cluster without explicitly requesting a acc_type.
    Returns:
        A tuple of the label key and value. Returns empty strings if check_mode
        is True.
    Raises:
        ResourcesUnavailableError: Can be raised from the following conditions:
            - The cluster does not have GPU resources (nvidia.com/gpu)
            - The cluster does not have GPU labels setup correctly
            - The cluster doesn't have any nodes with acc_type GPU
    """
    # Check if the cluster has GPU resources
    # TODO(romilb): This assumes the accelerator is a nvidia GPU. We
    #  need to support TPUs and other accelerators as well.
    # TODO(romilb): This will fail early for autoscaling clusters.
    #  For AS clusters, we may need a way for users to specify GPU node pools
    #  to use since the cluster may be scaling up from zero nodes and may not
    #  have any GPU nodes yet.
    has_gpus, cluster_resources = detect_gpu_resource()
    if has_gpus:
        # Check if the cluster has GPU labels setup correctly
        label_formatter, node_labels = \
            detect_gpu_label_formatter()
        if label_formatter is None:
            # If none of the GPU labels from LABEL_FORMATTER_REGISTRY are
            # detected, raise error
            with ux_utils.print_exception_no_traceback():
                supported_formats = ', '.join(
                    [f.get_label_key() for f in LABEL_FORMATTER_REGISTRY])
                suffix = ''
                if env_options.Options.SHOW_DEBUG_INFO.get():
                    suffix = f' Found node labels: {node_labels}'
                raise exceptions.ResourcesUnavailableError(
                    'Could not detect GPU labels in Kubernetes cluster. '
                    'If this cluster has GPUs, please ensure GPU nodes have '
                    'node labels of either of these formats: '
                    f'{supported_formats}. Please refer to '
                    'the documentation on how to set up node labels.'
                    f'{suffix}')
        if label_formatter is not None:
            if check_mode:
                # If check mode is enabled and we reached so far, we can
                # conclude that the cluster is setup correctly and return.
                return '', ''
            k8s_acc_label_key = label_formatter.get_label_key()
            k8s_acc_label_value = label_formatter.get_label_value(acc_type)
            # Search in node_labels to see if any node has the requested
            # GPU type.
            # Note - this only checks if the label is available on a
            # node. It does not (and should not) check if the resource
            # quantity is available since that is dynamic and can change
            # during scheduling.
            for label, value in node_labels:
                if label == k8s_acc_label_key and value == k8s_acc_label_value:
                    # If a node is found, we can break out of the loop
                    # and proceed to deploy.
                    return k8s_acc_label_key, k8s_acc_label_value
            # If no node is found with the requested acc_type, raise error
            with ux_utils.print_exception_no_traceback():
                suffix = ''
                if env_options.Options.SHOW_DEBUG_INFO.get():
                    gpus_available = set(
                        v for k, v in node_labels if k == k8s_acc_label_key)
                    suffix = f' Available GPUs on the cluster: {gpus_available}'
                raise exceptions.ResourcesUnavailableError(
                    'Could not find any node in the Kubernetes cluster '
                    f'with {acc_type} GPU. Please ensure at least '
                    f'one node in the cluster has {acc_type} GPU and . '
                    'Please refer to the documentation on how to set up '
                    f'node labels.{suffix}')
    else:
        # If GPU resources are not detected, raise error
        with ux_utils.print_exception_no_traceback():
            suffix = ''
            if env_options.Options.SHOW_DEBUG_INFO.get():
                suffix = (' Available resources on the cluster: '
                          f'{cluster_resources}')
            raise exceptions.ResourcesUnavailableError(
                'Could not detect GPU resources (`nvidia.com/gpu`) in '
                'Kubernetes cluster. If this cluster contains GPUs, please '
                'ensure GPU drivers are installed on the node. Check if the '
                'GPUs are setup correctly by running `kubectl describe nodes` '
                'and looking for the nvidia.com/gpu resource. '
                'Please refer to the documentation on how '
                f'to set up GPUs.{suffix}')


def get_head_ssh_port(cluster_name: str, namespace: str) -> int:
    svc_name = f'{cluster_name}-ray-head-ssh'
    return get_port(svc_name, namespace)


def get_port(svc_name: str, namespace: str) -> int:
    """Gets the nodeport of the specified service.

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
    """Check if the credentials in kubeconfig file are valid

    Args:
        timeout (int): Timeout in seconds for the test API call

    Returns:
        bool: True if credentials are valid, False otherwise
        str: Error message if credentials are invalid, None otherwise
    """
    try:
        ns = get_current_kube_config_context_namespace()
        kubernetes.core_api().list_namespaced_pod(ns, _request_timeout=timeout)
    except ImportError:
        # TODO(romilb): Update these error strs to also include link to docs
        #  when docs are ready.
        return False, ('`kubernetes` package is not installed. '
                       'Install it with: pip install kubernetes')
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
        return False, ('Failed to communicate with the cluster - timeout. '
                       'Check if your cluster is running and your network '
                       'is stable.')
    except ValueError as e:
        return False, common_utils.format_exception(e)
    except Exception as e:  # pylint: disable=broad-except
        return False, ('An error occurred: '
                       f'{common_utils.format_exception(e, use_bracket=True)}')
    # If we reach here, the credentials are valid and Kubernetes cluster is up
    # We now check if GPUs are available and labels are set correctly on the
    # cluster, and if not we return hints that may help debug any issues.
    # This early check avoids later surprises for user when they try to run
    # `sky launch --gpus <gpu>` and the optimizer does not list Kubernetes as a
    # provider if their cluster GPUs are not setup correctly.
    try:
        _, _ = get_gpu_label_key_value(acc_type='', check_mode=True)
    except exceptions.ResourcesUnavailableError as e:
        # If GPUs are not available, we return cluster as enabled (since it can
        # be a CPU-only cluster) but we also return the exception message which
        # serves as a hint for how to enable GPU access.
        return True, f'{e}'
    return True, None


def get_current_kube_config_context_name() -> Optional[str]:
    """Get the current kubernetes context from the kubeconfig file

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
    """Get the current kubernetes context namespace from the kubeconfig file

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
