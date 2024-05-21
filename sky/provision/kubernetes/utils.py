"""Kubernetes utilities for SkyPilot."""
import json
import math
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlparse

import jinja2
import yaml

import sky
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes
from sky.provision.kubernetes import network_utils
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import kubernetes_enums
from sky.utils import schemas
from sky.utils import ux_utils

# TODO(romilb): Move constants to constants.py
DEFAULT_NAMESPACE = 'default'

DEFAULT_SERVICE_ACCOUNT_NAME = 'skypilot-service-account'

MEMORY_SIZE_UNITS = {
    'B': 1,
    'K': 2**10,
    'M': 2**20,
    'G': 2**30,
    'T': 2**40,
    'P': 2**50,
}
NO_GPU_ERROR_MESSAGE = 'No GPUs found in Kubernetes cluster. \
If your cluster contains GPUs, make sure nvidia.com/gpu resource is available on the nodes and the node labels for identifying GPUs \
(e.g., skypilot.co/accelerator) are setup correctly. \
To further debug, run: sky check.'

KUBERNETES_AUTOSCALER_NOTE = (
    'Note: Kubernetes cluster autoscaling is enabled. '
    'All GPUs that can be provisioned may not be listed '
    'here. Refer to your autoscaler\'s node pool '
    'configuration to see the list of supported GPUs.')

# TODO(romilb): Add links to docs for configuration instructions when ready.
ENDPOINTS_DEBUG_MESSAGE = ('Additionally, make sure your {endpoint_type} '
                           'is configured correctly. '
                           '\nTo debug, run: {debug_cmd}')

KIND_CONTEXT_NAME = 'kind-skypilot'  # Context name used by sky local up

logger = sky_logging.init_logger(__name__)


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

    @classmethod
    def get_accelerator_from_label_value(cls, value: str) -> str:
        """Given a label value, returns the GPU type"""
        raise NotImplementedError

    @classmethod
    def validate_label_value(cls, value: str) -> Tuple[bool, str]:
        """Validates if the specified label value is correct.

        Used to check if the labelling on the cluster is correct and
        preemptively raise an error if it is not.

        Returns:
            bool: True if the label value is valid, False otherwise.
            str: Error message if the label value is invalid, None otherwise.
        """
        del value
        return True, ''


def get_gke_accelerator_name(accelerator: str) -> str:
    """Returns the accelerator name for GKE clusters

    Uses the format - nvidia-tesla-<accelerator>.
    A100-80GB, H100-80GB and L4 are an exception. They use nvidia-<accelerator>.
    """
    if accelerator in ('A100-80GB', 'L4', 'H100-80GB'):
        # A100-80GB, L4 and H100-80GB have a different name pattern.
        return 'nvidia-{}'.format(accelerator.lower())
    else:
        return 'nvidia-tesla-{}'.format(accelerator.lower())


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

    @classmethod
    def get_accelerator_from_label_value(cls, value: str) -> str:
        return value.upper()

    @classmethod
    def validate_label_value(cls, value: str) -> Tuple[bool, str]:
        """Values must be all lowercase for the SkyPilot formatter."""
        is_valid = value == value.lower()
        return is_valid, (f'Label value {value!r} must be lowercase if using '
                          f'the {cls.get_label_key()} label.'
                          if not is_valid else '')


class CoreWeaveLabelFormatter(GPULabelFormatter):
    """CoreWeave label formatter

    Uses gpu.nvidia.com/class as the key, and the uppercase SkyPilot
    accelerator str as the value.
    """

    LABEL_KEY = 'gpu.nvidia.com/class'

    @classmethod
    def get_label_key(cls) -> str:
        return cls.LABEL_KEY

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        return accelerator.upper()

    @classmethod
    def get_accelerator_from_label_value(cls, value: str) -> str:
        return value


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

    @classmethod
    def get_accelerator_from_label_value(cls, value: str) -> str:
        if value.startswith('nvidia-tesla-'):
            return value.replace('nvidia-tesla-', '').upper()
        elif value.startswith('nvidia-'):
            return value.replace('nvidia-', '').upper()
        else:
            raise ValueError(
                f'Invalid accelerator name in GKE cluster: {value}')


class KarpenterLabelFormatter(SkyPilotLabelFormatter):
    """Karpeneter label formatter
    Karpenter uses the label `karpenter.k8s.aws/instance-gpu-name` to identify
    the GPU type. Details: https://karpenter.sh/docs/reference/instance-types/
    The naming scheme is same as the SkyPilot formatter, so we inherit from it.
    """
    LABEL_KEY = 'karpenter.k8s.aws/instance-gpu-name'


# LABEL_FORMATTER_REGISTRY stores the label formats SkyPilot will try to
# discover the accelerator type from. The order of the list is important, as
# it will be used to determine the priority of the label formats when
# auto-detecting the GPU label type.
LABEL_FORMATTER_REGISTRY = [
    SkyPilotLabelFormatter, CoreWeaveLabelFormatter, GKELabelFormatter,
    KarpenterLabelFormatter
]

# Mapping of autoscaler type to label formatter
AUTOSCALER_TO_LABEL_FORMATTER = {
    kubernetes_enums.KubernetesAutoscalerType.GKE: GKELabelFormatter,
    kubernetes_enums.KubernetesAutoscalerType.KARPENTER: KarpenterLabelFormatter,  # pylint: disable=line-too-long
    kubernetes_enums.KubernetesAutoscalerType.GENERIC: SkyPilotLabelFormatter,
}


