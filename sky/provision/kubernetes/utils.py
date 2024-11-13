"""Kubernetes utilities for SkyPilot."""
import dataclasses
import functools
import json
import math
import os
import re
import shutil
import subprocess
import typing
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlparse

import jinja2
import yaml

import sky
from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky import status_lib
from sky.adaptors import kubernetes
from sky.provision import constants as provision_constants
from sky.provision.kubernetes import network_utils
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import kubernetes_enums
from sky.utils import schemas
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import resources as resources_lib

# TODO(romilb): Move constants to constants.py
DEFAULT_NAMESPACE = 'default'
IN_CLUSTER_REGION = 'in-cluster'

DEFAULT_SERVICE_ACCOUNT_NAME = 'skypilot-service-account'

MEMORY_SIZE_UNITS = {
    'B': 1,
    'K': 2**10,
    'M': 2**20,
    'G': 2**30,
    'T': 2**40,
    'P': 2**50,
}

# The resource keys used by Kubernetes to track NVIDIA GPUs and Google TPUs on
# nodes. These keys are typically used in the node's status.allocatable
# or status.capacity fields to indicate the available resources on the node.
GPU_RESOURCE_KEY = 'nvidia.com/gpu'
TPU_RESOURCE_KEY = 'google.com/tpu'

NO_ACCELERATOR_HELP_MESSAGE = (
    'If your cluster contains GPUs or TPUs, make sure '
    f'{GPU_RESOURCE_KEY} or {TPU_RESOURCE_KEY} resource is available '
    'on the nodes and the node labels for identifying GPUs/TPUs '
    '(e.g., skypilot.co/accelerator) are setup correctly. ')

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

# Port-forward proxy command constants
PORT_FORWARD_PROXY_CMD_TEMPLATE = 'kubernetes-port-forward-proxy-command.sh'
# We add a version suffix to the port-forward proxy command to ensure backward
# compatibility and avoid overwriting the older version.
PORT_FORWARD_PROXY_CMD_VERSION = 2
PORT_FORWARD_PROXY_CMD_PATH = ('~/.sky/kubernetes-port-forward-proxy-command-'
                               f'v{PORT_FORWARD_PROXY_CMD_VERSION}.sh')

# Mapping used to get generation for TPU accelerator name.
# https://cloud.google.com/kubernetes-engine/docs/how-to/tpus#run
GKE_TPU_ACCELERATOR_TO_GENERATION = {
    'tpu-v4-podslice': 'v4',
    # Only Single-host v5e TPU configurations are allowed.
    'tpu-v5-lite-device': 'v5e',
    # Multi-host compatible v5e TPU configurations allowed.
    'tpu-v5-lite-podslice': 'v5e',
    'tpu-v5p-slice': 'v5p',
}

POD_STATUSES = {
    'Pending', 'Running', 'Succeeded', 'Failed', 'Unknown', 'Terminating'
}
AUTODOWN_ANNOTATION_KEY = 'skypilot.co/autodown'
IDLE_MINUTES_TO_AUTOSTOP_ANNOTATION_KEY = (
    'skypilot.co/idle_minutes_to_autostop')
ANNOTATIONS_POD_NOT_FOUND_ERROR_MSG = ('Pod {pod_name} not found in namespace '
                                       '{namespace} while trying to {action} '
                                       'an annotation {annotation}.')

logger = sky_logging.init_logger(__name__)


class GPULabelFormatter:
    """Base class to define a GPU label formatter for a Kubernetes cluster

    A GPU label formatter is a class that defines how to use GPU type labels in
    a Kubernetes cluster. It is used by the Kubernetes cloud class to pick the
    key:value pair to use as node selector for GPU nodes.
    """

    @classmethod
    def get_label_key(cls, accelerator: Optional[str] = None) -> str:
        """Returns the label key for GPU type used by the Kubernetes cluster"""
        raise NotImplementedError

    @classmethod
    def get_label_keys(cls) -> List[str]:
        """Returns a list of label keys for GPU used by Kubernetes cluster."""
        raise NotImplementedError

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        """Given a GPU type, returns the label value to be used"""
        raise NotImplementedError

    @classmethod
    def match_label_key(cls, label_key: str) -> bool:
        """Checks if the given label key matches the formatter's label keys"""
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
    """Returns the accelerator name for GKE clusters.

    Uses the format - nvidia-tesla-<accelerator>.
    A100-80GB, H100-80GB, L4 are an exception. They use nvidia-<accelerator>.
    TPU types are an exception as well keeping the given name.
    """
    if accelerator == 'H100':
        # H100 is named as H100-80GB in GKE.
        accelerator = 'H100-80GB'
    if accelerator in ('A100-80GB', 'L4', 'H100-80GB', 'H100-MEGA-80GB'):
        # A100-80GB, L4, H100-80GB and H100-MEGA-80GB
        # have a different name pattern.
        return 'nvidia-{}'.format(accelerator.lower())
    elif accelerator.startswith('tpu-'):
        return accelerator
    else:
        return 'nvidia-tesla-{}'.format(accelerator.lower())


class SkyPilotLabelFormatter(GPULabelFormatter):
    """Custom label formatter for SkyPilot

    Uses skypilot.co/accelerator as the key, and SkyPilot accelerator str as the
    value.
    """

    LABEL_KEY = 'skypilot.co/accelerator'

    @classmethod
    def get_label_key(cls, accelerator: Optional[str] = None) -> str:
        return cls.LABEL_KEY

    @classmethod
    def get_label_keys(cls) -> List[str]:
        return [cls.LABEL_KEY]

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        # For SkyPilot formatter, we use the accelerator str directly.
        # See sky.utils.kubernetes.gpu_labeler.
        return accelerator.lower()

    @classmethod
    def match_label_key(cls, label_key: str) -> bool:
        return label_key == cls.LABEL_KEY

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
    def get_label_key(cls, accelerator: Optional[str] = None) -> str:
        return cls.LABEL_KEY

    @classmethod
    def get_label_keys(cls) -> List[str]:
        return [cls.LABEL_KEY]

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        return accelerator.upper()

    @classmethod
    def match_label_key(cls, label_key: str) -> bool:
        return label_key == cls.LABEL_KEY

    @classmethod
    def get_accelerator_from_label_value(cls, value: str) -> str:
        return value


class GKELabelFormatter(GPULabelFormatter):
    """GKE label formatter

    GKE nodes by default are populated with `cloud.google.com/gke-accelerator`
    label, which is used to identify the GPU type.
    """

    GPU_LABEL_KEY = 'cloud.google.com/gke-accelerator'
    TPU_LABEL_KEY = 'cloud.google.com/gke-tpu-accelerator'
    ACCELERATOR_COUNT_LABEL_KEY = 'cloud.google.com/gke-accelerator-count'
    TPU_TOPOLOGY_LABEL_KEY = 'cloud.google.com/gke-tpu-topology'

    @classmethod
    def get_label_key(cls, accelerator: Optional[str] = None) -> str:
        if accelerator is not None and accelerator.startswith('tpu-'):
            return cls.TPU_LABEL_KEY
        return cls.GPU_LABEL_KEY

    @classmethod
    def get_label_keys(cls) -> List[str]:
        return [cls.GPU_LABEL_KEY, cls.TPU_LABEL_KEY]

    @classmethod
    def match_label_key(cls, label_key: str) -> bool:
        return label_key in cls.get_label_keys()

    @classmethod
    def get_tpu_topology_label_key(cls) -> str:
        return cls.TPU_TOPOLOGY_LABEL_KEY

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        return get_gke_accelerator_name(accelerator)

    @classmethod
    def get_accelerator_from_label_value(cls, value: str) -> str:
        if value.startswith('nvidia-tesla-'):
            return value.replace('nvidia-tesla-', '').upper()
        elif value.startswith('nvidia-'):
            acc = value.replace('nvidia-', '').upper()
            if acc == 'H100-80GB':
                # H100 can be either H100-80GB or H100-MEGA-80GB in GKE
                # we map H100 ---> H100-80GB and keep H100-MEGA-80GB
                # to distinguish between a3-high and a3-mega instances
                return 'H100'
            return acc
        elif is_tpu_on_gke(value):
            return value
        else:
            raise ValueError(
                f'Invalid accelerator name in GKE cluster: {value}')


