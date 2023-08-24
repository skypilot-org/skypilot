"""Kubernetes utilities for SkyPilot."""
import math
import re
from typing import Any, List, Optional, Set, Tuple, Union

from sky import exceptions
from sky.adaptors import kubernetes
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import ux_utils

DEFAULT_NAMESPACE = 'default'

MEMORY_SIZE_UNITS = {
    'B': 1,
    'K': 2**10,
    'M': 2**20,
    'G': 2**30,
    'T': 2**40,
    'P': 2**50,
}


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