def detect_gpu_label_formatter(
) -> Tuple[Optional[GPULabelFormatter], Dict[str, List[Tuple[str, str]]]]:
    """Detects the GPU label formatter for the Kubernetes cluster

    Returns:
        GPULabelFormatter: The GPU label formatter for the cluster, if found.
        Dict[str, List[Tuple[str, str]]]: A mapping of nodes and the list of
             labels on each node. E.g., {'node1': [('label1', 'value1')]}
    """
    # Get all labels across all nodes
    node_labels: Dict[str, List[Tuple[str, str]]] = {}
    nodes = get_kubernetes_nodes()
    for node in nodes:
        node_labels[node.metadata.name] = []
        for label, value in node.metadata.labels.items():
            node_labels[node.metadata.name].append((label, value))

    label_formatter = None

    # Check if the node labels contain any of the GPU label prefixes
    for lf in LABEL_FORMATTER_REGISTRY:
        label_key = lf.get_label_key()
        for _, label_list in node_labels.items():
            for label, _ in label_list:
                if label.startswith(label_key):
                    label_formatter = lf()
                    return label_formatter, node_labels

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


def check_instance_fits(instance: str) -> Tuple[bool, Optional[str]]:
    """Checks if the instance fits on the Kubernetes cluster.

    If the instance has GPU requirements, checks if the GPU type is
    available on the cluster and if enough CPU/memory is available on any node
    with the GPU type.

    Args:
        instance: str, the instance type to check.

    Returns:
        bool: True if the instance fits on the cluster, False otherwise.
        Optional[str]: Error message if the instance does not fit.
    """

    def check_cpu_mem_fits(candidate_instance_type: 'KubernetesInstanceType',
                           node_list: List[Any]) -> Tuple[bool, Optional[str]]:
        """Checks if the instance fits on the cluster based on CPU and memory.

        We check only capacity, not allocatable, because availability can
        change during scheduling, and we want to let the Kubernetes scheduler
        handle that.
        """
        # We log max CPU and memory found on the GPU nodes for debugging.
        max_cpu = 0.0
        max_mem = 0.0

        for node in node_list:
            node_cpus = parse_cpu_or_gpu_resource(node.status.capacity['cpu'])
            node_memory_gb = parse_memory_resource(
                node.status.capacity['memory'], unit='G')
            if node_cpus > max_cpu:
                max_cpu = node_cpus
                max_mem = node_memory_gb
            if (node_cpus >= candidate_instance_type.cpus and
                    node_memory_gb >= candidate_instance_type.memory):
                return True, None
        return False, (
            'Maximum resources found on a single node: '
            f'{max_cpu} CPUs, {common_utils.format_float(max_mem)}G Memory')

    nodes = get_kubernetes_nodes()
    k8s_instance_type = KubernetesInstanceType.\
        from_instance_type(instance)
    acc_type = k8s_instance_type.accelerator_type
    if acc_type is not None:
        # If GPUs are requested, check if GPU type is available, and if so,
        # check if CPU and memory requirements on the specific node are met.
        try:
            gpu_label_key, gpu_label_val = get_gpu_label_key_value(acc_type)
        except exceptions.ResourcesUnavailableError as e:
            # If GPU not found, return empty list and error message.
            return False, str(e)
        # Get the set of nodes that have the GPU type
        gpu_nodes = [
            node for node in nodes if gpu_label_key in node.metadata.labels and
            node.metadata.labels[gpu_label_key] == gpu_label_val
        ]
        assert len(gpu_nodes) > 0, 'GPU nodes not found'
        candidate_nodes = gpu_nodes
        not_fit_reason_prefix = (f'GPU nodes with {acc_type} do not have '
                                 'enough CPU and/or memory. ')
    else:
        candidate_nodes = nodes
        not_fit_reason_prefix = 'No nodes found with enough CPU and/or memory. '
    # Check if  CPU and memory requirements are met on at least one
    # candidate node.
    fits, reason = check_cpu_mem_fits(k8s_instance_type, candidate_nodes)
    if not fits:
        if reason is not None:
            reason = not_fit_reason_prefix + reason
        return fits, reason
    else:
        return fits, reason


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
    # TODO(romilb): Currently, we broadly disable all GPU checks if autoscaling
    #  is configured in config.yaml since the cluster may be scaling up from
    #  zero nodes and may not have any GPU nodes yet. In the future, we should
    #  support pollingthe clusters for autoscaling information, such as the
    #  node pools configured etc.

    autoscaler_type = get_autoscaler_type()
    if autoscaler_type is not None:
        # If autoscaler is set in config.yaml, override the label key and value
        # to the autoscaler's format and bypass the GPU checks.
        if check_mode:
            # If check mode is enabled and autoscaler is set, we can return
            # early since we assume the cluster autoscaler will handle GPU
            # node provisioning.
            return '', ''
        formatter = AUTOSCALER_TO_LABEL_FORMATTER.get(autoscaler_type)
        assert formatter is not None, ('Unsupported autoscaler type:'
                                       f' {autoscaler_type}')
        return formatter.get_label_key(), formatter.get_label_value(acc_type)

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
            # Validate the label value on all nodes labels to ensure they are
            # correctly setup and will behave as expected.
            for node_name, label_list in node_labels.items():
                for label, value in label_list:
                    if label == label_formatter.get_label_key():
                        is_valid, reason = label_formatter.validate_label_value(
                            value)
                        if not is_valid:
                            raise exceptions.ResourcesUnavailableError(
                                f'Node {node_name!r} in Kubernetes cluster has '
                                f'invalid GPU label: {label}={value}. {reason}')
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
            for node_name, label_list in node_labels.items():
                for label, value in label_list:
                    if (label == k8s_acc_label_key and
                            value == k8s_acc_label_value):
                        # If a node is found, we can break out of the loop
                        # and proceed to deploy.
                        return k8s_acc_label_key, k8s_acc_label_value
            # If no node is found with the requested acc_type, raise error
            with ux_utils.print_exception_no_traceback():
                suffix = ''
                if env_options.Options.SHOW_DEBUG_INFO.get():
                    all_labels = []
                    for node_name, label_list in node_labels.items():
                        all_labels.extend(label_list)
                    gpus_available = set(
                        v for k, v in all_labels if k == k8s_acc_label_key)
                    suffix = f' Available GPUs on the cluster: {gpus_available}'
                raise exceptions.ResourcesUnavailableError(
                    'Could not find any node in the Kubernetes cluster '
                    f'with {acc_type} GPU. Please ensure at least '
                    f'one node in the cluster has {acc_type} GPU and node '
                    'labels are setup correctly. '
                    f'Please refer to the documentation for more. {suffix}')
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
    svc_name = f'{cluster_name}-head-ssh'
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