class GFDLabelFormatter(GPULabelFormatter):
    """GPU Feature Discovery label formatter

    NVIDIA GPUs nodes are labeled by GPU feature discovery
    e.g. nvidia.com/gpu.product=NVIDIA-H100-80GB-HBM3
    https://github.com/NVIDIA/gpu-feature-discovery

    GPU feature discovery is included as part of the
    NVIDIA GPU Operator:
    https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/overview.html

    This LabelFormatter can't be used in autoscaling clusters since accelerators
    may map to multiple label, so we're not implementing `get_label_value`
    """

    LABEL_KEY = 'nvidia.com/gpu.product'

    @classmethod
    def get_label_key(cls, accelerator: Optional[str] = None) -> str:
        return cls.LABEL_KEY

    @classmethod
    def get_label_keys(cls) -> List[str]:
        return [cls.LABEL_KEY]

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        """An accelerator can map to many Nvidia GFD labels
        (e.g., A100-80GB-PCIE vs. A100-SXM4-80GB).
        As a result, we do not support get_label_value for GFDLabelFormatter."""
        raise NotImplementedError

    @classmethod
    def match_label_key(cls, label_key: str) -> bool:
        return label_key == cls.LABEL_KEY

    @classmethod
    def get_accelerator_from_label_value(cls, value: str) -> str:
        """Searches against a canonical list of NVIDIA GPUs and pattern
        matches the canonical GPU name against the GFD label.
        """
        canonical_gpu_names = [
            'A100-80GB', 'A100', 'A10G', 'H100', 'K80', 'M60', 'T4g', 'T4',
            'V100', 'A10', 'P4000', 'P100', 'P40', 'P4', 'L4'
        ]
        for canonical_name in canonical_gpu_names:
            # A100-80G accelerator is A100-SXM-80GB or A100-PCIE-80GB
            if canonical_name == 'A100-80GB' and re.search(
                    r'A100.*-80GB', value):
                return canonical_name
            elif canonical_name in value:
                return canonical_name

        # If we didn't find a canonical name:
        # 1. remove 'NVIDIA-' (e.g., 'NVIDIA-RTX-A6000' -> 'RTX-A6000')
        # 2. remove 'GEFORCE-' (e.g., 'NVIDIA-GEFORCE-RTX-3070' -> 'RTX-3070')
        # 3. remove 'RTX-' (e.g. 'RTX-6000' -> 'RTX6000')
        # Same logic, but uppercased, as the Skypilot labeler job found in
        # sky/utils/kubernetes/k8s_gpu_labeler_setup.yaml
        return value.upper().replace('NVIDIA-',
                                     '').replace('GEFORCE-',
                                                 '').replace('RTX-', 'RTX')


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
    SkyPilotLabelFormatter, GKELabelFormatter, KarpenterLabelFormatter,
    GFDLabelFormatter, CoreWeaveLabelFormatter
]

# Mapping of autoscaler type to label formatter
AUTOSCALER_TO_LABEL_FORMATTER = {
    kubernetes_enums.KubernetesAutoscalerType.GKE: GKELabelFormatter,
    kubernetes_enums.KubernetesAutoscalerType.KARPENTER: KarpenterLabelFormatter,  # pylint: disable=line-too-long
    kubernetes_enums.KubernetesAutoscalerType.GENERIC: SkyPilotLabelFormatter,
}


@functools.lru_cache()
def detect_gpu_label_formatter(
    context: Optional[str]
) -> Tuple[Optional[GPULabelFormatter], Dict[str, List[Tuple[str, str]]]]:
    """Detects the GPU label formatter for the Kubernetes cluster

    Returns:
        GPULabelFormatter: The GPU label formatter for the cluster, if found.
        Dict[str, List[Tuple[str, str]]]: A mapping of nodes and the list of
             labels on each node. E.g., {'node1': [('label1', 'value1')]}
    """
    # Get all labels across all nodes
    node_labels: Dict[str, List[Tuple[str, str]]] = {}
    nodes = get_kubernetes_nodes(context)
    for node in nodes:
        node_labels[node.metadata.name] = []
        for label, value in node.metadata.labels.items():
            node_labels[node.metadata.name].append((label, value))

    label_formatter = None

    # Check if the node labels contain any of the GPU label prefixes
    for lf in LABEL_FORMATTER_REGISTRY:
        for _, label_list in node_labels.items():
            for label, _ in label_list:
                if lf.match_label_key(label):
                    label_formatter = lf()
                    return label_formatter, node_labels

    return label_formatter, node_labels


@functools.lru_cache(maxsize=10)
def detect_accelerator_resource(
        context: Optional[str]) -> Tuple[bool, Set[str]]:
    """Checks if the Kubernetes cluster has GPU/TPU resource.

    Two types of accelerator resources are available which are each checked
    with nvidia.com/gpu and google.com/tpu. If nvidia.com/gpu resource is
    missing, that typically means that the Kubernetes cluster does not have
    GPUs or the nvidia GPU operator and/or device drivers are not installed.

    Returns:
        bool: True if the cluster has GPU_RESOURCE_KEY or TPU_RESOURCE_KEY
            resource, False otherwise.
    """
    # Get the set of resources across all nodes
    cluster_resources: Set[str] = set()
    nodes = get_kubernetes_nodes(context)
    for node in nodes:
        cluster_resources.update(node.status.allocatable.keys())
    has_accelerator = (GPU_RESOURCE_KEY in cluster_resources or
                       TPU_RESOURCE_KEY in cluster_resources)

    return has_accelerator, cluster_resources


@functools.lru_cache(maxsize=10)
def get_kubernetes_nodes(context: Optional[str] = None) -> List[Any]:
    """Gets the kubernetes nodes in the context.

    If context is None, gets the nodes in the current context.
    """
    if context is None:
        context = get_current_kube_config_context_name()

    try:
        nodes = kubernetes.core_api(context).list_node(
            _request_timeout=kubernetes.API_TIMEOUT).items
    except kubernetes.max_retry_error():
        raise exceptions.ResourcesUnavailableError(
            'Timed out when trying to get node info from Kubernetes cluster. '
            'Please check if the cluster is healthy and retry. To debug, run: '
            'kubectl get nodes') from None
    return nodes


def get_all_pods_in_kubernetes_cluster(
        context: Optional[str] = None) -> List[Any]:
    """Gets pods in all namespaces in kubernetes cluster indicated by context.

    Used for computing cluster resource usage.
    """
    if context is None:
        context = get_current_kube_config_context_name()

    try:
        pods = kubernetes.core_api(context).list_pod_for_all_namespaces(
            _request_timeout=kubernetes.API_TIMEOUT).items
    except kubernetes.max_retry_error():
        raise exceptions.ResourcesUnavailableError(
            'Timed out when trying to get pod info from Kubernetes cluster. '
            'Please check if the cluster is healthy and retry. To debug, run: '
            'kubectl get pods') from None
    return pods


def check_instance_fits(context: Optional[str],
                        instance: str) -> Tuple[bool, Optional[str]]:
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

    # TODO(zhwu): this should check the node for specific context, instead
    # of the default context to make failover fully functional.

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

    def check_tpu_fits(candidate_instance_type: 'KubernetesInstanceType',
                       node_list: List[Any]) -> Tuple[bool, Optional[str]]:
        """Checks if the instance fits on the cluster based on requested TPU.

        It checks if the TPU type and count on each node match the required
        number of TPU chips for the instance. In the case of multi-host TPU
        podslice, the function ensures that the number of TPU chips on a single
        node (node_tpu_chip_count) and the total TPU chips across the entire
        podslice (topology_chip_count) are correctly handled.
        """
        acc_type = candidate_instance_type.accelerator_type
        acc_count = candidate_instance_type.accelerator_count
        tpu_list_in_cluster = []
        for node in node_list:
            if acc_type == node.metadata.labels[
                    GKELabelFormatter.TPU_LABEL_KEY]:
                # TODO(Doyoung): Update the logic when adding support for
                # multi-host TPUs.
                if is_multi_host_tpu(node.metadata.labels):
                    continue
                node_tpu_chip_count = int(node.metadata.labels[
                    GKELabelFormatter.ACCELERATOR_COUNT_LABEL_KEY])
                tpu_type = f'{acc_type}:{node_tpu_chip_count}'
                tpu_list_in_cluster.append(tpu_type)
                if node_tpu_chip_count == acc_count:
                    return True, None
        tpu_list_in_cluster_str = ','.join(tpu_list_in_cluster)
        # TODO(Doyoung): Update the error message raised with the multi-host
        # TPU support.
        return False, ('Requested TPU type was not found in the cluster. TPU '
                       'types found in the cluster: '
                       f'{tpu_list_in_cluster_str}. Note that multi-host TPU '
                       'podslices are currently not unsupported.')

    nodes = get_kubernetes_nodes(context)
    k8s_instance_type = KubernetesInstanceType.\
        from_instance_type(instance)
    acc_type = k8s_instance_type.accelerator_type
    acc_count = k8s_instance_type.accelerator_count
    if acc_type is not None:
        # If GPU/TPUs are requested, check if GPU/TPU type is available, and
        # if so, check if CPU and memory requirements on the specific node are
        # met.
        try:
            gpu_label_key, gpu_label_val, _, _ = (
                get_accelerator_label_key_value(context, acc_type, acc_count))
        except exceptions.ResourcesUnavailableError as e:
            # If GPU not found, return empty list and error message.
            return False, str(e)
        # Get the set of nodes that have the GPU type
        gpu_nodes = [
            node for node in nodes if gpu_label_key in node.metadata.labels and
            node.metadata.labels[gpu_label_key] == gpu_label_val
        ]
        assert len(gpu_nodes) > 0, 'GPU nodes not found'
        if is_tpu_on_gke(acc_type):
            # If requested accelerator is a TPU type, check if the cluster
            # has sufficient TPU resource to meet the requirement.
            fits, reason = check_tpu_fits(k8s_instance_type, gpu_nodes)
            if reason is not None:
                return fits, reason

        candidate_nodes = gpu_nodes
        not_fit_reason_prefix = (
            f'GPU nodes with {acc_type} do not have '
            f'enough CPU (> {k8s_instance_type.cpus} CPUs) and/or '
            f'memory (> {k8s_instance_type.memory} G). ')
    else:
        candidate_nodes = nodes
        not_fit_reason_prefix = (f'No nodes found with enough '
                                 f'CPU (> {k8s_instance_type.cpus} CPUs) '
                                 'and/or memory '
                                 f'(> {k8s_instance_type.memory} G). ')
    # Check if CPU and memory requirements are met on at least one
    # candidate node.
    fits, reason = check_cpu_mem_fits(k8s_instance_type, candidate_nodes)
    if not fits:
        if reason is not None:
            reason = not_fit_reason_prefix + reason
        return fits, reason
    else:
        return fits, reason