def get_external_ip(
        network_mode: Optional[kubernetes_enums.KubernetesNetworkingMode]):
    if network_mode == kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD:
        return '127.0.0.1'
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

    # If we reach here, the credentials are valid and Kubernetes cluster is up.
    # We now do softer checks to check if exec based auth is used and to
    # see if the cluster is GPU-enabled.

    _, exec_msg = is_kubeconfig_exec_auth()

    # We now check if GPUs are available and labels are set correctly on the
    # cluster, and if not we return hints that may help debug any issues.
    # This early check avoids later surprises for user when they try to run
    # `sky launch --gpus <gpu>` and the optimizer does not list Kubernetes as a
    # provider if their cluster GPUs are not setup correctly.
    gpu_msg = ''
    try:
        _, _ = get_gpu_label_key_value(acc_type='', check_mode=True)
    except exceptions.ResourcesUnavailableError as e:
        # If GPUs are not available, we return cluster as enabled (since it can
        # be a CPU-only cluster) but we also return the exception message which
        # serves as a hint for how to enable GPU access.
        gpu_msg = str(e)
    if exec_msg and gpu_msg:
        return True, f'{gpu_msg}\n    Additionally, {exec_msg}'
    elif gpu_msg:
        return True, gpu_msg
    elif exec_msg:
        return True, exec_msg
    else:
        return True, None


def is_kubeconfig_exec_auth() -> Tuple[bool, Optional[str]]:
    """Checks if the kubeconfig file uses exec-based authentication

    Exec-based auth is commonly used for authenticating with cloud hosted
    Kubernetes services, such as GKE. Here is an example snippet from a
    kubeconfig using exec-based authentication for a GKE cluster:
    - name: mycluster
      user:
        exec:
          apiVersion: client.authentication.k8s.io/v1beta1
          command: /Users/romilb/google-cloud-sdk/bin/gke-gcloud-auth-plugin
          installHint: Install gke-gcloud-auth-plugin ...
          provideClusterInfo: true


    Using exec-based authentication is problematic when used in conjunction
    with kubernetes.remote_identity = LOCAL_CREDENTIAL in ~/.sky/config.yaml.
    This is because the exec-based authentication may not have the relevant
    dependencies installed on the remote cluster or may have hardcoded paths
    that are not available on the remote cluster.

    Returns:
        bool: True if exec-based authentication is used and LOCAL_CREDENTIAL
            mode is used for remote_identity in ~/.sky/config.yaml.
        str: Error message if exec-based authentication is used, None otherwise
    """
    k8s = kubernetes.kubernetes
    try:
        k8s.config.load_kube_config()
    except kubernetes.config_exception():
        # Using service account token or other auth methods, continue
        return False, None

    # Get active context and user from kubeconfig using k8s api
    _, current_context = k8s.config.list_kube_config_contexts()
    target_username = current_context['context']['user']

    # K8s api does not provide a mechanism to get the user details from the
    # context. We need to load the kubeconfig file and parse it to get the
    # user details.
    kubeconfig_path = os.path.expanduser(
        os.getenv('KUBECONFIG',
                  k8s.config.kube_config.KUBE_CONFIG_DEFAULT_LOCATION))
    # Load the kubeconfig file as a dictionary
    with open(kubeconfig_path, 'r', encoding='utf-8') as f:
        kubeconfig = yaml.safe_load(f)

    user_details = kubeconfig['users']

    # Find user matching the target username
    user_details = next(
        user for user in user_details if user['name'] == target_username)

    remote_identity = skypilot_config.get_nested(
        ('kubernetes', 'remote_identity'),
        schemas.get_default_remote_identity('kubernetes'))
    if ('exec' in user_details.get('user', {}) and remote_identity
            == schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value):
        ctx_name = current_context['name']
        exec_msg = ('exec-based authentication is used for '
                    f'Kubernetes context {ctx_name!r}.'
                    ' This may cause issues with autodown or when running '
                    'Managed Jobs or SkyServe controller on Kubernetes. '
                    'To fix, configure SkyPilot to create a service account '
                    'for running pods by setting the following in '
                    '~/.sky/config.yaml:\n'
                    '    kubernetes:\n'
                    '      remote_identity: SERVICE_ACCOUNT\n'
                    '    More: https://skypilot.readthedocs.io/en/latest/'
                    'reference/config.html')
        return True, exec_msg
    return False, None


def get_current_kube_config_context_name() -> Optional[str]:
    """Get the current kubernetes context from the kubeconfig file

    Returns:
        str | None: The current kubernetes context if it exists, None otherwise
    """
    k8s = kubernetes.kubernetes
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
    k8s = kubernetes.kubernetes
    try:
        _, current_context = k8s.config.list_kube_config_contexts()
        if 'namespace' in current_context['context']:
            return current_context['context']['namespace']
        else:
            return DEFAULT_NAMESPACE
    except k8s.config.config_exception.ConfigException:
        return DEFAULT_NAMESPACE


def parse_cpu_or_gpu_resource(resource_qty_str: str) -> Union[int, float]:
    resource_str = str(resource_qty_str)
    if resource_str[-1] == 'm':
        # For example, '500m' rounds up to 1.
        return math.ceil(int(resource_str[:-1]) / 1000)
    else:
        return float(resource_str)


def parse_memory_resource(resource_qty_str: str,
                          unit: str = 'B') -> Union[int, float]:
    """Returns memory size in chosen units given a resource quantity string."""
    if unit not in MEMORY_SIZE_UNITS:
        valid_units = ', '.join(MEMORY_SIZE_UNITS.keys())
        raise ValueError(
            f'Invalid unit: {unit}. Valid units are: {valid_units}')

    resource_str = str(resource_qty_str)
    bytes_value: Union[int, float]
    try:
        bytes_value = int(resource_str)
    except ValueError:
        memory_size = re.sub(r'([KMGTPB]+)', r' \1', resource_str)
        number, unit_index = [item.strip() for item in memory_size.split()]
        unit_index = unit_index[0]
        bytes_value = float(number) * MEMORY_SIZE_UNITS[unit_index]
    return bytes_value / MEMORY_SIZE_UNITS[unit]


class KubernetesInstanceType:
    """Class to represent the "Instance Type" in a Kubernetes.
    Since Kubernetes does not have a notion of instances, we generate
    virtual instance types that represent the resources requested by a
    pod ("node").
    This name captures the following resource requests:
        - CPU
        - Memory
        - Accelerators
    The name format is "{n}CPU--{k}GB" where n is the number of vCPUs and
    k is the amount of memory in GB. Accelerators can be specified by
    appending "--{a}{type}" where a is the number of accelerators and
    type is the accelerator type.
    CPU and memory can be specified as floats. Accelerator count must be int.
    Examples:
        - 4CPU--16GB
        - 0.5CPU--1.5GB
        - 4CPU--16GB--1V100
    """

    def __init__(self,
                 cpus: float,
                 memory: float,
                 accelerator_count: Optional[int] = None,
                 accelerator_type: Optional[str] = None):
        self.cpus = cpus
        self.memory = memory
        self.accelerator_count = accelerator_count
        self.accelerator_type = accelerator_type

    @property
    def name(self) -> str:
        """Returns the name of the instance."""
        assert self.cpus is not None
        assert self.memory is not None
        name = (f'{common_utils.format_float(self.cpus)}CPU--'
                f'{common_utils.format_float(self.memory)}GB')
        if self.accelerator_count:
            name += f'--{self.accelerator_count}{self.accelerator_type}'
        return name

    @staticmethod
    def is_valid_instance_type(name: str) -> bool:
        """Returns whether the given name is a valid instance type."""
        pattern = re.compile(r'^(\d+(\.\d+)?CPU--\d+(\.\d+)?GB)(--\d+\S+)?$')
        return bool(pattern.match(name))

    @classmethod
    def _parse_instance_type(
            cls,
            name: str) -> Tuple[float, float, Optional[int], Optional[str]]:
        """Parses and returns resources from the given InstanceType name
        Returns:
            cpus | float: Number of CPUs
            memory | float: Amount of memory in GB
            accelerator_count | float: Number of accelerators
            accelerator_type | str: Type of accelerator
        """
        pattern = re.compile(
            r'^(?P<cpus>\d+(\.\d+)?)CPU--(?P<memory>\d+(\.\d+)?)GB(?:--(?P<accelerator_count>\d+)(?P<accelerator_type>\S+))?$'  # pylint: disable=line-too-long
        )
        match = pattern.match(name)
        if match:
            cpus = float(match.group('cpus'))
            memory = float(match.group('memory'))
            accelerator_count = match.group('accelerator_count')
            accelerator_type = match.group('accelerator_type')
            if accelerator_count:
                accelerator_count = int(accelerator_count)
                accelerator_type = str(accelerator_type)
            else:
                accelerator_count = None
                accelerator_type = None
            return cpus, memory, accelerator_count, accelerator_type
        else:
            raise ValueError(f'Invalid instance name: {name}')

    @classmethod
    def from_instance_type(cls, name: str) -> 'KubernetesInstanceType':
        """Returns an instance name object from the given name."""
        if not cls.is_valid_instance_type(name):
            raise ValueError(f'Invalid instance name: {name}')
        cpus, memory, accelerator_count, accelerator_type = \
            cls._parse_instance_type(name)
        return cls(cpus=cpus,
                   memory=memory,
                   accelerator_count=accelerator_count,
                   accelerator_type=accelerator_type)

    @classmethod
    def from_resources(cls,
                       cpus: float,
                       memory: float,
                       accelerator_count: Union[float, int] = 0,
                       accelerator_type: str = '') -> 'KubernetesInstanceType':
        """Returns an instance name object from the given resources.
        If accelerator_count is not an int, it will be rounded up since GPU
        requests in Kubernetes must be int.
        """
        name = f'{cpus}CPU--{memory}GB'
        # Round up accelerator_count if it is not an int.
        accelerator_count = math.ceil(accelerator_count)
        if accelerator_count > 0:
            name += f'--{accelerator_count}{accelerator_type}'
        return cls(cpus=cpus,
                   memory=memory,
                   accelerator_count=accelerator_count,
                   accelerator_type=accelerator_type)

    def __str__(self):
        return self.name


def construct_ssh_jump_command(private_key_path: str,
                               ssh_jump_ip: str,
                               ssh_jump_port: Optional[int] = None,
                               proxy_cmd_path: Optional[str] = None) -> str:
    ssh_jump_proxy_command = (f'ssh -tt -i {private_key_path} '
                              '-o StrictHostKeyChecking=no '
                              '-o UserKnownHostsFile=/dev/null '
                              f'-o IdentitiesOnly=yes '
                              f'-W %h:%p sky@{ssh_jump_ip}')
    if ssh_jump_port is not None:
        ssh_jump_proxy_command += f' -p {ssh_jump_port} '
    if proxy_cmd_path is not None:
        proxy_cmd_path = os.path.expanduser(proxy_cmd_path)
        # adding execution permission to the proxy command script
        os.chmod(proxy_cmd_path, os.stat(proxy_cmd_path).st_mode | 0o111)
        ssh_jump_proxy_command += f' -o ProxyCommand=\'{proxy_cmd_path}\' '
    return ssh_jump_proxy_command