def get_accelerator_label_key_value(
    context: Optional[str],
    acc_type: str,
    acc_count: Optional[int],
    check_mode=False
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Returns the label key and value for the given GPU/TPU type.

    Args:
        acc_type: The GPU/TPU type required by the task.
        acc_count: Number of GPU/TPUs required by the task.
        check_mode: If True, only checks if the cluster has GPU/TPU resources
            and labels are setup on the cluster. acc_type is ignore does not
            return the label key and value. Useful for checking if GPUs are
            configured correctly on the cluster without explicitly requesting
            a acc_type.
    Returns:
        A tuple of the accelerator label key, value, topology label key, and
        topology value. The topology label key and value are populated only if
        the requested accelerator type is TPU. Returns None if check_mode is
        True.
    Raises:
        ResourcesUnavailableError: Can be raised from the following conditions:
            - The cluster does not have GPU/TPU resources
                (nvidia.com/gpu, google.com/tpu)
            - The cluster does not have GPU/TPU labels setup correctly
            - The cluster doesn't have any nodes with acc_type GPU/TPU
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
            return None, None, None, None
        formatter = AUTOSCALER_TO_LABEL_FORMATTER.get(autoscaler_type)
        assert formatter is not None, ('Unsupported autoscaler type:'
                                       f' {autoscaler_type}')
        return formatter.get_label_key(acc_type), formatter.get_label_value(
            acc_type), None, None

    has_gpus, cluster_resources = detect_accelerator_resource(context)
    if has_gpus:
        # Check if the cluster has GPU labels setup correctly
        label_formatter, node_labels = \
            detect_gpu_label_formatter(context)
        if label_formatter is None:
            # If none of the GPU labels from LABEL_FORMATTER_REGISTRY are
            # detected, raise error
            with ux_utils.print_exception_no_traceback():
                supported_formats = ', '.join([
                    key for f in LABEL_FORMATTER_REGISTRY
                    for key in f.get_label_keys()
                ])
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
                    if label_formatter.match_label_key(label):
                        is_valid, reason = label_formatter.validate_label_value(
                            value)
                        if not is_valid:
                            raise exceptions.ResourcesUnavailableError(
                                f'Node {node_name!r} in Kubernetes cluster has '
                                f'invalid GPU label: {label}={value}. {reason}')
            if check_mode:
                # If check mode is enabled and we reached so far, we can
                # conclude that the cluster is setup correctly and return.
                return None, None, None, None
            # Search in node_labels to see if any node has the requested
            # GPU type.
            # Note - this only checks if the label is available on a
            # node. It does not (and should not) check if the resource
            # quantity is available since that is dynamic and can change
            # during scheduling.
            for node_name, label_list in node_labels.items():
                node_metadata_labels = dict(label_list)
                # TODO(Doyoung): Update the logic when adding support for
                # multi-host TPUs.
                if is_multi_host_tpu(node_metadata_labels):
                    continue
                for label, value in label_list:
                    if (label_formatter.match_label_key(label) and
                            label_formatter.get_accelerator_from_label_value(
                                value) == acc_type):
                        if is_tpu_on_gke(acc_type):
                            assert isinstance(label_formatter,
                                              GKELabelFormatter)
                            if node_metadata_labels.get(
                                    label_formatter.TPU_LABEL_KEY) == acc_type:
                                topology_label_key = (
                                    label_formatter.TPU_TOPOLOGY_LABEL_KEY)
                                topology_value = node_metadata_labels.get(
                                    topology_label_key)
                                assert topology_value is not None
                                tpu_topology_chip_count = reduce_tpu_topology(
                                    topology_value)
                                # For single-host TPUs, there aren't multiple
                                # different topologies that maps to identical
                                # number of TPU chips.
                                if tpu_topology_chip_count == acc_count:
                                    return (label, value, topology_label_key,
                                            topology_value)
                                else:
                                    continue
                        else:
                            return label, value, None, None

            # If no node is found with the requested acc_type, raise error
            with ux_utils.print_exception_no_traceback():
                suffix = ''
                if env_options.Options.SHOW_DEBUG_INFO.get():
                    all_labels = []
                    for node_name, label_list in node_labels.items():
                        all_labels.extend(label_list)
                    acc_available = set(v for k, v in all_labels
                                        if label_formatter.match_label_key(k))
                    suffix = (' Available GPU/TPUs on the cluster: '
                              f'{acc_available}')
                # TODO(Doyoung): Update the error message raised with the
                # multi-host TPU support.
                raise exceptions.ResourcesUnavailableError(
                    'Could not find any node in the Kubernetes cluster '
                    f'with {acc_type}. Please ensure at least one node in the '
                    f'cluster has {acc_type} and node labels are setup '
                    'correctly. Please refer to the documentration for more. '
                    f'{suffix}. Note that multi-host TPU podslices are '
                    'currently not unsupported.')
    else:
        # If GPU resources are not detected, raise error
        with ux_utils.print_exception_no_traceback():
            suffix = ''
            if env_options.Options.SHOW_DEBUG_INFO.get():
                suffix = (' Available resources on the cluster: '
                          f'{cluster_resources}')
            raise exceptions.ResourcesUnavailableError(
                f'Could not detect GPU/TPU resources ({GPU_RESOURCE_KEY!r} or '
                f'{TPU_RESOURCE_KEY!r}) in Kubernetes cluster. If this cluster'
                ' contains GPUs, please ensure GPU drivers are installed on '
                'the node. Check if the GPUs are setup correctly by running '
                '`kubectl describe nodes` and looking for the '
                f'{GPU_RESOURCE_KEY!r} or {TPU_RESOURCE_KEY!r} resource. '
                'Please refer to the documentation on how to set up GPUs.'
                f'{suffix}')


def get_head_ssh_port(cluster_name: str, namespace: str,
                      context: Optional[str]) -> int:
    svc_name = f'{cluster_name}-head-ssh'
    return get_port(svc_name, namespace, context)


def get_port(svc_name: str, namespace: str, context: Optional[str]) -> int:
    """Gets the nodeport of the specified service.

    Args:
        svc_name (str): Name of the kubernetes service. Note that this may be
            different from the cluster name.
        namespace (str): Kubernetes namespace to look for the service in.
        context (str): Kubernetes context to use.
    """
    head_service = kubernetes.core_api(context).read_namespaced_service(
        svc_name, namespace)
    return head_service.spec.ports[0].node_port


def get_external_ip(network_mode: Optional[
    kubernetes_enums.KubernetesNetworkingMode], context: Optional[str]) -> str:
    if network_mode == kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD:
        return '127.0.0.1'
    # Return the IP address of the first node with an external IP
    nodes = kubernetes.core_api(context).list_node().items
    for node in nodes:
        if node.status.addresses:
            for address in node.status.addresses:
                if address.type == 'ExternalIP':
                    return address.address
    # If no external IP is found, use the API server IP
    api_host = kubernetes.core_api(context).api_client.configuration.host
    parsed_url = urlparse(api_host)
    return parsed_url.hostname


def check_credentials(context: Optional[str],
                      timeout: int = kubernetes.API_TIMEOUT) -> \
        Tuple[bool, Optional[str]]:
    """Check if the credentials in kubeconfig file are valid

    Args:
        context (Optional[str]): The Kubernetes context to use. If none, uses
            in-cluster auth to check credentials, if available.
        timeout (int): Timeout in seconds for the test API call

    Returns:
        bool: True if credentials are valid, False otherwise
        str: Error message if credentials are invalid, None otherwise
    """
    try:
        namespace = get_kube_config_context_namespace(context)
        kubernetes.core_api(context).list_namespaced_pod(
            namespace, _request_timeout=timeout)
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

    _, exec_msg = is_kubeconfig_exec_auth(context)

    # We now check if GPUs are available and labels are set correctly on the
    # cluster, and if not we return hints that may help debug any issues.
    # This early check avoids later surprises for user when they try to run
    # `sky launch --gpus <gpu>` and the optimizer does not list Kubernetes as a
    # provider if their cluster GPUs are not setup correctly.
    gpu_msg = ''
    try:
        get_accelerator_label_key_value(context,
                                        acc_type='',
                                        acc_count=0,
                                        check_mode=True)
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


def is_kubeconfig_exec_auth(
        context: Optional[str] = None) -> Tuple[bool, Optional[str]]:
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
    all_contexts, current_context = k8s.config.list_kube_config_contexts()
    context_obj = current_context
    if context is not None:
        for c in all_contexts:
            if c['name'] == context:
                context_obj = c
                break
        else:
            raise ValueError(f'Kubernetes context {context!r} not found.')
    target_username = context_obj['context']['user']

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
        ctx_name = context_obj['name']
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


@functools.lru_cache()
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


def is_incluster_config_available() -> bool:
    """Check if in-cluster auth is available.

    Note: We cannot use load_incluster_config() to check if in-cluster config
    is available because it will load the in-cluster config (if available)
    and modify the current global kubernetes config. We simply check if the
    service account token file exists to determine if in-cluster config may
    be available.
    """
    return os.path.exists('/var/run/secrets/kubernetes.io/serviceaccount/token')


def get_all_kube_config_context_names() -> List[Optional[str]]:
    """Get all kubernetes context names from the kubeconfig file.

    If running in-cluster, returns [None] to indicate in-cluster config.

    We should not cache the result of this function as the admin policy may
    update the contexts.

    Returns:
        List[Optional[str]]: The list of kubernetes context names if
            available, an empty list otherwise. If running in-cluster,
            returns [None] to indicate in-cluster config.
    """
    k8s = kubernetes.kubernetes
    try:
        all_contexts, _ = k8s.config.list_kube_config_contexts()
        # all_contexts will always have at least one context. If kubeconfig
        # does not have any contexts defined, it will raise ConfigException.
        return [context['name'] for context in all_contexts]
    except k8s.config.config_exception.ConfigException:
        # If running in cluster, return [None] to indicate in-cluster config
        if is_incluster_config_available():
            return [None]
        return []


@functools.lru_cache()
def get_kube_config_context_namespace(
        context_name: Optional[str] = None) -> str:
    """Get the current kubernetes context namespace from the kubeconfig file

    Returns:
        str | None: The current kubernetes context namespace if it exists, else
            the default namespace.
    """
    k8s = kubernetes.kubernetes
    # Get namespace if using in-cluster config
    ns_path = '/var/run/secrets/kubernetes.io/serviceaccount/namespace'
    if os.path.exists(ns_path):
        with open(ns_path, encoding='utf-8') as f:
            return f.read().strip()
    # If not in-cluster, get the namespace from kubeconfig
    try:
        contexts, current_context = k8s.config.list_kube_config_contexts()
        if context_name is None:
            context = current_context
        else:
            context = next((c for c in contexts if c['name'] == context_name),
                           None)
            if context is None:
                return DEFAULT_NAMESPACE

        if 'namespace' in context['context']:
            return context['context']['namespace']
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


def construct_ssh_jump_command(
        private_key_path: str,
        ssh_jump_ip: str,
        ssh_jump_port: Optional[int] = None,
        ssh_jump_user: str = 'sky',
        proxy_cmd_path: Optional[str] = None,
        proxy_cmd_target_pod: Optional[str] = None,
        current_kube_context: Optional[str] = None,
        current_kube_namespace: Optional[str] = None) -> str:
    ssh_jump_proxy_command = (f'ssh -tt -i {private_key_path} '
                              '-o StrictHostKeyChecking=no '
                              '-o UserKnownHostsFile=/dev/null '
                              f'-o IdentitiesOnly=yes '
                              f'-W %h:%p {ssh_jump_user}@{ssh_jump_ip}')
    if ssh_jump_port is not None:
        ssh_jump_proxy_command += f' -p {ssh_jump_port} '
    if proxy_cmd_path is not None:
        proxy_cmd_path = os.path.expanduser(proxy_cmd_path)
        # adding execution permission to the proxy command script
        os.chmod(proxy_cmd_path, os.stat(proxy_cmd_path).st_mode | 0o111)
        kube_context_flag = f'-c {current_kube_context} ' if (
            current_kube_context is not None) else ''
        kube_namespace_flag = f'-n {current_kube_namespace} ' if (
            current_kube_namespace is not None) else ''
        ssh_jump_proxy_command += (f' -o ProxyCommand=\'{proxy_cmd_path} '
                                   f'{kube_context_flag}'
                                   f'{kube_namespace_flag}'
                                   f'{proxy_cmd_target_pod}\'')
    return ssh_jump_proxy_command


def get_ssh_proxy_command(
    k8s_ssh_target: str,
    network_mode: kubernetes_enums.KubernetesNetworkingMode,
    private_key_path: str,
    context: Optional[str],
    namespace: str,
) -> str:
    """Generates the SSH proxy command to connect to the pod.

    Uses a jump pod if the network mode is NODEPORT, and direct port-forwarding
    if the network mode is PORTFORWARD.

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
    port to a SSH jump pod. When establishing an SSH session in this mode, the
    ProxyCommand makes use of this external port to create a communication
    channel directly to port 22, which is the default port ssh server listens
    on, of the jump pod.

    With Port-forward mode, instead of directly exposing an external port,
    'kubectl port-forward' sets up a tunnel between a local port
    (127.0.0.1:23100) and port 22 of the provisioned pod. Then we establish TCP
    connection to the local end of this tunnel, 127.0.0.1:23100, using 'socat'.
    All of this is done in a ProxyCommand script. Any stdin provided on the
    local machine is forwarded through this tunnel to the application
    (SSH server) listening in the pod. Similarly, any output from the
    application in the pod is tunneled back and displayed in the terminal on
    the local machine.

    Args:
        k8s_ssh_target: str; The Kubernetes object that will be used as the
            target for SSH. If network_mode is NODEPORT, this is the name of the
            service. If network_mode is PORTFORWARD, this is the pod name.
        network_mode: KubernetesNetworkingMode; networking mode for ssh
            session. It is either 'NODEPORT' or 'PORTFORWARD'
        private_key_path: str; Path to the private key to use for SSH.
            This key must be authorized to access the SSH jump pod.
            Required for NODEPORT networking mode.
        namespace: Kubernetes namespace to use.
            Required for NODEPORT networking mode.
    """
    # Fetch IP to connect to for the jump svc
    ssh_jump_ip = get_external_ip(network_mode, context)
    assert private_key_path is not None, 'Private key path must be provided'
    if network_mode == kubernetes_enums.KubernetesNetworkingMode.NODEPORT:
        assert namespace is not None, 'Namespace must be provided for NodePort'
        ssh_jump_port = get_port(k8s_ssh_target, namespace, context)
        ssh_jump_proxy_command = construct_ssh_jump_command(
            private_key_path, ssh_jump_ip, ssh_jump_port=ssh_jump_port)
    else:
        ssh_jump_proxy_command_path = create_proxy_command_script()
        ssh_jump_proxy_command = construct_ssh_jump_command(
            private_key_path,
            ssh_jump_ip,
            ssh_jump_user=constants.SKY_SSH_USER_PLACEHOLDER,
            proxy_cmd_path=ssh_jump_proxy_command_path,
            proxy_cmd_target_pod=k8s_ssh_target,
            # We embed both the current context and namespace to the SSH proxy
            # command to make sure SSH still works when the current
            # context/namespace is changed by the user.
            current_kube_context=context,
            current_kube_namespace=namespace)
    return ssh_jump_proxy_command


def create_proxy_command_script() -> str:
    """Creates a ProxyCommand script that uses kubectl port-forward to setup
    a tunnel between a local port and the SSH server in the pod.

    Returns:
        str: Path to the ProxyCommand script.
    """
    port_fwd_proxy_cmd_path = os.path.expanduser(PORT_FORWARD_PROXY_CMD_PATH)
    os.makedirs(os.path.dirname(port_fwd_proxy_cmd_path),
                exist_ok=True,
                mode=0o700)

    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    template_path = os.path.join(root_dir, 'templates',
                                 PORT_FORWARD_PROXY_CMD_TEMPLATE)
    # Copy the template to the proxy command path. We create a copy to allow
    # different users sharing the same SkyPilot installation to have their own
    # proxy command scripts.
    shutil.copy(template_path, port_fwd_proxy_cmd_path)
    # Set the permissions to 700 to ensure only the owner can read, write,
    # and execute the file.
    os.chmod(port_fwd_proxy_cmd_path, 0o700)
    return port_fwd_proxy_cmd_path


def setup_ssh_jump_svc(ssh_jump_name: str, namespace: str,
                       context: Optional[str],
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
        kubernetes.core_api(context).create_namespaced_service(
            namespace, content['service_spec'])
    except kubernetes.api_exception() as e:
        # SSH Jump Pod service already exists.
        if e.status == 409:
            ssh_jump_service = kubernetes.core_api(
                context).read_namespaced_service(name=ssh_jump_name,
                                                 namespace=namespace)
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
                kubernetes.core_api(context).delete_namespaced_service(
                    name=ssh_jump_name, namespace=namespace)
                kubernetes.core_api(context).create_namespaced_service(
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
                       ssh_key_secret: str, namespace: str,
                       context: Optional[str]):
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
        kubernetes.core_api(context).create_namespaced_service_account(
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
        kubernetes.auth_api(context).create_namespaced_role(
            namespace, content['role'])
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
        kubernetes.auth_api(context).create_namespaced_role_binding(
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
        kubernetes.core_api(context).create_namespaced_pod(
            namespace, content['pod_spec'])
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.info(
                f'SSH Jump Host {ssh_jump_name} already exists in the cluster, '
                'using it.')
        else:
            raise
    else:
        logger.info(f'Created SSH Jump Host {ssh_jump_name}.')


def clean_zombie_ssh_jump_pod(namespace: str, context: Optional[str],
                              node_id: str):
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
        pod = kubernetes.core_api(context).read_namespaced_pod(
            node_id, namespace)
    except kubernetes.api_exception() as e:
        if e.status == 404:
            logger.warning(f'Failed to get pod {node_id},'
                           ' but the pod was not found (404).')
        raise
    else:
        ssh_jump_name = pod.metadata.labels.get('skypilot-ssh-jump')
    try:
        ssh_jump_pod = kubernetes.core_api(context).read_namespaced_pod(
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
            kubernetes.core_api(context).delete_namespaced_pod(
                ssh_jump_name, namespace)
            kubernetes.core_api(context).delete_namespaced_service(
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
            kubernetes.core_api(context).delete_namespaced_service(
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
            if key in ['containers', 'imagePullSecrets']:
                # If the key is 'containers' or 'imagePullSecrets, we take the
                # first and only container/secret in the list and merge it, as
                # we only support one container per pod.
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


def combine_pod_config_fields(
    cluster_yaml_path: str,
    cluster_config_overrides: Dict[str, Any],
) -> None:
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
    # We don't use override_configs in `skypilot_config.get_nested`, as merging
    # the pod config requires special handling.
    kubernetes_config = skypilot_config.get_nested(('kubernetes', 'pod_config'),
                                                   default_value={},
                                                   override_configs={})
    override_pod_config = (cluster_config_overrides.get('kubernetes', {}).get(
        'pod_config', {}))
    merge_dicts(override_pod_config, kubernetes_config)

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


def check_nvidia_runtime_class(context: Optional[str] = None) -> bool:
    """Checks if the 'nvidia' RuntimeClass exists in the cluster"""
    # Fetch the list of available RuntimeClasses
    runtime_classes = kubernetes.node_api(context).list_runtime_class()

    # Check if 'nvidia' RuntimeClass exists
    nvidia_exists = any(
        rc.metadata.name == 'nvidia' for rc in runtime_classes.items)
    return nvidia_exists


def check_secret_exists(secret_name: str, namespace: str,
                        context: Optional[str]) -> bool:
    """Checks if a secret exists in a namespace

    Args:
        secret_name: Name of secret to check
        namespace: Namespace to check
    """

    try:
        kubernetes.core_api(context).read_namespaced_secret(
            secret_name, namespace, _request_timeout=kubernetes.API_TIMEOUT)
    except kubernetes.api_exception() as e:
        if e.status == 404:
            return False
        raise
    else:
        return True


def create_namespace(namespace: str, context: Optional[str]) -> None:
    """Creates a namespace in the cluster.

    If the namespace already exists, logs a message and does nothing.

    Args:
        namespace: Name of the namespace to create
        context: Name of the context to use. Can be none to use default context.
    """
    kubernetes_client = kubernetes.kubernetes.client
    try:
        kubernetes.core_api(context).read_namespace(namespace)
    except kubernetes.api_exception() as e:
        if e.status != 404:
            raise
    else:
        return

    ns_metadata = dict(name=namespace, labels={'parent': 'skypilot'})
    merge_custom_metadata(ns_metadata)
    namespace_obj = kubernetes_client.V1Namespace(metadata=ns_metadata)
    try:
        kubernetes.core_api(context).create_namespace(namespace_obj)
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
    autoscaler_type = skypilot_config.get_nested(('kubernetes', 'autoscaler'),
                                                 None)
    if autoscaler_type is not None:
        autoscaler_type = kubernetes_enums.KubernetesAutoscalerType(
            autoscaler_type)
    return autoscaler_type


# Mapping of known spot label keys and values for different cluster types
# Add new cluster types here if they support spot instances along with the
# corresponding spot label key and value.
SPOT_LABEL_MAP = {
    kubernetes_enums.KubernetesAutoscalerType.GKE.value:
        ('cloud.google.com/gke-spot', 'true')
}


def get_spot_label(
        context: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Get the spot label key and value for using spot instances, if supported.

    Checks if the underlying cluster supports spot instances by checking nodes
    for known spot label keys and values. If found, returns the spot label key
    and value. If not, checks if autoscaler is configured and returns
    appropriate labels. If neither are found, returns None.

    Returns:
        Tuple[str, str]: Tuple containing the spot label key and value. Returns
            None if spot instances are not supported.
    """
    # Check if the cluster supports spot instances by checking nodes for known
    # spot label keys and values
    for node in get_kubernetes_nodes(context):
        for _, (key, value) in SPOT_LABEL_MAP.items():
            if key in node.metadata.labels and node.metadata.labels[
                    key] == value:
                return key, value

    # Check if autoscaler is configured. Allow spot instances if autoscaler type
    # is known to support spot instances.
    autoscaler_type = get_autoscaler_type()
    if autoscaler_type == kubernetes_enums.KubernetesAutoscalerType.GKE:
        return SPOT_LABEL_MAP[autoscaler_type.value]

    return None, None


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


@dataclasses.dataclass
class KubernetesNodeInfo:
    """Dataclass to store Kubernetes node information."""
    name: str
    accelerator_type: Optional[str]
    # Resources available on the node. E.g., {'nvidia.com/gpu': '2'}
    total: Dict[str, int]
    free: Dict[str, int]


def get_kubernetes_node_info(
        context: Optional[str] = None) -> Dict[str, KubernetesNodeInfo]:
    """Gets the resource information for all the nodes in the cluster.

    Currently only GPU resources are supported. The function returns the total
    number of GPUs available on the node and the number of free GPUs on the
    node.

    If the user does not have sufficient permissions to list pods in all
    namespaces, the function will return free GPUs as -1.

    Returns:
        Dict[str, KubernetesNodeInfo]: Dictionary containing the node name as
            key and the KubernetesNodeInfo object as value
    """
    nodes = get_kubernetes_nodes(context)
    # Get the pods to get the real-time resource usage
    try:
        pods = get_all_pods_in_kubernetes_cluster(context)
    except kubernetes.api_exception() as e:
        if e.status == 403:
            pods = None
        else:
            raise

    lf, _ = detect_gpu_label_formatter(context)
    if not lf:
        label_key = None
    else:
        label_keys = lf.get_label_keys()

    node_info_dict: Dict[str, KubernetesNodeInfo] = {}

    for label_key in label_keys:
        for node in nodes:
            allocated_qty = 0
            if lf is not None and label_key in node.metadata.labels:
                accelerator_name = lf.get_accelerator_from_label_value(
                    node.metadata.labels.get(label_key))
            else:
                accelerator_name = None

            accelerator_count = get_node_accelerator_count(
                node.status.allocatable)

            if pods is None:
                accelerators_available = -1

            else:
                for pod in pods:
                    # Get all the pods running on the node
                    if (pod.spec.node_name == node.metadata.name and
                            pod.status.phase in ['Running', 'Pending']):
                        # Iterate over all the containers in the pod and sum the
                        # GPU requests
                        for container in pod.spec.containers:
                            if container.resources.requests:
                                allocated_qty += get_node_accelerator_count(
                                    container.resources.requests)

                accelerators_available = accelerator_count - allocated_qty

            # Exclude multi-host TPUs from being processed.
            # TODO(Doyoung): Remove the logic when adding support for
            # multi-host TPUs.
            if is_multi_host_tpu(node.metadata.labels):
                continue

            node_info_dict[node.metadata.name] = KubernetesNodeInfo(
                name=node.metadata.name,
                accelerator_type=accelerator_name,
                total={'accelerator_count': int(accelerator_count)},
                free={'accelerators_available': int(accelerators_available)})

    return node_info_dict


def to_label_selector(tags):
    label_selector = ''
    for k, v in tags.items():
        if label_selector != '':
            label_selector += ','
        label_selector += '{}={}'.format(k, v)
    return label_selector


def get_namespace_from_config(provider_config: Dict[str, Any]) -> str:
    context = get_context_from_config(provider_config)
    return provider_config.get('namespace',
                               get_kube_config_context_namespace(context))


def filter_pods(namespace: str,
                context: Optional[str],
                tag_filters: Dict[str, str],
                status_filters: Optional[List[str]] = None) -> Dict[str, Any]:
    """Filters pods by tags and status."""
    non_included_pod_statuses = POD_STATUSES.copy()

    field_selector = ''
    if status_filters is not None:
        non_included_pod_statuses -= set(status_filters)
        field_selector = ','.join(
            [f'status.phase!={status}' for status in non_included_pod_statuses])

    label_selector = to_label_selector(tag_filters)
    pod_list = kubernetes.core_api(context).list_namespaced_pod(
        namespace, field_selector=field_selector, label_selector=label_selector)

    # Don't return pods marked for deletion,
    # i.e. pods with non-null metadata.DeletionTimestamp.
    pods = [
        pod for pod in pod_list.items if pod.metadata.deletion_timestamp is None
    ]
    return {pod.metadata.name: pod for pod in pods}


def _remove_pod_annotation(pod: Any,
                           annotation_key: str,
                           namespace: str,
                           context: Optional[str] = None) -> None:
    """Removes specified Annotations from a Kubernetes pod."""
    try:
        # Remove the specified annotation
        if pod.metadata.annotations:
            if annotation_key in pod.metadata.annotations:
                # Patch the pod with the updated metadata.
                body = {'metadata': {'annotations': {annotation_key: None}}}
                kubernetes.core_api(context).patch_namespaced_pod(
                    name=pod.metadata.name,
                    namespace=namespace,
                    body=body,
                    _request_timeout=kubernetes.API_TIMEOUT)

    except kubernetes.api_exception() as e:
        if e.status == 404:
            logger.warning(
                ANNOTATIONS_POD_NOT_FOUND_ERROR_MSG.format(
                    pod_name=pod.metadata.name,
                    namespace=namespace,
                    action='remove',
                    annotation=annotation_key))
        else:
            with ux_utils.print_exception_no_traceback():
                raise


def _add_pod_annotation(pod: Any,
                        annotation: Dict[str, str],
                        namespace: str,
                        context: Optional[str] = None) -> None:
    """Adds specified Annotations on a Kubernetes pod."""
    try:
        # Patch the pod with the updated metadata
        body = {'metadata': {'annotations': annotation}}
        kubernetes.core_api(context).patch_namespaced_pod(
            name=pod.metadata.name,
            namespace=namespace,
            body=body,
            _request_timeout=kubernetes.API_TIMEOUT)

    except kubernetes.api_exception() as e:
        if e.status == 404:
            logger.warning(
                ANNOTATIONS_POD_NOT_FOUND_ERROR_MSG.format(
                    pod_name=pod.metadata.name,
                    namespace=namespace,
                    action='add',
                    annotation=annotation))
        else:
            with ux_utils.print_exception_no_traceback():
                raise


def set_autodown_annotations(handle: 'backends.CloudVmRayResourceHandle',
                             idle_minutes_to_autostop: Optional[int],
                             down: bool = False) -> None:
    """Adds or removes Annotations of autodown on Kubernetes pods."""
    tags = {
        provision_constants.TAG_RAY_CLUSTER_NAME: handle.cluster_name_on_cloud,
    }
    ray_config = common_utils.read_yaml(handle.cluster_yaml)
    provider_config = ray_config['provider']
    namespace = get_namespace_from_config(provider_config)
    context = get_context_from_config(provider_config)
    running_pods = filter_pods(namespace, context, tags)

    for _, pod in running_pods.items():
        if down:
            idle_minutes_to_autostop_annotation = {
                IDLE_MINUTES_TO_AUTOSTOP_ANNOTATION_KEY:
                    str(idle_minutes_to_autostop)
            }
            autodown_annotation = {AUTODOWN_ANNOTATION_KEY: 'true'}
            _add_pod_annotation(pod=pod,
                                annotation=idle_minutes_to_autostop_annotation,
                                namespace=namespace,
                                context=context)
            _add_pod_annotation(pod=pod,
                                annotation=autodown_annotation,
                                namespace=namespace,
                                context=context)

        # If idle_minutes_to_autostop is negative, it indicates a request to
        # cancel autostop using the --cancel flag with the `sky autostop`
        # command.
        elif (idle_minutes_to_autostop is not None and
              idle_minutes_to_autostop < 0):
            _remove_pod_annotation(
                pod=pod,
                annotation_key=IDLE_MINUTES_TO_AUTOSTOP_ANNOTATION_KEY,
                namespace=namespace,
                context=context)
            _remove_pod_annotation(pod=pod,
                                   annotation_key=AUTODOWN_ANNOTATION_KEY,
                                   namespace=namespace,
                                   context=context)


def get_context_from_config(provider_config: Dict[str, Any]) -> Optional[str]:
    context = provider_config.get('context',
                                  get_current_kube_config_context_name())
    if context == IN_CLUSTER_REGION:
        # If the context (also used as the region) is set to IN_CLUSTER_REGION
        # we need to use in-cluster auth.
        context = None
    return context


def get_skypilot_pods(context: Optional[str] = None) -> List[Any]:
    """Gets all SkyPilot pods in the Kubernetes cluster.

    Args:
        context: Kubernetes context to use. If None, uses the current context.

    Returns:
        A list of Kubernetes pod objects.
    """
    if context is None:
        context = get_current_kube_config_context_name()

    try:
        pods = kubernetes.core_api(context).list_pod_for_all_namespaces(
            label_selector='skypilot-cluster',
            _request_timeout=kubernetes.API_TIMEOUT).items
    except kubernetes.max_retry_error():
        raise exceptions.ResourcesUnavailableError(
            'Timed out trying to get SkyPilot pods from Kubernetes cluster. '
            'Please check if the cluster is healthy and retry. To debug, run: '
            'kubectl get pods --selector=skypilot-cluster --all-namespaces'
        ) from None
    return pods


def is_tpu_on_gke(accelerator: str) -> bool:
    """Determins if the given accelerator is a TPU supported on GKE."""
    return accelerator in GKE_TPU_ACCELERATOR_TO_GENERATION


def get_node_accelerator_count(attribute_dict: dict) -> int:
    """Retrieves the count of accelerators from a node's resource dictionary.

    This method checks the node's allocatable resources or the accelerators
    already deployed on the node, using pod objects that describe resource
    requests.

    Args:
        attribute_dict: Containing resource information from a node, such as
            allocatable or requested resources.

    Returns:
        Number of accelerators allocated or available from the node. If no
            resource is found, it returns 0.
    """
    assert not (GPU_RESOURCE_KEY in attribute_dict and
                TPU_RESOURCE_KEY in attribute_dict)
    if GPU_RESOURCE_KEY in attribute_dict:
        return int(attribute_dict[GPU_RESOURCE_KEY])
    elif TPU_RESOURCE_KEY in attribute_dict:
        return int(attribute_dict[TPU_RESOURCE_KEY])
    return 0


def reduce_tpu_topology(topology: str) -> int:
    """Computes the number of TPU chips from its topology string."""
    chip_dimensions = [int(chip_count) for chip_count in topology.split('x')]
    # tpu_topology_chip_count represents the total number of TPU chips in the
    # entire podslice, whether it is a single-host or multi-host TPU podslice.
    tpu_topology_chip_count = functools.reduce(lambda x, y: x * y,
                                               chip_dimensions)
    return tpu_topology_chip_count


def is_multi_host_tpu(node_metadata_labels: dict) -> bool:
    """Determines whether the given node is a multi-host TPU configuration."""
    if GKELabelFormatter.TPU_LABEL_KEY in node_metadata_labels:
        assert GKELabelFormatter.TPU_TOPOLOGY_LABEL_KEY in node_metadata_labels
        topology_value = (
            node_metadata_labels[GKELabelFormatter.TPU_TOPOLOGY_LABEL_KEY])
        accelerator_count_label_key = (
            GKELabelFormatter.ACCELERATOR_COUNT_LABEL_KEY)
        assert accelerator_count_label_key in node_metadata_labels
        # node_tpu_chip_count represents the number of TPU chips
        # available in this node. If the node is part of a node pool
        # forming a multi-host TPU podslice, it only reflects the
        # number of TPU chips in this individual node, not the entire
        # multi-host TPU podslice.
        node_tpu_chip_count = int(
            node_metadata_labels[accelerator_count_label_key])
        topology_chip_count = reduce_tpu_topology(topology_value)
        # For multi-host TPU podslices, topology_chip_count and
        # node_tpu_chip_count will differ, as topology_chip_count
        # reflects the total across all hosts, while
        # node_tpu_chip_count reflects only the chips in a single node.
        if node_tpu_chip_count != topology_chip_count:
            return True
    return False


def multi_host_tpu_exists_in_cluster(context: Optional[str] = None) -> bool:
    """Checks if there exists a multi-host TPU within the cluster."""
    nodes = get_kubernetes_nodes(context)
    for node in nodes:
        if is_multi_host_tpu(node.metadata.labels):
            return True
    return False


@dataclasses.dataclass
class KubernetesSkyPilotClusterInfo:
    cluster_name_on_cloud: str
    cluster_name: str
    user: str
    status: status_lib.ClusterStatus
    pods: List[Any]
    launched_at: float
    resources: 'resources_lib.Resources'
    resources_str: str


def process_skypilot_pods(
    pods: List[Any],
    context: Optional[str] = None
) -> Tuple[List[KubernetesSkyPilotClusterInfo],
           List[KubernetesSkyPilotClusterInfo],
           List[KubernetesSkyPilotClusterInfo]]:
    """Process SkyPilot pods on k8s to extract cluster and controller info.

    Args:
        pods: List of Kubernetes pod objects.
        context: Kubernetes context name, used to detect GPU label formatter.

    Returns:
        A tuple containing:
        - List of KubernetesSkyPilotClusterInfo with all cluster info.
        - List of KubernetesSkyPilotClusterInfo with job controller info.
        - List of KubernetesSkyPilotClusterInfo with serve controller info.
    """
    # pylint: disable=import-outside-toplevel
    from sky import resources as resources_lib
    clusters: Dict[str, KubernetesSkyPilotClusterInfo] = {}
    jobs_controllers: List[KubernetesSkyPilotClusterInfo] = []
    serve_controllers: List[KubernetesSkyPilotClusterInfo] = []

    for pod in pods:
        cluster_name_on_cloud = pod.metadata.labels.get('skypilot-cluster')
        cluster_name = cluster_name_on_cloud.rsplit(
            '-', 1
        )[0]  # Remove the user hash to get cluster name (e.g., mycluster-2ea4)
        if cluster_name_on_cloud not in clusters:
            # Parse the start time for the cluster
            start_time = pod.status.start_time
            if start_time is not None:
                start_time = pod.status.start_time.timestamp()

            # Parse resources
            cpu_request = parse_cpu_or_gpu_resource(
                pod.spec.containers[0].resources.requests.get('cpu', '0'))
            memory_request = parse_memory_resource(
                pod.spec.containers[0].resources.requests.get('memory', '0'),
                unit='G')
            gpu_count = parse_cpu_or_gpu_resource(
                pod.spec.containers[0].resources.requests.get(
                    'nvidia.com/gpu', '0'))
            gpu_name = None
            if gpu_count > 0:
                label_formatter, _ = (detect_gpu_label_formatter(context))
                assert label_formatter is not None, (
                    'GPU label formatter cannot be None if there are pods '
                    f'requesting GPUs: {pod.metadata.name}')
                gpu_label = label_formatter.get_label_key()
                # Get GPU name from pod node selector
                if pod.spec.node_selector is not None:
                    gpu_name = label_formatter.get_accelerator_from_label_value(
                        pod.spec.node_selector.get(gpu_label))

            resources = resources_lib.Resources(
                cloud=clouds.Kubernetes(),
                cpus=int(cpu_request),
                memory=int(memory_request),
                accelerators=(f'{gpu_name}:{gpu_count}'
                              if gpu_count > 0 else None))
            if pod.status.phase == 'Pending':
                # If pod is pending, do not show it in the status
                continue

            cluster_info = KubernetesSkyPilotClusterInfo(
                cluster_name_on_cloud=cluster_name_on_cloud,
                cluster_name=cluster_name,
                user=pod.metadata.labels.get('skypilot-user'),
                status=status_lib.ClusterStatus.UP,
                pods=[],
                launched_at=start_time,
                resources=resources,
                resources_str='')
            clusters[cluster_name_on_cloud] = cluster_info
            # Check if cluster name is name of a controller
            # Can't use controller_utils.Controllers.from_name(cluster_name)
            # because hash is different across users
            if 'sky-jobs-controller' in cluster_name_on_cloud:
                jobs_controllers.append(cluster_info)
            elif 'sky-serve-controller' in cluster_name_on_cloud:
                serve_controllers.append(cluster_info)
        else:
            # Update start_time if this pod started earlier
            pod_start_time = pod.status.start_time
            if pod_start_time is not None:
                pod_start_time = pod_start_time.timestamp()
                if pod_start_time < clusters[cluster_name_on_cloud].launched_at:
                    clusters[cluster_name_on_cloud].launched_at = pod_start_time
        clusters[cluster_name_on_cloud].pods.append(pod)
    # Update resources_str in clusters:
    for cluster in clusters.values():
        num_pods = len(cluster.pods)
        cluster.resources_str = f'{num_pods}x {cluster.resources}'
    return list(clusters.values()), jobs_controllers, serve_controllers