def get_ssh_proxy_command(
        private_key_path: str, ssh_jump_name: str,
        network_mode: kubernetes_enums.KubernetesNetworkingMode, namespace: str,
        port_fwd_proxy_cmd_path: str, port_fwd_proxy_cmd_template: str) -> str:
    """Generates the SSH proxy command to connect through the SSH jump pod.

    By default, establishing an SSH connection creates a communication
    channel to a remote node by setting up a TCP connection. When a
    ProxyCommand is specified, this default behavior is overridden. The command
    specified in ProxyCommand is executed, and its standard input and output
    become the communication channel for the SSH session.

    Pods within a Kubernetes cluster have internal IP addresses that are
    typically not accessible from outside the cluster. Since the default TCP
    connection of SSH won't allow access to these pods, we employ a
    ProxyCommand to establish the required communication channel. We offer this
    in two different networking options: NodePort/port-forward.

    With the NodePort networking mode, a NodePort service is launched. This
    service opens an external port on the node which redirects to the desired
    port within the pod. When establishing an SSH session in this mode, the
    ProxyCommand makes use of this external port to create a communication
    channel directly to port 22, which is the default port ssh server listens
    on, of the jump pod.

    With Port-forward mode, instead of directly exposing an external port,
    'kubectl port-forward' sets up a tunnel between a local port
    (127.0.0.1:23100) and port 22 of the jump pod. Then we establish a TCP
    connection to the local end of this tunnel, 127.0.0.1:23100, using 'socat'.
    This is setup in the inner ProxyCommand of the nested ProxyCommand, and the
    rest is the same as NodePort approach, which the outer ProxyCommand
    establishes a communication channel between 127.0.0.1:23100 and port 22 on
    the jump pod. Consequently, any stdin provided on the local machine is
    forwarded through this tunnel to the application (SSH server) listening in
    the pod. Similarly, any output from the application in the pod is tunneled
    back and displayed in the terminal on the local machine.

    Args:
        private_key_path: str; Path to the private key to use for SSH.
            This key must be authorized to access the SSH jump pod.
        ssh_jump_name: str; Name of the SSH jump service to use
        network_mode: KubernetesNetworkingMode; networking mode for ssh
            session. It is either 'NODEPORT' or 'PORTFORWARD'
        namespace: Kubernetes namespace to use
        port_fwd_proxy_cmd_path: str; path to the script used as Proxycommand
            with 'kubectl port-forward'
        port_fwd_proxy_cmd_template: str; template used to create
            'kubectl port-forward' Proxycommand
    """
    # Fetch IP to connect to for the jump svc
    ssh_jump_ip = get_external_ip(network_mode)
    if network_mode == kubernetes_enums.KubernetesNetworkingMode.NODEPORT:
        ssh_jump_port = get_port(ssh_jump_name, namespace)
        ssh_jump_proxy_command = construct_ssh_jump_command(
            private_key_path, ssh_jump_ip, ssh_jump_port=ssh_jump_port)
    # Setting kubectl port-forward/socat to establish ssh session using
    # ClusterIP service to disallow any ports opened
    else:
        vars_to_fill = {
            'ssh_jump_name': ssh_jump_name,
        }
        common_utils.fill_template(port_fwd_proxy_cmd_template,
                                   vars_to_fill,
                                   output_path=port_fwd_proxy_cmd_path)
        ssh_jump_proxy_command = construct_ssh_jump_command(
            private_key_path,
            ssh_jump_ip,
            proxy_cmd_path=port_fwd_proxy_cmd_path)
    return ssh_jump_proxy_command


def setup_ssh_jump_svc(ssh_jump_name: str, namespace: str,
                       service_type: kubernetes_enums.KubernetesServiceType):
    """Sets up Kubernetes service resource to access for SSH jump pod.

    This method acts as a necessary complement to be run along with
    setup_ssh_jump_pod(...) method. This service ensures the pod is accessible.

    Args:
        ssh_jump_name: Name to use for the SSH jump service
        namespace: Namespace to create the SSH jump service in
        service_type: Networking configuration on either to use NodePort
            or ClusterIP service to ssh in
    """
    # Fill in template - ssh_key_secret and ssh_jump_image are not required for
    # the service spec, so we pass in empty strs.
    content = fill_ssh_jump_template('', '', ssh_jump_name, service_type.value)

    # Add custom metadata from config
    merge_custom_metadata(content['service_spec']['metadata'])

    # Create service
    try:
        kubernetes.core_api().create_namespaced_service(namespace,
                                                        content['service_spec'])
    except kubernetes.api_exception() as e:
        # SSH Jump Pod service already exists.
        if e.status == 409:
            ssh_jump_service = kubernetes.core_api().read_namespaced_service(
                name=ssh_jump_name, namespace=namespace)
            curr_svc_type = ssh_jump_service.spec.type
            if service_type.value == curr_svc_type:
                # If the currently existing SSH Jump service's type is identical
                # to user's configuration for networking mode
                logger.debug(
                    f'SSH Jump Service {ssh_jump_name} already exists in the '
                    'cluster, using it.')
            else:
                # If a different type of service type for SSH Jump pod compared
                # to user's configuration for networking mode exists, we remove
                # existing servie to create a new one following user's config
                kubernetes.core_api().delete_namespaced_service(
                    name=ssh_jump_name, namespace=namespace)
                kubernetes.core_api().create_namespaced_service(
                    namespace, content['service_spec'])
                port_forward_mode = (
                    kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD.value)
                nodeport_mode = (
                    kubernetes_enums.KubernetesNetworkingMode.NODEPORT.value)
                clusterip_svc = (
                    kubernetes_enums.KubernetesServiceType.CLUSTERIP.value)
                nodeport_svc = (
                    kubernetes_enums.KubernetesServiceType.NODEPORT.value)
                curr_network_mode = port_forward_mode \
                    if curr_svc_type == clusterip_svc else nodeport_mode
                new_network_mode = nodeport_mode \
                    if curr_svc_type == clusterip_svc else port_forward_mode
                new_svc_type = nodeport_svc \
                    if curr_svc_type == clusterip_svc else clusterip_svc
                logger.info(
                    f'Switching the networking mode from '
                    f'\'{curr_network_mode}\' to \'{new_network_mode}\' '
                    f'following networking configuration. Deleting existing '
                    f'\'{curr_svc_type}\' service and recreating as '
                    f'\'{new_svc_type}\' service.')
        else:
            raise
    else:
        logger.info(f'Created SSH Jump Service {ssh_jump_name}.')


def setup_ssh_jump_pod(ssh_jump_name: str, ssh_jump_image: str,
                       ssh_key_secret: str, namespace: str):
    """Sets up Kubernetes RBAC and pod for SSH jump host.

    Our Kubernetes implementation uses a SSH jump pod to reach SkyPilot clusters
    running inside a cluster. This function sets up the resources needed for
    the SSH jump pod. This includes a service account which grants the jump pod
    permission to watch for other SkyPilot pods and terminate itself if there
    are no SkyPilot pods running.

    setup_ssh_jump_service must also be run to ensure that the SSH jump pod is
    reachable.

    Args:
        ssh_jump_image: Container image to use for the SSH jump pod
        ssh_jump_name: Name to use for the SSH jump pod
        ssh_key_secret: Secret name for the SSH key stored in the cluster
        namespace: Namespace to create the SSH jump pod in
    """
    # Fill in template - service is created separately so service_type is not
    # required, so we pass in empty str.
    content = fill_ssh_jump_template(ssh_key_secret, ssh_jump_image,
                                     ssh_jump_name, '')

    # Add custom metadata to all objects
    for object_type in content.keys():
        merge_custom_metadata(content[object_type]['metadata'])

    # ServiceAccount
    try:
        kubernetes.core_api().create_namespaced_service_account(
            namespace, content['service_account'])
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.info(
                'SSH Jump ServiceAccount already exists in the cluster, using '
                'it.')
        else:
            raise
    else:
        logger.info('Created SSH Jump ServiceAccount.')
    # Role
    try:
        kubernetes.auth_api().create_namespaced_role(namespace, content['role'])
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.info(
                'SSH Jump Role already exists in the cluster, using it.')
        else:
            raise
    else:
        logger.info('Created SSH Jump Role.')
    # RoleBinding
    try:
        kubernetes.auth_api().create_namespaced_role_binding(
            namespace, content['role_binding'])
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.info(
                'SSH Jump RoleBinding already exists in the cluster, using '
                'it.')
        else:
            raise
    else:
        logger.info('Created SSH Jump RoleBinding.')
    # Pod
    try:
        kubernetes.core_api().create_namespaced_pod(namespace,
                                                    content['pod_spec'])
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.info(
                f'SSH Jump Host {ssh_jump_name} already exists in the cluster, '
                'using it.')
        else:
            raise
    else:
        logger.info(f'Created SSH Jump Host {ssh_jump_name}.')


def clean_zombie_ssh_jump_pod(namespace: str, node_id: str):
    """Analyzes SSH jump pod and removes if it is in a bad state

    Prevents the existence of a dangling SSH jump pod. This could happen
    in case the pod main container did not start properly (or failed). In that
    case, jump pod lifecycle manager will not function properly to
    remove the pod and service automatically, and must be done manually.

    Args:
        namespace: Namespace to remove the SSH jump pod and service from
        node_id: Name of head pod
    """

    def find(l, predicate):
        """Utility function to find element in given list"""
        results = [x for x in l if predicate(x)]
        return results[0] if len(results) > 0 else None

    # Get the SSH jump pod name from the head pod
    try:
        pod = kubernetes.core_api().read_namespaced_pod(node_id, namespace)
    except kubernetes.api_exception() as e:
        if e.status == 404:
            logger.warning(f'Failed to get pod {node_id},'
                           ' but the pod was not found (404).')
        raise
    else:
        ssh_jump_name = pod.metadata.labels.get('skypilot-ssh-jump')
    try:
        ssh_jump_pod = kubernetes.core_api().read_namespaced_pod(
            ssh_jump_name, namespace)
        cont_ready_cond = find(ssh_jump_pod.status.conditions,
                               lambda c: c.type == 'ContainersReady')
        if (cont_ready_cond and cont_ready_cond.status
                == 'False') or ssh_jump_pod.status.phase == 'Pending':
            # Either the main container is not ready or the pod failed
            # to schedule. To be on the safe side and prevent a dangling
            # ssh jump pod, lets remove it and the service. Otherwise, main
            # container is ready and its lifecycle management script takes
            # care of the cleaning.
            kubernetes.core_api().delete_namespaced_pod(ssh_jump_name,
                                                        namespace)
            kubernetes.core_api().delete_namespaced_service(
                ssh_jump_name, namespace)
    except kubernetes.api_exception() as e:
        # We keep the warning in debug to avoid polluting the `sky launch`
        # output.
        logger.debug(f'Tried to check ssh jump pod {ssh_jump_name},'
                     f' but got error {e}\n. Consider running `kubectl '
                     f'delete pod {ssh_jump_name} -n {namespace}` to manually '
                     'remove the pod if it has crashed.')
        # We encountered an issue while checking ssh jump pod. To be on
        # the safe side, lets remove its service so the port is freed
        try:
            kubernetes.core_api().delete_namespaced_service(
                ssh_jump_name, namespace)
        except kubernetes.api_exception():
            pass


def fill_ssh_jump_template(ssh_key_secret: str, ssh_jump_image: str,
                           ssh_jump_name: str, service_type: str) -> Dict:
    template_path = os.path.join(sky.__root_dir__, 'templates',
                                 'kubernetes-ssh-jump.yml.j2')
    if not os.path.exists(template_path):
        raise FileNotFoundError(
            'Template "kubernetes-ssh-jump.j2" does not exist.')
    with open(template_path, 'r', encoding='utf-8') as fin:
        template = fin.read()
    j2_template = jinja2.Template(template)
    cont = j2_template.render(name=ssh_jump_name,
                              image=ssh_jump_image,
                              secret=ssh_key_secret,
                              service_type=service_type)
    content = yaml.safe_load(cont)
    return content


def check_port_forward_mode_dependencies() -> None:
    """Checks if 'socat' and 'nc' are installed"""

    # Construct runtime errors
    socat_default_error = RuntimeError(
        f'`socat` is required to setup Kubernetes cloud with '
        f'`{kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD.value}` '  # pylint: disable=line-too-long
        'default networking mode and it is not installed. '
        'On Debian/Ubuntu, install it with:\n'
        f'  $ sudo apt install socat\n'
        f'On MacOS, install it with: \n'
        f'  $ brew install socat')
    netcat_default_error = RuntimeError(
        f'`nc` is required to setup Kubernetes cloud with '
        f'`{kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD.value}` '  # pylint: disable=line-too-long
        'default networking mode and it is not installed. '
        'On Debian/Ubuntu, install it with:\n'
        f'  $ sudo apt install netcat\n'
        f'On MacOS, install it with: \n'
        f'  $ brew install netcat')
    mac_installed_error = RuntimeError(
        f'The default MacOS `nc` is installed. However, for '
        f'`{kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD.value}` '  # pylint: disable=line-too-long
        'default networking mode, GNU netcat is required. '
        f'On MacOS, install it with: \n'
        f'  $ brew install netcat')

    # Ensure socat is installed
    try:
        subprocess.run(['socat', '-V'],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        with ux_utils.print_exception_no_traceback():
            raise socat_default_error from None

    # Ensure netcat is installed
    #
    # In some cases, the user may have the default MacOS nc installed, which
    # does not support the -z flag. To use the -z flag for port scanning,
    # they need GNU nc installed. We check for this case and raise an error.
    try:
        netcat_output = subprocess.run(['nc', '-h'],
                                       capture_output=True,
                                       check=False)
        nc_mac_installed = netcat_output.returncode == 1 and 'apple' in str(
            netcat_output.stderr)

        if nc_mac_installed:
            with ux_utils.print_exception_no_traceback():
                raise mac_installed_error from None
        elif netcat_output.returncode != 0:
            with ux_utils.print_exception_no_traceback():
                raise netcat_default_error from None

    except FileNotFoundError:
        with ux_utils.print_exception_no_traceback():
            raise netcat_default_error from None


def get_endpoint_debug_message() -> str:
    """ Returns a string message for user to debug Kubernetes port opening

    Polls the configured ports mode on Kubernetes to produce an
    appropriate error message with debugging hints.

    Also checks if the
    """
    port_mode = network_utils.get_port_mode()
    if port_mode == kubernetes_enums.KubernetesPortMode.INGRESS:
        endpoint_type = 'Ingress'
        debug_cmd = 'kubectl describe ingress && kubectl describe ingressclass'
    elif port_mode == kubernetes_enums.KubernetesPortMode.LOADBALANCER:
        endpoint_type = 'LoadBalancer'
        debug_cmd = 'kubectl describe service'
    elif port_mode == kubernetes_enums.KubernetesPortMode.PODIP:
        endpoint_type = 'PodIP'
        debug_cmd = 'kubectl describe pod'
    return ENDPOINTS_DEBUG_MESSAGE.format(endpoint_type=endpoint_type,
                                          debug_cmd=debug_cmd)


def merge_dicts(source: Dict[Any, Any], destination: Dict[Any, Any]):
    """Merge two dictionaries into the destination dictionary.

    Updates nested dictionaries instead of replacing them.
    If a list is encountered, it will be appended to the destination list.

    An exception is when the key is 'containers', in which case the
    first container in the list will be fetched and merge_dict will be
    called on it with the first container in the destination list.
    """
    for key, value in source.items():
        if isinstance(value, dict) and key in destination:
            merge_dicts(value, destination[key])
        elif isinstance(value, list) and key in destination:
            assert isinstance(destination[key], list), \
                f'Expected {key} to be a list, found {destination[key]}'
            if key == 'containers':
                # If the key is 'containers', we take the first and only
                # container in the list and merge it.
                assert len(value) == 1, \
                    f'Expected only one container, found {value}'
                merge_dicts(value[0], destination[key][0])
            elif key in ['volumes', 'volumeMounts']:
                # If the key is 'volumes' or 'volumeMounts', we search for
                # item with the same name and merge it.
                for new_volume in value:
                    new_volume_name = new_volume.get('name')
                    if new_volume_name is not None:
                        destination_volume = next(
                            (v for v in destination[key]
                             if v.get('name') == new_volume_name), None)
                        if destination_volume is not None:
                            merge_dicts(new_volume, destination_volume)
                        else:
                            destination[key].append(new_volume)
            else:
                destination[key].extend(value)
        else:
            destination[key] = value


def combine_pod_config_fields(cluster_yaml_path: str) -> None:
    """Adds or updates fields in the YAML with fields from the ~/.sky/config's
    kubernetes.pod_spec dict.
    This can be used to add fields to the YAML that are not supported by
    SkyPilot yet, or require simple configuration (e.g., adding an
    imagePullSecrets field).
    Note that new fields are added and existing ones are updated. Nested fields
    are not completely replaced, instead their objects are merged. Similarly,
    if a list is encountered in the config, it will be appended to the
    destination list.
    For example, if the YAML has the following:
        ```
        ...
        node_config:
            spec:
                containers:
                    - name: ray
                    image: rayproject/ray:nightly
        ```
    and the config has the following:
        ```
        kubernetes:
            pod_config:
                spec:
                    imagePullSecrets:
                        - name: my-secret
        ```
    then the resulting YAML will be:
        ```
        ...
        node_config:
            spec:
                containers:
                    - name: ray
                    image: rayproject/ray:nightly
                imagePullSecrets:
                    - name: my-secret
        ```
    """
    with open(cluster_yaml_path, 'r', encoding='utf-8') as f:
        yaml_content = f.read()
    yaml_obj = yaml.safe_load(yaml_content)
    kubernetes_config = skypilot_config.get_nested(('kubernetes', 'pod_config'),
                                                   {})

    # Merge the kubernetes config into the YAML for both head and worker nodes.
    merge_dicts(
        kubernetes_config,
        yaml_obj['available_node_types']['ray_head_default']['node_config'])

    # Write the updated YAML back to the file
    common_utils.dump_yaml(cluster_yaml_path, yaml_obj)


def combine_metadata_fields(cluster_yaml_path: str) -> None:
    """Updates the metadata for all Kubernetes objects created by SkyPilot with
    fields from the ~/.sky/config's kubernetes.custom_metadata dict.

    Obeys the same add or update semantics as combine_pod_config_fields().
    """

    with open(cluster_yaml_path, 'r', encoding='utf-8') as f:
        yaml_content = f.read()
    yaml_obj = yaml.safe_load(yaml_content)
    custom_metadata = skypilot_config.get_nested(
        ('kubernetes', 'custom_metadata'), {})

    # List of objects in the cluster YAML to be updated
    combination_destinations = [
        # Service accounts
        yaml_obj['provider']['autoscaler_service_account']['metadata'],
        yaml_obj['provider']['autoscaler_role']['metadata'],
        yaml_obj['provider']['autoscaler_role_binding']['metadata'],
        yaml_obj['provider']['autoscaler_service_account']['metadata'],
        # Pod spec
        yaml_obj['available_node_types']['ray_head_default']['node_config']
        ['metadata'],
        # Services for pods
        *[svc['metadata'] for svc in yaml_obj['provider']['services']]
    ]

    for destination in combination_destinations:
        merge_dicts(custom_metadata, destination)

    # Write the updated YAML back to the file
    common_utils.dump_yaml(cluster_yaml_path, yaml_obj)


def merge_custom_metadata(original_metadata: Dict[str, Any]) -> None:
    """Merges original metadata with custom_metadata from config

    Merge is done in-place, so return is not required
    """
    custom_metadata = skypilot_config.get_nested(
        ('kubernetes', 'custom_metadata'), {})
    merge_dicts(custom_metadata, original_metadata)


def check_nvidia_runtime_class() -> bool:
    """Checks if the 'nvidia' RuntimeClass exists in the cluster"""
    # Fetch the list of available RuntimeClasses
    runtime_classes = kubernetes.node_api().list_runtime_class()

    # Check if 'nvidia' RuntimeClass exists
    nvidia_exists = any(
        rc.metadata.name == 'nvidia' for rc in runtime_classes.items)
    return nvidia_exists


def check_secret_exists(secret_name: str, namespace: str) -> bool:
    """Checks if a secret exists in a namespace

    Args:
        secret_name: Name of secret to check
        namespace: Namespace to check
    """

    try:
        kubernetes.core_api().read_namespaced_secret(
            secret_name, namespace, _request_timeout=kubernetes.API_TIMEOUT)
    except kubernetes.api_exception() as e:
        if e.status == 404:
            return False
        raise
    else:
        return True


def create_namespace(namespace: str) -> None:
    """Creates a namespace in the cluster.

    If the namespace already exists, logs a message and does nothing.

    Args:
        namespace: Name of the namespace to create
    """
    kubernetes_client = kubernetes.kubernetes.client
    ns_metadata = dict(name=namespace, labels={'parent': 'skypilot'})
    merge_custom_metadata(ns_metadata)
    namespace_obj = kubernetes_client.V1Namespace(metadata=ns_metadata)
    try:
        kubernetes.core_api().create_namespace(namespace_obj)
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.info(f'Namespace {namespace} already exists in the cluster.')
        else:
            raise


def get_head_pod_name(cluster_name_on_cloud: str):
    """Returns the pod name of the head pod for the given cluster name on cloud

    Args:
        cluster_name_on_cloud: Name of the cluster on cloud

    Returns:
        str: Pod name of the head pod
    """
    # We could have iterated over all pods in the namespace and checked for the
    # label, but since we know the naming convention, we can directly return the
    # head pod name.
    return f'{cluster_name_on_cloud}-head'


def get_autoscaler_type(
) -> Optional[kubernetes_enums.KubernetesAutoscalerType]:
    """Returns the autoscaler type by reading from config"""
    autoscaler_type = skypilot_config.get_nested(['kubernetes', 'autoscaler'],
                                                 None)
    if autoscaler_type is not None:
        autoscaler_type = kubernetes_enums.KubernetesAutoscalerType(
            autoscaler_type)
    return autoscaler_type


def dict_to_k8s_object(object_dict: Dict[str, Any], object_type: 'str') -> Any:
    """Converts a dictionary to a Kubernetes object.

    Useful for comparing two Kubernetes objects. Adapted from
    https://github.com/kubernetes-client/python/issues/977#issuecomment-592030030  # pylint: disable=line-too-long

    Args:
        object_dict: Dictionary representing the Kubernetes object
        object_type: Type of the Kubernetes object. E.g., 'V1Pod', 'V1Service'.
    """

    class FakeKubeResponse:

        def __init__(self, obj):
            self.data = json.dumps(obj)

    fake_kube_response = FakeKubeResponse(object_dict)
    return kubernetes.api_client().deserialize(fake_kube_response, object_type)
