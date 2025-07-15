"""Kubernetes utilities for SkyPilot."""
import dataclasses
import enum
import functools
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import typing
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlparse

import sky
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.adaptors import gcp
from sky.adaptors import kubernetes
from sky.provision import constants as provision_constants
from sky.provision.kubernetes import constants as kubernetes_constants
from sky.provision.kubernetes import network_utils
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import env_options
from sky.utils import kubernetes_enums
from sky.utils import schemas
from sky.utils import status_lib
from sky.utils import timeline
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import jinja2
    import yaml

    from sky import backends
    from sky import resources as resources_lib
else:
    jinja2 = adaptors_common.LazyImport('jinja2')
    yaml = adaptors_common.LazyImport('yaml')

# Please be careful when changing this.
# When mounting, Kubernetes changes the ownership of the parent directory
# to root:root.
# See https://stackoverflow.com/questions/50818029/mounted-folder-created-as-root-instead-of-current-user-in-docker/50820023#50820023.  # pylint: disable=line-too-long
HIGH_AVAILABILITY_DEPLOYMENT_VOLUME_MOUNT_NAME = 'sky-data'
# Path where the persistent volume for HA controller is mounted.
# TODO(andy): Consider using dedicated path like `/var/skypilot`
# and store all data that needs to be persisted in future.
HIGH_AVAILABILITY_DEPLOYMENT_VOLUME_MOUNT_PATH = '/home/sky'


class KubernetesHighPerformanceNetworkType(enum.Enum):
    """Enum for different Kubernetes cluster types with high performance
    network configurations.

    This enum defines cluster types that support optimized networking for
    distributed ML workloads:
    - GCP_TCPX: GKE clusters with GPUDirect-TCPX support
      (A3 High instances: a3-highgpu-8g)
    - GCP_TCPXO: GKE clusters with GPUDirect-TCPXO support
      (A3 Mega instances: a3-megagpu-8g)
    - GCP_GPUDIRECT_RDMA: GKE clusters with GPUDirect-RDMA support
      (A4/A3 Ultra instances)
    - NEBIUS: Nebius clusters with InfiniBand support for high-throughput,
      low-latency networking
    - NONE: Standard clusters without specialized networking optimizations

    The network configurations align with corresponding VM-based
    implementations:
    - GCP settings match
      sky.provision.gcp.constants.GPU_DIRECT_TCPX_SPECIFIC_OPTIONS
    - Nebius settings match the InfiniBand configuration used in Nebius VMs
    """

    GCP_TCPX = 'gcp_tcpx'
    GCP_TCPXO = 'gcp_tcpxo'
    GCP_GPUDIRECT_RDMA = 'gcp_gpudirect_rdma'
    NEBIUS = 'nebius'
    NONE = 'none'

    def get_network_env_vars(self) -> Dict[str, str]:
        """Get network environment variables for this cluster type."""
        if self == KubernetesHighPerformanceNetworkType.NEBIUS:
            # Nebius cluster with InfiniBand - use InfiniBand optimizations
            return {
                'NCCL_IB_HCA': 'mlx5',
                'UCX_NET_DEVICES': ('mlx5_0:1,mlx5_1:1,mlx5_2:1,mlx5_3:1,'
                                    'mlx5_4:1,mlx5_5:1,mlx5_6:1,mlx5_7:1')
            }
        else:
            # GCP clusters and generic clusters - environment variables are
            # handled directly in the template
            return {}

    def supports_high_performance_networking(self) -> bool:
        """Check if this cluster type supports high performance networking."""
        return self is not KubernetesHighPerformanceNetworkType.NONE

    def supports_gpu_direct(self) -> bool:
        """Check if this cluster type supports GPUDirect networking."""
        return self in (KubernetesHighPerformanceNetworkType.GCP_TCPX,
                        KubernetesHighPerformanceNetworkType.GCP_TCPXO,
                        KubernetesHighPerformanceNetworkType.GCP_GPUDIRECT_RDMA)

    def requires_ipc_lock_capability(self) -> bool:
        """Check if this cluster type requires IPC_LOCK capability."""
        return self.supports_high_performance_networking()

    def requires_tcpxo_daemon(self) -> bool:
        """Check if this cluster type requires TCPXO daemon."""
        return self == KubernetesHighPerformanceNetworkType.GCP_TCPXO


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
    'tpu-v6e-slice': 'v6e',
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

# Default retry settings for Kubernetes API calls
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_INTERVAL_SECONDS = 1


def normalize_tpu_accelerator_name(accelerator: str) -> Tuple[str, int]:
    """Normalize TPU names to the k8s-compatible name and extract count."""
    # Examples:
    # 'tpu-v6e-8' -> ('tpu-v6e-slice', 8)
    # 'tpu-v5litepod-4' -> ('tpu-v5-lite-podslice', 4)

    gcp_to_k8s_patterns = [
        (r'^tpu-v6e-(\d+)$', 'tpu-v6e-slice'),
        (r'^tpu-v5p-(\d+)$', 'tpu-v5p-slice'),
        (r'^tpu-v5litepod-(\d+)$', 'tpu-v5-lite-podslice'),
        (r'^tpu-v5lite-(\d+)$', 'tpu-v5-lite-device'),
        (r'^tpu-v4-(\d+)$', 'tpu-v4-podslice'),
    ]

    for pattern, replacement in gcp_to_k8s_patterns:
        match = re.match(pattern, accelerator)
        if match:
            count = int(match.group(1))
            return replacement, count

    # Default fallback
    return accelerator, 1


def _retry_on_error(max_retries=DEFAULT_MAX_RETRIES,
                    retry_interval=DEFAULT_RETRY_INTERVAL_SECONDS,
                    resource_type: Optional[str] = None):
    """Decorator to retry Kubernetes API calls on transient failures.

    Args:
        max_retries: Maximum number of retry attempts
        retry_interval: Initial seconds to wait between retries
        resource_type: Type of resource being accessed (e.g. 'node', 'pod').
            Used to provide more specific error messages.

    Raises:
        KubeAPIUnreachableError: If the API server of the given context is
            unreachable.
    """

    def decorator(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            backoff = common_utils.Backoff(initial_backoff=retry_interval,
                                           max_backoff_factor=3)

            assert 'context' in kwargs, 'context is required'
            context = kwargs.get('context')

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (kubernetes.max_retry_error(),
                        kubernetes.api_exception(),
                        kubernetes.config_exception()) as e:
                    last_exception = e
                    # Don't retry on permanent errors like 401 (Unauthorized)
                    # or 403 (Forbidden)
                    if (isinstance(e, kubernetes.api_exception()) and
                            e.status in (401, 403)):
                        # Raise KubeAPIUnreachableError exception so that the
                        # optimizer/provisioner can failover to other clouds.
                        raise exceptions.KubeAPIUnreachableError(
                            f'Kubernetes API error: {str(e)}') from e
                    if attempt < max_retries - 1:
                        sleep_time = backoff.current_backoff()
                        logger.debug(f'Kubernetes API call {func.__name__} '
                                     f'failed with {str(e)}. Retrying in '
                                     f'{sleep_time:.1f}s...')
                        time.sleep(sleep_time)
                        continue

            # Format error message based on the type of exception
            resource_msg = f' when trying to get {resource_type} info' \
                if resource_type else ''
            debug_cmd = f' To debug, run: kubectl get {resource_type}s' \
                if resource_type else ''
            if context:
                debug_cmd += f' --context {context}'

            if isinstance(last_exception, kubernetes.max_retry_error()):
                error_msg = f'Timed out{resource_msg} from Kubernetes cluster.'
            elif isinstance(last_exception, kubernetes.api_exception()):
                error_msg = (f'Kubernetes API error{resource_msg}: '
                             f'{str(last_exception)}')
            else:
                error_msg = (f'Kubernetes configuration error{resource_msg}: '
                             f'{str(last_exception)}')

            raise exceptions.KubeAPIUnreachableError(
                f'{error_msg}'
                f' Please check if the cluster is healthy and retry.'
                f'{debug_cmd}') from last_exception

        return wrapper

    return decorator


class GPULabelFormatter:
    """Base class to define a GPU label formatter for a Kubernetes cluster

    A GPU label formatter is a class that defines how to use GPU type labels in
    a Kubernetes cluster. It is used by the Kubernetes cloud class to pick the
    key:value pair to use as node selector for GPU nodes.
    """

    @classmethod
    def get_tpu_topology_label_key(cls) -> str:
        """Returns the label for TPU topology used by the Kubernetes cluster.

        Only implemented by formatters that support TPUs.
        """
        raise NotImplementedError

    @classmethod
    def get_tpu_topology_label_value(cls, acc_type: str, acc_count: int) -> str:
        """Returns the TPU topology value for the given TPU type and count.

        Only implemented by formatters that support TPUs.
        """
        raise NotImplementedError

    @classmethod
    def get_label_key(cls, accelerator: Optional[str] = None) -> str:
        """Returns the label key for GPU type used by the Kubernetes cluster"""
        raise NotImplementedError

    @classmethod
    def get_label_keys(cls) -> List[str]:
        """Returns a list of label keys for GPU used by Kubernetes cluster."""
        raise NotImplementedError

    @classmethod
    def get_label_values(cls, accelerator: str) -> List[str]:
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
    if accelerator in ('A100-80GB', 'L4', 'H100-80GB', 'H100-MEGA-80GB',
                       'B200'):
        # A100-80GB, L4, H100-80GB and H100-MEGA-80GB
        # have a different name pattern.
        return 'nvidia-{}'.format(accelerator.lower())
    elif accelerator == 'H200':
        # H200s on GCP use this label format
        return 'nvidia-h200-141gb'
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
    def get_label_values(cls, accelerator: str) -> List[str]:
        # For SkyPilot formatter, we use the accelerator str directly.
        # See sky.utils.kubernetes.gpu_labeler.
        return [accelerator.lower()]

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
    def get_label_values(cls, accelerator: str) -> List[str]:
        return [accelerator.upper()]

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

    # Mapping from TPU type to {count: topologies}. Used to determine topology
    # label to use in an autoscaling environment. For list of topologies, see:
    # tpu v5e: https://cloud.google.com/tpu/docs/tpus-in-gke
    # tpu v5p: https://cloud.google.com/tpu/docs/v5p
    # tpu v6e: https://cloud.google.com/tpu/docs/v6e
    # TODO(romilb): Add support for TPU v4.
    GKE_TPU_TOPOLOGIES = {
        'tpu-v5-lite-podslice': {
            1: '1x1',
            4: '2x2',
            8: '2x4'
        },
        'tpu-v5-lite-device': {
            1: '1x1',
            4: '2x2',
            8: '2x4'
        },
        'tpu-v5p-slice': {
            4: '2x2x1'
        },
        'tpu-v6e-slice': {
            1: '1x1',
            4: '2x2',
            8: '2x4'
        }
    }

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
    def get_tpu_topology_label_value(cls, acc_type: str, acc_count: int) -> str:
        """Returns the TPU topology label value for the given TPU count.

        e.g. tpu-v5-lite-podslice:8 -> '2x4'
        """
        # If the TPU type is in the GKE_TPU_ACCELERATOR_TO_GENERATION, it means
        # that it has been normalized before, no need to normalize again.
        if acc_type not in GKE_TPU_ACCELERATOR_TO_GENERATION:
            acc_type, acc_count = normalize_tpu_accelerator_name(acc_type)
        count_to_topology = cls.GKE_TPU_TOPOLOGIES.get(acc_type,
                                                       {}).get(acc_count, None)
        if count_to_topology is None:
            supported_tpus = {
                tpu: list(topologies.values())
                for tpu, topologies in cls.GKE_TPU_TOPOLOGIES.items()
            }
            raise ValueError(
                f'No TPU topology found for {acc_type} with count {acc_count}. '
                f'Supported TPU types and counts: {supported_tpus}')
        return count_to_topology

    @classmethod
    def get_label_values(cls, accelerator: str) -> List[str]:
        return [get_gke_accelerator_name(accelerator)]

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
            elif acc == 'H200-141GB':
                return 'H200'
            return acc
        elif is_tpu_on_gke(value):
            return value
        else:
            raise ValueError(
                f'Invalid accelerator name in GKE cluster: {value}')

    @classmethod
    def validate_label_value(cls, value: str) -> Tuple[bool, str]:
        try:
            _ = cls.get_accelerator_from_label_value(value)
            return True, ''
        except ValueError as e:
            return False, str(e)


class GFDLabelFormatter(GPULabelFormatter):
    """GPU Feature Discovery label formatter

    NVIDIA GPUs nodes are labeled by GPU feature discovery
    e.g. nvidia.com/gpu.product=NVIDIA-H100-80GB-HBM3
    https://github.com/NVIDIA/gpu-feature-discovery

    GPU feature discovery is included as part of the
    NVIDIA GPU Operator:
    https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/overview.html

    This LabelFormatter can't be used in autoscaling clusters since accelerators
    may map to multiple label, so we're not implementing `get_label_values`
    """

    LABEL_KEY = 'nvidia.com/gpu.product'

    @classmethod
    def get_label_key(cls, accelerator: Optional[str] = None) -> str:
        return cls.LABEL_KEY

    @classmethod
    def get_label_keys(cls) -> List[str]:
        return [cls.LABEL_KEY]

    @classmethod
    def get_label_values(cls, accelerator: str) -> List[str]:
        # An accelerator can map to many Nvidia GFD labels
        # (e.g., A100-80GB-PCIE vs. A100-SXM4-80GB).
        # TODO implement get_label_values for GFDLabelFormatter
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
            'V100', 'A10', 'P4000', 'P100', 'P40', 'P4', 'L40', 'L4'
        ]
        for canonical_name in canonical_gpu_names:
            # A100-80G accelerator is A100-SXM-80GB or A100-PCIE-80GB
            if canonical_name == 'A100-80GB' and re.search(
                    r'A100.*-80GB', value):
                return canonical_name
            # Use word boundary matching to prevent substring matches
            elif re.search(rf'\b{re.escape(canonical_name)}\b', value):
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


@annotations.lru_cache(scope='request')
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
    nodes = get_kubernetes_nodes(context=context)
    for node in nodes:
        node_labels[node.metadata.name] = []
        for label, value in node.metadata.labels.items():
            node_labels[node.metadata.name].append((label, value))

    # Check if the node labels contain any of the GPU label prefixes
    for lf in LABEL_FORMATTER_REGISTRY:
        skip = False
        for _, label_list in node_labels.items():
            for label, value in label_list:
                if lf.match_label_key(label):
                    valid, reason = lf.validate_label_value(value)
                    if valid:
                        return lf(), node_labels
                    else:
                        logger.warning(f'GPU label {label} matched for label '
                                       f'formatter {lf.__class__.__name__}, '
                                       f'but has invalid value {value}. '
                                       f'Reason: {reason}. '
                                       'Skipping...')
                        skip = True
                        break
            if skip:
                break
        if skip:
            continue

    return None, node_labels


class Autoscaler:
    """Base class to define a autoscaler for a Kubernetes cluster.
    An autoscaler is a class that defines how to detect if a Kubernetes
    context can autoscale to meet the resource requirements of a task.
    """

    label_formatter: Any = None

    # returns if the autoscaler backend can be queried for information.
    # If True, SkyPilot will query the autoscaler backend to check if
    # the Kubernetes context can autoscale to meet the resource requirements
    # of a task.
    can_query_backend: bool = False

    @classmethod
    # pylint: disable=unused-argument
    def can_create_new_instance_of_type(cls, context: str,
                                        instance_type: str) -> bool:
        """Returns if the Kubernetes context has an autoscaler
        that can create a new node that satisfies the instance type.
        Args:
            context: The Kubernetes context to check.
            instance_type: The instance type to check.
        Returns:
            bool: True if the Kubernetes context has an autoscaler that can
                create a new node satisfying the instance type,
                or if such determination is not possible.
                False if the Kubernetes context autoscaler cannot create a new
                node satisfying the instance type.
        """
        # For autoscalers that SkyPilot does not know how to interface with,
        # assume the autoscaler can create a new node that satisfies
        # the instance type.
        # If this is not the case, the autoscaler will fail to provision the
        # node and the pod will be stuck in pending state until
        # provision_timeout, after which failover will be triggered.
        return True


class GKEAutoscaler(Autoscaler):
    """GKE autoscaler
    """

    label_formatter: Any = GKELabelFormatter
    can_query_backend: bool = True

    # This variable is stored in memory in the server.
    # The variable will reset if the server restarts.
    _pip_install_gcp_hint_last_sent = 0.0

    @classmethod
    @annotations.lru_cache(scope='request', maxsize=10)
    def can_create_new_instance_of_type(cls, context: str,
                                        instance_type: str) -> bool:
        """Looks at each node pool in the cluster and checks if
        it can create a new node that satisfies the instance type.
        If the context does not match standard GKE context naming convention,
        or GKE credential is not set, this function returns True
        for optimistic pod scheduling.
        """
        # assume context naming convention of
        # gke_PROJECT-ID_LOCATION_CLUSTER-NAME
        valid, project_id, location, cluster_name = cls._validate_context_name(
            context)
        if not valid:
            # Context name is not in the format of
            # gke_PROJECT-ID_LOCATION_CLUSTER-NAME.
            # Cannot determine if the context can autoscale
            # return True for optimistic pod scheduling.
            logger.debug(f'context {context} is not in the format of '
                         f'gke_PROJECT-ID_LOCATION_CLUSTER-NAME. '
                         'reporting context as potentially capable of '
                         'provisioning resources without further check')
            return True
        try:
            logger.debug(
                f'attempting to get information about cluster {cluster_name}')
            container_service = gcp.build('container',
                                          'v1',
                                          credentials=None,
                                          cache_discovery=False)
            cluster = container_service.projects().locations().clusters().get(
                name=f'projects/{project_id}'
                f'/locations/{location}'
                f'/clusters/{cluster_name}').execute()
        except ImportError:
            # If the gcp module is not installed, return True for
            # optimistic pod scheduling.
            # Remind the user once per day to install the gcp module for better
            # pod scheduling with GKE autoscaler.
            if time.time() - cls._pip_install_gcp_hint_last_sent > 60 * 60 * 24:
                logger.info(
                    'Could not fetch autoscaler information from GKE. '
                    'Run pip install "skypilot[gcp]" for more intelligent pod '
                    'scheduling with GKE autoscaler.')
                cls._pip_install_gcp_hint_last_sent = time.time()
            return True
        except gcp.http_error_exception() as e:
            # Cluster information is not available.
            # return True for optimistic pod scheduling.
            logger.debug(f'{e.message}', exc_info=True)
            return True

        # Check if any node pool with autoscaling enabled can
        # fit the instance type.
        node_pools = cluster.get('nodePools', [])
        for node_pool in node_pools:
            name = node_pool.get('name', '')
            logger.debug(f'checking if node pool {name} '
                         'has autoscaling enabled.')
            autoscaling_enabled = (node_pool.get('autoscaling',
                                                 {}).get('enabled', False))
            if autoscaling_enabled:
                logger.debug(f'node pool {name} has autoscaling enabled. '
                             'Checking if it can create a node '
                             f'satisfying {instance_type}')
                try:
                    if cls._check_instance_fits_gke_autoscaler_node_pool(
                            instance_type, node_pool):
                        return True
                except KeyError:
                    logger.debug('encountered KeyError while checking if '
                                 f'node pool {name} can create a node '
                                 f'satisfying {instance_type}.')
                    return True
        return False

    @classmethod
    @annotations.lru_cache(scope='request', maxsize=10)
    def get_available_machine_types(cls, context: str) -> List[str]:
        """Returns the list of machine types that are available in the cluster.
        """
        # Assume context naming convention of
        # gke_PROJECT-ID_LOCATION_CLUSTER-NAME
        valid, project_id, location, cluster_name = cls._validate_context_name(
            context)
        if not valid:
            # Context name is not in the format of
            # gke_PROJECT-ID_LOCATION_CLUSTER-NAME.
            # Cannot determine if the context can autoscale.
            # Return empty list.
            logger.debug(f'Context {context} is not in the format of '
                         f'gke_PROJECT-ID_LOCATION_CLUSTER-NAME. '
                         'Returning empty machine type list.')
            return []
        try:
            logger.debug(
                f'Attempting to get information about cluster {cluster_name}')
            container_service = gcp.build('container',
                                          'v1',
                                          credentials=None,
                                          cache_discovery=False)
            cluster = container_service.projects().locations().clusters().get(
                name=f'projects/{project_id}'
                f'/locations/{location}'
                f'/clusters/{cluster_name}').execute()
        except ImportError:
            # If the gcp module is not installed, return empty list.
            # Remind the user once per day to install the gcp module for better
            # pod scheduling with GKE autoscaler.
            if time.time() - cls._pip_install_gcp_hint_last_sent > 60 * 60 * 24:
                logger.info(
                    'Could not fetch autoscaler information from GKE. '
                    'Run pip install "skypilot[gcp]" for more intelligent pod '
                    'scheduling with GKE autoscaler.')
                cls._pip_install_gcp_hint_last_sent = time.time()
            return []
        except gcp.http_error_exception() as e:
            # Cluster information is not available.
            # Return empty list.
            logger.debug(f'{e.message}', exc_info=True)
            return []

        machine_types = []
        # Get the list of machine types that are available in the cluster.
        node_pools = cluster.get('nodePools', [])
        for node_pool in node_pools:
            name = node_pool.get('name', '')
            logger.debug(f'Checking if node pool {name} '
                         'has autoscaling enabled.')
            autoscaling_enabled = (node_pool.get('autoscaling',
                                                 {}).get('enabled', False))
            if autoscaling_enabled:
                logger.debug(f'Node pool {name} has autoscaling enabled.')
                try:
                    machine_type = node_pool.get('config',
                                                 {}).get('machineType', '')
                    if machine_type:
                        machine_types.append(machine_type)
                except KeyError:
                    logger.debug(f'Encountered KeyError while checking machine '
                                 f'type of node pool {name}.')
                    continue
        return machine_types

    @classmethod
    def _validate_context_name(cls, context: str) -> Tuple[bool, str, str, str]:
        """Validates the context name is in the format of
        gke_PROJECT-ID_LOCATION_CLUSTER-NAME
        Returns:
            bool: True if the context name is in the format of
                gke_PROJECT-ID_LOCATION_CLUSTER-NAME
            str: project id
            str: location
            str: cluster name
        """
        context_components = context.split('_')
        if len(context_components) != 4 or context_components[0] != 'gke':
            logger.debug(
                f'context {context} is not in valid GKE context format.')
            return False, '', '', ''

        logger.debug(f'context {context} is in valid GKE context format.')
        return True, context_components[1], context_components[
            2], context_components[3]

    @classmethod
    def _check_instance_fits_gke_autoscaler_node_pool(
        cls, instance_type: str, node_pool: dict
    ) -> bool:  # check if there are any spare capacity in the autoscaler.
        node_pool_name = node_pool['name']
        logger.debug(
            f'checking if autoscale-enabled node pool {node_pool_name} '
            f'can create a node satisfying {instance_type}')
        k8s_instance_type = KubernetesInstanceType.\
            from_instance_type(instance_type)
        node_config = node_pool['config']
        machine_type = node_config['machineType']

        # Accelerator check
        requested_acc_type = k8s_instance_type.accelerator_type
        requested_acc_count = k8s_instance_type.accelerator_count
        acc_is_tpu = (requested_acc_type is not None and
                      is_tpu_on_gke(requested_acc_type))
        if requested_acc_type is not None:
            assert requested_acc_count is not None, (requested_acc_type,
                                                     requested_acc_count)
            accelerator_exists = False
            if acc_is_tpu:
                # Accelerator type is a TPU.
                logger.debug(
                    f'checking {node_pool_name} for TPU {requested_acc_type}:'
                    f'{requested_acc_count}')
                if 'resourceLabels' in node_config:
                    requested_acc_type, requested_acc_count = normalize_tpu_accelerator_name(
                        requested_acc_type)
                    accelerator_exists = cls._node_pool_has_tpu_capacity(
                        node_config['resourceLabels'], machine_type,
                        requested_acc_type, requested_acc_count)
            else:
                # Accelerator type is a GPU.
                logger.debug(
                    f'checking {node_pool_name} for GPU {requested_acc_type}:'
                    f'{requested_acc_count}')
                if 'accelerators' in node_config:
                    accelerator_exists = cls._node_pool_has_gpu_capacity(
                        node_config['accelerators'], requested_acc_type,
                        requested_acc_count)

            if not accelerator_exists:
                logger.debug(f'{node_pool_name} does not have accelerators '
                             f'{requested_acc_type}:{requested_acc_count}')
                return False

        # vcpu and memory check is not supported for TPU instances.
        # TODO(seungjin): Correctly account for vcpu/memory for TPUs.
        if acc_is_tpu:
            # vcpu and memory check
            logger.debug(f'vcpu and memory check is not supported for TPUs. '
                         'Skipping vcpu and memory check for node pool '
                         f'{node_pool_name}.')
            return True

        vcpus, mem = clouds.GCP.get_vcpus_mem_from_instance_type(machine_type)
        if vcpus is not None and vcpus < k8s_instance_type.cpus:
            logger.debug(f'vcpu check failed for {machine_type} '
                         f'on node pool {node_pool_name}')
            return False
        if mem is not None and mem < k8s_instance_type.memory:
            logger.debug(f'memory check failed for {machine_type} '
                         f'on node pool {node_pool_name}')
            return False

        logger.debug(f'node pool {node_pool_name} can create a node '
                     f'satisfying {instance_type}')
        return True

    @classmethod
    def _node_pool_has_gpu_capacity(cls, node_pool_accelerators: List[dict],
                                    requested_gpu_type: str,
                                    requested_gpu_count: int) -> bool:
        """Check if the node pool has enough GPU capacity
        to fit the instance type.
        """
        for accelerator in node_pool_accelerators:
            node_accelerator_type = (
                GKELabelFormatter.get_accelerator_from_label_value(
                    accelerator['acceleratorType']))
            node_accelerator_count = accelerator['acceleratorCount']
            if node_accelerator_type == requested_gpu_type and int(
                    node_accelerator_count) >= requested_gpu_count:
                return True
        return False

    @classmethod
    def _node_pool_has_tpu_capacity(cls, node_pool_resource_labels: dict,
                                    machine_type: str, requested_tpu_type: str,
                                    requested_tpu_count: int) -> bool:
        """Check if the node pool has enough TPU capacity
        to fit the instance type.
        """

        if 'goog-gke-tpu-node-pool-type' not in node_pool_resource_labels:
            # This node does not have TPUs.
            return False
        if cls._is_node_multi_host_tpu(node_pool_resource_labels):
            # This node is a multi-host TPU.
            # multi-host TPUs are not supported in SkyPilot yet.
            return False
        node_tpu_type = node_pool_resource_labels['goog-gke-accelerator-type']
        # infer chip count from instance type
        tpu_chip_count = cls._tpu_chip_count_from_instance_type(machine_type)

        # For TPUs, the number of requested TPU count
        # must exactly match the TPU count in the instance.
        return (node_tpu_type == requested_tpu_type and
                tpu_chip_count == requested_tpu_count)

    @classmethod
    def _tpu_chip_count_from_instance_type(cls, machine_type: str) -> int:
        """Infer the number of TPU chips from the instance type."""
        # according to
        # https://cloud.google.com/kubernetes-engine/docs/concepts/tpus#machine_type
        # GKE TPU machine types have the format of
        # ct<version>-<type>-<node-chip-count>t
        logger.debug(
            f'inferring TPU chip count from machine type: {machine_type}')
        pattern = r'ct[a-z0-9]+-[a-z]+-([0-9]+)t'
        search = re.search(pattern, machine_type)
        if search is None:
            logger.debug(f'machine type {machine_type} is not a '
                         'valid TPU machine type format.')
            return 0
        num_tpu_chips = search.group(1)
        logger.debug(
            f'machine type {machine_type} has {num_tpu_chips} TPU chips.')
        return int(num_tpu_chips)

    @classmethod
    def _is_node_multi_host_tpu(cls, resource_labels: dict) -> bool:
        """Check if the node pool is a multi-host TPU."""
        return ('goog-gke-tpu-node-pool-type' in resource_labels and
                resource_labels['goog-gke-tpu-node-pool-type'] == 'multi-host')


class KarpenterAutoscaler(Autoscaler):
    """Karpenter autoscaler
    """

    label_formatter: Any = KarpenterLabelFormatter
    can_query_backend: bool = False


class GenericAutoscaler(Autoscaler):
    """Generic autoscaler
    """

    label_formatter: Any = SkyPilotLabelFormatter
    can_query_backend: bool = False


# Mapping of autoscaler type to autoscaler
AUTOSCALER_TYPE_TO_AUTOSCALER = {
    kubernetes_enums.KubernetesAutoscalerType.GKE: GKEAutoscaler,
    kubernetes_enums.KubernetesAutoscalerType.KARPENTER: KarpenterAutoscaler,
    kubernetes_enums.KubernetesAutoscalerType.GENERIC: GenericAutoscaler,
}


def get_autoscaler(autoscaler_type: kubernetes_enums.KubernetesAutoscalerType):
    return AUTOSCALER_TYPE_TO_AUTOSCALER.get(autoscaler_type, Autoscaler)


@annotations.lru_cache(scope='request', maxsize=10)
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
    nodes = get_kubernetes_nodes(context=context)
    for node in nodes:
        cluster_resources.update(node.status.allocatable.keys())
    has_accelerator = (get_gpu_resource_key() in cluster_resources or
                       TPU_RESOURCE_KEY in cluster_resources)

    return has_accelerator, cluster_resources


@annotations.lru_cache(scope='request', maxsize=10)
@_retry_on_error(resource_type='node')
def get_kubernetes_nodes(*, context: Optional[str] = None) -> List[Any]:
    """Gets the kubernetes nodes in the context.

    If context is None, gets the nodes in the current context.
    """
    if context is None:
        context = get_current_kube_config_context_name()

    nodes = kubernetes.core_api(context).list_node(
        _request_timeout=kubernetes.API_TIMEOUT).items
    return nodes


@_retry_on_error(resource_type='pod')
def get_all_pods_in_kubernetes_cluster(*,
                                       context: Optional[str] = None
                                      ) -> List[Any]:
    """Gets pods in all namespaces in kubernetes cluster indicated by context.

    Used for computing cluster resource usage.
    """
    if context is None:
        context = get_current_kube_config_context_name()

    pods = kubernetes.core_api(context).list_pod_for_all_namespaces(
        _request_timeout=kubernetes.API_TIMEOUT).items
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
            # We don't consider nodes that have exactly the same amount of
            # CPU or memory as the candidate instance type.
            # This is to account for the fact that each node always has some
            # amount kube-system pods running on it and consuming resources.
            if (node_cpus > candidate_instance_type.cpus and
                    node_memory_gb > candidate_instance_type.memory):
                return True, None
        return False, (
            'Maximum resources found on a single node: '
            f'{max_cpu} CPUs, {common_utils.format_float(max_mem)}G Memory')

    def check_tpu_fits(acc_type: str, acc_count: int,
                       node_list: List[Any]) -> Tuple[bool, Optional[str]]:
        """Checks if the instance fits on the cluster based on requested TPU.

        It checks if the TPU type and count on each node match the required
        number of TPU chips for the instance. In the case of multi-host TPU
        podslice, the function ensures that the number of TPU chips on a single
        node (node_tpu_chip_count) and the total TPU chips across the entire
        podslice (topology_chip_count) are correctly handled.
        """
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

    nodes = get_kubernetes_nodes(context=context)
    k8s_instance_type = KubernetesInstanceType.\
        from_instance_type(instance)
    acc_type = k8s_instance_type.accelerator_type
    acc_count = k8s_instance_type.accelerator_count
    if acc_type is not None:
        # If GPU/TPUs are requested, check if GPU/TPU type is available, and
        # if so, check if CPU and memory requirements on the specific node are
        # met.
        assert acc_count is not None, (acc_type, acc_count)
        try:
            gpu_label_key, gpu_label_values, _, _ = (
                get_accelerator_label_key_values(context, acc_type, acc_count))
            if gpu_label_values is None:
                gpu_label_values = []
        except exceptions.ResourcesUnavailableError as e:
            # If GPU not found, return empty list and error message.
            return False, str(e)
        # Get the set of nodes that have the GPU type
        gpu_nodes = [
            node for node in nodes if gpu_label_key in node.metadata.labels and
            node.metadata.labels[gpu_label_key] in gpu_label_values
        ]
        if not gpu_nodes:
            return False, f'No GPU nodes found with {acc_type} on the cluster'
        if is_tpu_on_gke(acc_type):
            # If requested accelerator is a TPU type, check if the cluster
            # has sufficient TPU resource to meet the requirement.
            acc_type, acc_count = normalize_tpu_accelerator_name(acc_type)
            fits, reason = check_tpu_fits(acc_type, acc_count, gpu_nodes)
            if reason is not None:
                return fits, reason
        else:
            # Check if any of the GPU nodes have sufficient number of GPUs.
            gpu_nodes = [
                node for node in gpu_nodes if
                get_node_accelerator_count(node.status.allocatable) >= acc_count
            ]
            if not gpu_nodes:
                return False, (
                    f'No GPU nodes found with {acc_count} or more GPUs.')

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


def get_accelerator_label_keys(context: Optional[str],) -> List[str]:
    """Returns the label keys that should be avoided for scheduling
    CPU-only tasks.
    """
    label_formatter, _ = detect_gpu_label_formatter(context)
    if label_formatter is None:
        return []
    return label_formatter.get_label_keys()


def get_accelerator_label_key_values(
    context: Optional[str],
    acc_type: str,
    acc_count: int,
    check_mode=False
) -> Tuple[Optional[str], Optional[List[str]], Optional[str], Optional[str]]:
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
            - The cluster has GPU/TPU resources, but no node in the cluster has
              an accelerator label.
            - The cluster has a node with an invalid accelerator label value.
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

    is_ssh_node_pool = context.startswith('ssh-') if context else False
    cloud_name = 'SSH Node Pool' if is_ssh_node_pool else 'Kubernetes cluster'
    context_display_name = common_utils.removeprefix(
        context, 'ssh-') if (context and is_ssh_node_pool) else context

    autoscaler_type = skypilot_config.get_effective_region_config(
        cloud='kubernetes',
        region=context,
        keys=('autoscaler',),
        default_value=None)
    if autoscaler_type is not None:
        # If autoscaler is set in config.yaml, override the label key and value
        # to the autoscaler's format and bypass the GPU checks.
        if check_mode:
            # If check mode is enabled and autoscaler is set, we can return
            # early since we assume the cluster autoscaler will handle GPU
            # node provisioning.
            return None, None, None, None
        autoscaler = AUTOSCALER_TYPE_TO_AUTOSCALER.get(
            kubernetes_enums.KubernetesAutoscalerType(autoscaler_type))
        assert autoscaler is not None, ('Unsupported autoscaler type:'
                                        f' {autoscaler_type}')
        formatter = autoscaler.label_formatter
        tpu_topology_label_key = None
        tpu_topology_label_value = None
        if is_tpu_on_gke(acc_type):
            assert formatter == GKELabelFormatter, formatter
            tpu_topology_label_key = formatter.get_tpu_topology_label_key()
            tpu_topology_label_value = formatter.get_tpu_topology_label_value(
                acc_type, acc_count)
        return formatter.get_label_key(acc_type), formatter.get_label_values(
            acc_type), tpu_topology_label_key, tpu_topology_label_value

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
                msg = (f'Could not detect GPU labels in {cloud_name}.')
                if not is_ssh_node_pool:
                    msg += (' Run `sky check ssh` to debug.')
                else:
                    msg += (
                        ' If this cluster has GPUs, please ensure GPU nodes have '
                        'node labels of either of these formats: '
                        f'{supported_formats}. Please refer to '
                        'the documentation on how to set up node labels.')
                msg += f'{suffix}'
                raise exceptions.ResourcesUnavailableError(msg)
        else:
            # Validate the label value on all nodes labels to ensure they are
            # correctly setup and will behave as expected.
            for node_name, label_list in node_labels.items():
                for label, value in label_list:
                    if label_formatter.match_label_key(label):
                        is_valid, reason = label_formatter.validate_label_value(
                            value)
                        if not is_valid:
                            raise exceptions.ResourcesUnavailableError(
                                f'Node {node_name!r} in {cloud_name} has '
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
                                value).lower() == acc_type.lower()):
                        if is_tpu_on_gke(acc_type):
                            assert isinstance(label_formatter,
                                              GKELabelFormatter)
                            if node_metadata_labels.get(
                                    label_formatter.TPU_LABEL_KEY) == acc_type:
                                topology_label_key = (
                                    label_formatter.get_tpu_topology_label_key(
                                    ))
                                # Instead of using get_tpu_topology_label_value,
                                # we use the node's label value to determine the
                                # topology. This is to make sure the node's
                                # available topology matches our request.
                                topology_value = node_metadata_labels.get(
                                    topology_label_key)
                                assert topology_value is not None
                                tpu_topology_chip_count = reduce_tpu_topology(
                                    topology_value)
                                # For single-host TPUs, there aren't multiple
                                # different topologies that maps to identical
                                # number of TPU chips.
                                if tpu_topology_chip_count == acc_count:
                                    return (label, [value], topology_label_key,
                                            topology_value)
                                else:
                                    continue
                        else:
                            return label, [value], None, None

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
                    f'Could not find any node in the {cloud_name} '
                    f'with {acc_type}. Please ensure at least one node in the '
                    f'cluster has {acc_type} and node labels are setup '
                    'correctly. Please refer to the documentation for more. '
                    f'{suffix}. Note that multi-host TPU podslices are '
                    'currently not unsupported.')
    else:
        # If GPU resources are not detected, raise error
        with ux_utils.print_exception_no_traceback():
            suffix = ''
            if env_options.Options.SHOW_DEBUG_INFO.get():
                suffix = (' Available resources on the cluster: '
                          f'{cluster_resources}')
            if is_ssh_node_pool:
                msg = (
                    f'Could not detect GPUs in SSH Node Pool '
                    f'\'{context_display_name}\'. If this cluster contains '
                    'GPUs, please ensure GPU drivers are installed on the node '
                    'and re-run '
                    f'`sky ssh up --infra {context_display_name}`. {suffix}')
            else:
                msg = (
                    f'Could not detect GPU/TPU resources ({GPU_RESOURCE_KEY!r} or '
                    f'{TPU_RESOURCE_KEY!r}) in Kubernetes cluster. If this cluster'
                    ' contains GPUs, please ensure GPU drivers are installed on '
                    'the node. Check if the GPUs are setup correctly by running '
                    '`kubectl describe nodes` and looking for the '
                    f'{GPU_RESOURCE_KEY!r} or {TPU_RESOURCE_KEY!r} resource. '
                    'Please refer to the documentation on how to set up GPUs.'
                    f'{suffix}')
            raise exceptions.ResourcesUnavailableError(msg)
    assert False, 'This should not be reached'


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
                      timeout: int = kubernetes.API_TIMEOUT,
                      run_optional_checks: bool = False) -> \
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

    # Check if $KUBECONFIG envvar consists of multiple paths. We run this before
    # optional checks.
    try:
        _ = get_kubeconfig_paths()
    except ValueError as e:
        return False, f'{common_utils.format_exception(e, use_bracket=True)}'

    # If we reach here, the credentials are valid and Kubernetes cluster is up.
    if not run_optional_checks:
        return True, None

    # We now do softer checks to check if exec based auth is used and to
    # see if the cluster is GPU-enabled.
    _, exec_msg = is_kubeconfig_exec_auth(context)

    # We now check if GPUs are available and labels are set correctly on the
    # cluster, and if not we return hints that may help debug any issues.
    # This early check avoids later surprises for user when they try to run
    # `sky launch --gpus <gpu>` and the optimizer does not list Kubernetes as a
    # provider if their cluster GPUs are not setup correctly.
    gpu_msg = ''
    unlabeled_nodes = get_unlabeled_accelerator_nodes(context)
    if unlabeled_nodes:
        gpu_msg = (f'Cluster has {len(unlabeled_nodes)} nodes with '
                   f'accelerators that are not labeled. '
                   f'To label the nodes, run '
                   f'`python -m sky.utils.kubernetes.gpu_labeler '
                   f'--context {context}`')
    else:
        try:
            # This function raises a ResourcesUnavailableError in three cases:
            # 1. If no node in cluster has GPU/TPU resource in its capacity.
            #    (e.g. google.com/tpu, nvidia.com/gpu)
            # 2. If at least one node in cluster has GPU/TPU resource in its
            #    capacity, but no node in the cluster has an accelerator label.
            # 3. If an accelerator label on a node is invalid.
            # Exception 2 is a special case of a cluster having at least one
            # unlabelled node, which is caught in
            # `get_unlabeled_accelerator_nodes`.
            # Therefore, if `get_unlabeled_accelerator_nodes` detects unlabelled
            # nodes, we skip this check.
            get_accelerator_label_key_values(context,
                                             acc_type='',
                                             acc_count=0,
                                             check_mode=True)
        except exceptions.ResourcesUnavailableError as e:
            # If GPUs are not available, we return cluster as enabled
            # (since it can be a CPU-only cluster) but we also return the
            # exception message which serves as a hint for how to enable
            # GPU access.
            gpu_msg = str(e)
    if exec_msg and gpu_msg:
        return True, f'{gpu_msg}\n    Additionally, {exec_msg}'
    elif gpu_msg:
        return True, gpu_msg
    elif exec_msg:
        return True, exec_msg
    else:
        return True, None


def check_pod_config(pod_config: dict) \
    -> Tuple[bool, Optional[str]]:
    """Check if the pod_config is a valid pod config

    Using deserialize api to check the pod_config is valid or not.

    Returns:
        bool: True if pod_config is valid.
        str: Error message about why the pod_config is invalid, None otherwise.
    """
    errors = []
    # This api_client won't be used to send any requests, so there is no need to
    # load kubeconfig
    api_client = kubernetes.kubernetes.client.ApiClient()

    # Used for kubernetes api_client deserialize function, the function will use
    # data attr, the detail ref:
    # https://github.com/kubernetes-client/python/blob/master/kubernetes/client/api_client.py#L244
    class InnerResponse():

        def __init__(self, data: dict):
            self.data = json.dumps(data)

    try:
        # Validate metadata if present
        if 'metadata' in pod_config:
            try:
                value = InnerResponse(pod_config['metadata'])
                api_client.deserialize(
                    value, kubernetes.kubernetes.client.V1ObjectMeta)
            except ValueError as e:
                errors.append(f'Invalid metadata: {str(e)}')
        # Validate spec if present
        if 'spec' in pod_config:
            try:
                value = InnerResponse(pod_config['spec'])
                api_client.deserialize(value,
                                       kubernetes.kubernetes.client.V1PodSpec)
            except ValueError as e:
                errors.append(f'Invalid spec: {str(e)}')
        return len(errors) == 0, '.'.join(errors)
    except Exception as e:  # pylint: disable=broad-except
        errors.append(f'Validation error: {str(e)}')
        return False, '.'.join(errors)


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
    if context == kubernetes.in_cluster_context_name():
        # If in-cluster config is used, exec-based auth is not used.
        return False, None
    try:
        k8s.config.load_kube_config()
    except kubernetes.config_exception():
        # Using service account token or other auth methods, continue
        return False, None

    # Get active context and user from kubeconfig using k8s api
    all_contexts, current_context = kubernetes.list_kube_config_contexts()
    context_obj = current_context
    if context is not None:
        for c in all_contexts:
            if c['name'] == context:
                context_obj = c
                break
        else:
            raise ValueError(f'Kubernetes context {context!r} not found.')
    target_username = context_obj['context']['user']

    # Load the kubeconfig for the context
    kubeconfig_text = _get_kubeconfig_text_for_context(context)
    kubeconfig = yaml.safe_load(kubeconfig_text)

    # Get the user details
    user_details = kubeconfig['users']

    # Find user matching the target username
    user_details = next(
        user for user in user_details if user['name'] == target_username)

    remote_identity = skypilot_config.get_effective_region_config(
        cloud='kubernetes',
        region=context,
        keys=('remote_identity',),
        default_value=schemas.get_default_remote_identity('kubernetes'))
    if ('exec' in user_details.get('user', {}) and remote_identity
            == schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value):
        ctx_name = context_obj['name']
        exec_msg = ('exec-based authentication is used for '
                    f'Kubernetes context {ctx_name!r}. '
                    'Make sure that the corresponding cloud provider is '
                    'also enabled through `sky check` (e.g.: GCP for GKE). '
                    'Alternatively, configure SkyPilot to create a service '
                    'account for running pods by setting the following in '
                    '~/.sky/config.yaml:\n'
                    '    kubernetes:\n'
                    '      remote_identity: SERVICE_ACCOUNT\n'
                    '    More: https://docs.skypilot.co/en/latest/'
                    'reference/config.html')
        return True, exec_msg
    return False, None


def _get_kubeconfig_text_for_context(context: Optional[str] = None) -> str:
    """Get the kubeconfig text for the given context.

    The kubeconfig might be multiple files, this function use kubectl to
    handle merging automatically.
    """
    command = 'kubectl config view --minify'
    if context is not None:
        command += f' --context={context}'

    # Ensure subprocess inherits the current environment properly
    # This fixes the issue where kubectl can't find ~/.kube/config in API server context
    env = os.environ.copy()

    proc = subprocess.run(command,
                          shell=True,
                          check=False,
                          env=env,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(
            f'Failed to get kubeconfig text for context {context}: {proc.stderr.decode("utf-8")}'
        )
    return proc.stdout.decode('utf-8')


@annotations.lru_cache(scope='request')
def get_current_kube_config_context_name() -> Optional[str]:
    """Get the current kubernetes context from the kubeconfig file

    Returns:
        str | None: The current kubernetes context if it exists, None otherwise
    """
    k8s = kubernetes.kubernetes
    try:
        _, current_context = kubernetes.list_kube_config_contexts()
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


def get_all_kube_context_names() -> List[str]:
    """Get all kubernetes context names available in the environment.

    Fetches context names from the kubeconfig file and in-cluster auth, if any.

    If running in-cluster and IN_CLUSTER_CONTEXT_NAME_ENV_VAR is not set,
    returns the default in-cluster kubernetes context name.

    We should not cache the result of this function as the admin policy may
    update the contexts.

    Returns:
        List[Optional[str]]: The list of kubernetes context names if
            available, an empty list otherwise.
    """
    k8s = kubernetes.kubernetes
    context_names = []
    try:
        all_contexts, _ = kubernetes.list_kube_config_contexts()
        # all_contexts will always have at least one context. If kubeconfig
        # does not have any contexts defined, it will raise ConfigException.
        context_names = [context['name'] for context in all_contexts]
    except k8s.config.config_exception.ConfigException:
        # If no config found, continue
        pass
    if is_incluster_config_available():
        context_names.append(kubernetes.in_cluster_context_name())
    return context_names


@annotations.lru_cache(scope='request')
def get_kube_config_context_namespace(
        context_name: Optional[str] = None) -> str:
    """Get the current kubernetes context namespace from the kubeconfig file

    Returns:
        str: The current kubernetes context namespace if it exists, else
            the default namespace.
    """
    k8s = kubernetes.kubernetes
    ns_path = '/var/run/secrets/kubernetes.io/serviceaccount/namespace'
    # If using in-cluster context, first check for the environment variable,
    # then fall back to the service account namespace file. Uses the same logic
    # as adaptors.kubernetes._load_config() to stay consistent with in-cluster
    # config loading.
    if (context_name == kubernetes.in_cluster_context_name() or
            context_name is None):
        # First check for environment variable. We allow the env var to take
        # effect only when using in-cluster auth because the recommended way to
        # set the namespace when using kubeconfig is to change the namespace
        # configured in the context.
        env_namespace = os.getenv(
            kubernetes_constants.KUBERNETES_IN_CLUSTER_NAMESPACE_ENV_VAR)
        if env_namespace:
            return env_namespace
        # Fall back to service account namespace file
        if os.path.exists(ns_path):
            with open(ns_path, encoding='utf-8') as f:
                return f.read().strip()
    # If not in-cluster, get the namespace from kubeconfig
    try:
        contexts, current_context = kubernetes.list_kube_config_contexts()
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
    appending "--{type}:{a}" where type is the accelerator type and a
    is the number of accelerators.
    CPU and memory can be specified as floats. Accelerator count must be int.
    Examples:
        - 4CPU--16GB
        - 0.5CPU--1.5GB
        - 4CPU--16GB--V100:1
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
            # Replace spaces with underscores in accelerator type to make it a
            # valid logical instance type name.
            assert self.accelerator_type is not None, self.accelerator_count
            acc_name = self.accelerator_type.replace(' ', '_')
            name += f'--{acc_name}:{self.accelerator_count}'
        return name

    @staticmethod
    def is_valid_instance_type(name: str) -> bool:
        """Returns whether the given name is a valid instance type."""
        # Before https://github.com/skypilot-org/skypilot/pull/4756,
        # the accelerators are appended with format "--{a}{type}",
        # e.g. "4CPU--16GB--1V100".
        # Check both patterns to keep backward compatibility.
        # TODO(romilb): Backward compatibility, remove after 0.11.0.
        prev_pattern = re.compile(
            r'^(\d+(\.\d+)?CPU--\d+(\.\d+)?GB)(--\d+\S+)?$')
        pattern = re.compile(
            r'^(\d+(\.\d+)?CPU--\d+(\.\d+)?GB)(--[\w\d-]+:\d+)?$')
        return bool(pattern.match(name)) or bool(prev_pattern.match(name))

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
            r'^(?P<cpus>\d+(\.\d+)?)CPU--(?P<memory>\d+(\.\d+)?)GB(?:--(?P<accelerator_type>[\w\d-]+):(?P<accelerator_count>\d+))?$'  # pylint: disable=line-too-long
        )
        match = pattern.match(name)
        # TODO(romilb): Backward compatibility, remove after 0.11.0.
        prev_pattern = re.compile(
            r'^(?P<cpus>\d+(\.\d+)?)CPU--(?P<memory>\d+(\.\d+)?)GB(?:--(?P<accelerator_count>\d+)(?P<accelerator_type>\S+))?$'  # pylint: disable=line-too-long
        )
        prev_match = prev_pattern.match(name)
        if match:
            cpus = float(match.group('cpus'))
            memory = float(match.group('memory'))
            accelerator_count = match.group('accelerator_count')
            accelerator_type = match.group('accelerator_type')
            if accelerator_count:
                accelerator_count = int(accelerator_count)
                # This is to revert the accelerator types with spaces back to
                # the original format.
                accelerator_type = str(accelerator_type).replace('_', ' ')
            else:
                accelerator_count = None
                accelerator_type = None
            return cpus, memory, accelerator_count, accelerator_type
        # TODO(romilb): Backward compatibility, remove after 0.11.0.
        elif prev_match:
            cpus = float(prev_match.group('cpus'))
            memory = float(prev_match.group('memory'))
            accelerator_count = prev_match.group('accelerator_count')
            accelerator_type = prev_match.group('accelerator_type')
            if accelerator_count:
                accelerator_count = int(accelerator_count)
                accelerator_type = str(accelerator_type).replace('_', ' ')
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
            name += f'--{accelerator_type}:{accelerator_count}'
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
                              r'-W \[%h\]:%p '
                              f'{ssh_jump_user}@{ssh_jump_ip}')
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
    # Return the path to the proxy command script without expanding the user
    # home directory to be compatible when a SSH is called from a client in
    # client-server mode.
    return PORT_FORWARD_PROXY_CMD_PATH


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
    merge_custom_metadata(content['service_spec']['metadata'], context)

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
        merge_custom_metadata(content[object_type]['metadata'], context)

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
        return results[0] if results else None

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


def check_port_forward_mode_dependencies(
        raise_error: bool = True) -> Optional[List[str]]:
    """Checks if 'socat' and 'nc' are installed

    Args:
        raise_error: set to true when the dependencies need to be present.
            set to false for `sky check`, where reason strings are compiled
            at the end.

    Returns: the reasons list if there are missing dependencies.
    """

    # errors
    socat_message = (
        '`socat` is required to setup Kubernetes cloud with '
        f'`{kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD.value}` '  # pylint: disable=line-too-long
        'default networking mode and it is not installed. ')
    netcat_default_message = (
        '`nc` is required to setup Kubernetes cloud with '
        f'`{kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD.value}` '  # pylint: disable=line-too-long
        'default networking mode and it is not installed. ')
    netcat_macos_message = (
        'The default MacOS `nc` is installed. However, for '
        f'`{kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD.value}` '  # pylint: disable=line-too-long
        'default networking mode, GNU netcat is required. ')

    # save
    reasons = []
    required_binaries = []

    # Ensure socat is installed
    try:
        subprocess.run(['socat', '-V'],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        required_binaries.append('socat')
        reasons.append(socat_message)

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
            required_binaries.append('netcat')
            reasons.append(netcat_macos_message)
        elif netcat_output.returncode != 0:
            required_binaries.append('netcat')
            reasons.append(netcat_default_message)

    except FileNotFoundError:
        required_binaries.append('netcat')
        reasons.append(netcat_default_message)

    if required_binaries:
        reasons.extend([
            'On Debian/Ubuntu, install the missing dependenc(ies) with:',
            f'  $ sudo apt install {" ".join(required_binaries)}',
            'On MacOS, install with: ',
            f'  $ brew install {" ".join(required_binaries)}',
        ])
        if raise_error:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('\n'.join(reasons))
        return reasons
    return None


def get_endpoint_debug_message(context: Optional[str] = None) -> str:
    """ Returns a string message for user to debug Kubernetes port opening

    Polls the configured ports mode on Kubernetes to produce an
    appropriate error message with debugging hints.

    Also checks if the
    """
    port_mode = network_utils.get_port_mode(None, context)
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


def combine_pod_config_fields(
    cluster_yaml_path: str,
    cluster_config_overrides: Dict[str, Any],
    cloud: Optional[clouds.Cloud] = None,
    context: Optional[str] = None,
) -> None:
    """Adds or updates fields in the YAML with fields from the
    ~/.sky/config.yaml's kubernetes.pod_spec dict.
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
    # We don't use override_configs in `get_effective_region_config`, as merging
    # the pod config requires special handling.
    if isinstance(cloud, clouds.SSH):
        kubernetes_config = skypilot_config.get_effective_region_config(
            cloud='ssh', region=None, keys=('pod_config',), default_value={})
        override_pod_config = config_utils.get_cloud_config_value_from_dict(
            dict_config=cluster_config_overrides,
            cloud='ssh',
            keys=('pod_config',),
            default_value={})
    else:
        kubernetes_config = skypilot_config.get_effective_region_config(
            cloud='kubernetes',
            region=context,
            keys=('pod_config',),
            default_value={})
        override_pod_config = config_utils.get_cloud_config_value_from_dict(
            dict_config=cluster_config_overrides,
            cloud='kubernetes',
            region=context,
            keys=('pod_config',),
            default_value={})
    config_utils.merge_k8s_configs(kubernetes_config, override_pod_config)

    # Merge the kubernetes config into the YAML for both head and worker nodes.
    config_utils.merge_k8s_configs(
        yaml_obj['available_node_types']['ray_head_default']['node_config'],
        kubernetes_config)

    # Write the updated YAML back to the file
    common_utils.dump_yaml(cluster_yaml_path, yaml_obj)


def combine_metadata_fields(cluster_yaml_path: str,
                            context: Optional[str] = None) -> None:
    """Updates the metadata for all Kubernetes objects created by SkyPilot with
    fields from the ~/.sky/config.yaml's kubernetes.custom_metadata dict.

    Obeys the same add or update semantics as combine_pod_config_fields().
    """

    with open(cluster_yaml_path, 'r', encoding='utf-8') as f:
        yaml_content = f.read()
    yaml_obj = yaml.safe_load(yaml_content)
    custom_metadata = skypilot_config.get_effective_region_config(
        cloud='kubernetes',
        region=context,
        keys=('custom_metadata',),
        default_value={})

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
        config_utils.merge_k8s_configs(destination, custom_metadata)

    # Write the updated YAML back to the file
    common_utils.dump_yaml(cluster_yaml_path, yaml_obj)


def merge_custom_metadata(original_metadata: Dict[str, Any],
                          context: Optional[str] = None) -> None:
    """Merges original metadata with custom_metadata from config

    Merge is done in-place, so return is not required
    """
    custom_metadata = skypilot_config.get_effective_region_config(
        cloud='kubernetes',
        region=context,
        keys=('custom_metadata',),
        default_value={})
    config_utils.merge_k8s_configs(original_metadata, custom_metadata)


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
    merge_custom_metadata(ns_metadata, context)
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


def get_custom_config_k8s_contexts() -> List[str]:
    """Returns the list of context names from the config"""
    contexts = skypilot_config.get_effective_region_config(
        cloud='kubernetes',
        region=None,
        keys=('context_configs',),
        default_value={})
    return [*contexts] or []


# Mapping of known spot label keys and values for different cluster types
# Add new cluster types here if they support spot instances along with the
# corresponding spot label key and value.
SPOT_LABEL_MAP = {
    kubernetes_enums.KubernetesAutoscalerType.GKE.value:
        ('cloud.google.com/gke-spot', 'true')
}


def get_autoscaler_type(
    context: Optional[str] = None
) -> Optional[kubernetes_enums.KubernetesAutoscalerType]:
    """Returns the autoscaler type by reading from config"""
    autoscaler_type = skypilot_config.get_effective_region_config(
        cloud='kubernetes',
        region=context,
        keys=('autoscaler',),
        default_value=None)
    if autoscaler_type is not None:
        autoscaler_type = kubernetes_enums.KubernetesAutoscalerType(
            autoscaler_type)
    return autoscaler_type


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
    for node in get_kubernetes_nodes(context=context):
        for _, (key, value) in SPOT_LABEL_MAP.items():
            if key in node.metadata.labels and node.metadata.labels[
                    key] == value:
                return key, value

    # Check if autoscaler is configured. Allow spot instances if autoscaler type
    # is known to support spot instances.
    autoscaler_type = get_autoscaler_type(context=context)
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


def get_unlabeled_accelerator_nodes(context: Optional[str] = None) -> List[Any]:
    """Gets a list of unlabeled GPU nodes in the cluster.

    This function returns a list of nodes that have GPU resources but no label
    that indicates the accelerator type.

    Args:
        context: The context to check.

    Returns:
        List[Any]: List of unlabeled nodes with accelerators.
    """
    nodes = get_kubernetes_nodes(context=context)
    nodes_with_accelerator = []
    for node in nodes:
        if get_gpu_resource_key() in node.status.capacity:
            nodes_with_accelerator.append(node)

    label_formatter, _ = detect_gpu_label_formatter(context)
    if not label_formatter:
        return nodes_with_accelerator
    else:
        label_keys = label_formatter.get_label_keys()

    unlabeled_nodes = []
    for node in nodes_with_accelerator:
        labeled = False
        for label_key in label_keys:
            if label_key in node.metadata.labels:
                labeled = True
                break
        if not labeled:
            unlabeled_nodes.append(node)

    return unlabeled_nodes


def get_kubernetes_node_info(
        context: Optional[str] = None) -> models.KubernetesNodesInfo:
    """Gets the resource information for all the nodes in the cluster.

    This function returns a model with node info map as a nested field. This
    allows future extensions while keeping the client-server compatibility,
    e.g. when adding a new field to the model, the legacy clients will not be
    affected and new clients can opt-in new behavior if the new field is
    presented.

    Currently only GPU resources are supported. The function returns the total
    number of GPUs available on the node and the number of free GPUs on the
    node.

    If the user does not have sufficient permissions to list pods in all
    namespaces, the function will return free GPUs as -1.

    Returns:
        KubernetesNodesInfo: A model that contains the node info map and other
            information.
    """
    nodes = get_kubernetes_nodes(context=context)
    # Get the pods to get the real-time resource usage
    try:
        pods = get_all_pods_in_kubernetes_cluster(context=context)
    except kubernetes.api_exception() as e:
        if e.status == 403:
            pods = None
        else:
            raise

    lf, _ = detect_gpu_label_formatter(context)
    if not lf:
        label_keys = []
    else:
        label_keys = lf.get_label_keys()

    node_info_dict: Dict[str, models.KubernetesNodeInfo] = {}
    has_multi_host_tpu = False

    for node in nodes:
        accelerator_name = None
        # Determine the accelerator name from the node labels and pick the
        # first one found. We assume that the node has only one accelerator type
        # (e.g., either GPU or TPU).
        for label_key in label_keys:
            if lf is not None and label_key in node.metadata.labels:
                accelerator_name = lf.get_accelerator_from_label_value(
                    node.metadata.labels.get(label_key))
                break

        # Extract IP address from node addresses (prefer external, fallback to internal)
        node_ip = None
        if node.status.addresses:
            # First try to find external IP
            for address in node.status.addresses:
                if address.type == 'ExternalIP':
                    node_ip = address.address
                    break
            # If no external IP, try to find internal IP
            if node_ip is None:
                for address in node.status.addresses:
                    if address.type == 'InternalIP':
                        node_ip = address.address
                        break

        allocated_qty = 0
        accelerator_count = get_node_accelerator_count(node.status.allocatable)

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
            has_multi_host_tpu = True
            continue

        node_info_dict[node.metadata.name] = models.KubernetesNodeInfo(
            name=node.metadata.name,
            accelerator_type=accelerator_name,
            total={'accelerator_count': int(accelerator_count)},
            free={'accelerators_available': int(accelerators_available)},
            ip_address=node_ip)
    hint = ''
    if has_multi_host_tpu:
        hint = ('(Note: Multi-host TPUs are detected and excluded from the '
                'display as multi-host TPUs are not supported.)')

    return models.KubernetesNodesInfo(
        node_info_dict=node_info_dict,
        hint=hint,
    )


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


@timeline.event
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
    ray_config = global_user_state.get_cluster_yaml_dict(handle.cluster_yaml)
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
    if context == kubernetes.in_cluster_context_name():
        # If the context (also used as the region) is in-cluster, we need
        # to use in-cluster auth by setting the context to None.
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
    """Determines if the given accelerator is a TPU supported on GKE."""
    normalized, _ = normalize_tpu_accelerator_name(accelerator)
    return normalized in GKE_TPU_ACCELERATOR_TO_GENERATION


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
    gpu_resource_name = get_gpu_resource_key()
    assert not (gpu_resource_name in attribute_dict and
                TPU_RESOURCE_KEY in attribute_dict)
    if gpu_resource_name in attribute_dict:
        return int(attribute_dict[gpu_resource_name])
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


@dataclasses.dataclass
class KubernetesSkyPilotClusterInfoPayload:
    """SkyPilot Cluster on Kubernetes payload."""
    cluster_name_on_cloud: str
    cluster_name: str
    user: str
    status: status_lib.ClusterStatus
    resources_str: str
    launched_at: float

    @classmethod
    def from_cluster(
        cls, cluster: KubernetesSkyPilotClusterInfo
    ) -> 'KubernetesSkyPilotClusterInfoPayload':
        resources_str = f'{len(cluster.pods)}x {cluster.resources}'
        return cls(
            cluster_name_on_cloud=cluster.cluster_name_on_cloud,
            cluster_name=cluster.cluster_name,
            user=cluster.user,
            status=cluster.status,
            resources_str=resources_str,
            launched_at=cluster.launched_at,
        )


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


def get_gpu_resource_key():
    """Get the GPU resource name to use in kubernetes.
    The function first checks for an environment variable.
    If defined, it uses its value; otherwise, it returns the default value.
    Args:
        name (str): Default GPU resource name, default is "nvidia.com/gpu".
    Returns:
        str: The selected GPU resource name.
    """
    # Retrieve GPU resource name from environment variable, if set.
    # Else use default.
    # E.g., can be nvidia.com/gpu-h100, amd.com/gpu etc.
    return os.getenv('CUSTOM_GPU_RESOURCE_KEY', default=GPU_RESOURCE_KEY)


def get_kubeconfig_paths() -> List[str]:
    """Get the path to the kubeconfig files.
    Parses `KUBECONFIG` env var if present, else uses the default path.
    """
    # We should always use the latest KUBECONFIG environment variable to
    # make sure env var overrides get respected.
    paths = os.getenv('KUBECONFIG', kubernetes.DEFAULT_KUBECONFIG_PATH)
    expanded = []
    for path in paths.split(kubernetes.ENV_KUBECONFIG_PATH_SEPARATOR):
        expanded.append(os.path.expanduser(path))
    return expanded


def format_kubeconfig_exec_auth(config: Any,
                                output_path: str,
                                inject_wrapper: bool = True) -> bool:
    """Reformat the kubeconfig so that exec-based authentication can be used
    with SkyPilot. Will create a new kubeconfig file under <output_path>
    regardless of whether a change has been made.

    kubectl internally strips all environment variables except for system
    defaults. If `inject_wrapper` is true, a wrapper executable is applied
    to inject the relevant PATH information before exec-auth is executed.

    Contents of sky-kube-exec-wrapper:

    #!/bin/bash
    export PATH="$HOME/skypilot-runtime/bin:$HOME/google-cloud-sdk:$PATH"
    exec "$@"

    refer to `skylet/constants.py` for more information.

    Args:
        config (dict): kubeconfig parsed by yaml.safe_load
        output_path (str): Path where the potentially modified kubeconfig file
          will be saved
        inject_wrapper (bool): Whether to inject the wrapper script
    Returns: whether config was updated, for logging purposes
    """
    updated = False
    for user in config.get('users', []):
        exec_info = user.get('user', {}).get('exec', {})
        current_command = exec_info.get('command', '')

        if current_command:
            # Strip the path and keep only the executable name
            executable = os.path.basename(current_command)
            if executable == kubernetes_constants.SKY_K8S_EXEC_AUTH_WRAPPER:
                # we don't want this happening recursively.
                continue

            if inject_wrapper:
                exec_info[
                    'command'] = kubernetes_constants.SKY_K8S_EXEC_AUTH_WRAPPER
                if exec_info.get('args') is None:
                    exec_info['args'] = []
                exec_info['args'].insert(0, executable)
                updated = True
            elif executable != current_command:
                exec_info['command'] = executable
                updated = True

            # Handle Nebius kubeconfigs: change --profile to 'sky'
            if executable == 'nebius':
                args = exec_info.get('args', [])
                if args and '--profile' in args:
                    try:
                        profile_index = args.index('--profile')
                        if profile_index + 1 < len(args):
                            old_profile = args[profile_index + 1]
                            if old_profile != 'sky':
                                args[profile_index + 1] = 'sky'
                                updated = True
                    except ValueError:
                        pass

    os.makedirs(os.path.dirname(os.path.expanduser(output_path)), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as file:
        yaml.safe_dump(config, file)

    return updated


def format_kubeconfig_exec_auth_with_cache(kubeconfig_path: str) -> str:
    """Reformat the kubeconfig file or retrieve it from cache if it has already
    been formatted before. Store it in the cache directory if necessary.

    Having a cache for this is good if users spawn an extreme number of jobs
    concurrently.

    Args:
        kubeconfig_path (str): kubeconfig path
    Returns: updated kubeconfig path
    """
    # TODO(kyuds): GC cache files
    with open(kubeconfig_path, 'r', encoding='utf-8') as file:
        config = yaml.safe_load(file)
    normalized = yaml.dump(config, sort_keys=True)
    hashed = hashlib.sha1(normalized.encode('utf-8')).hexdigest()
    path = os.path.expanduser(
        f'{kubernetes_constants.SKY_K8S_EXEC_AUTH_KUBECONFIG_CACHE}/{hashed}.yaml'
    )

    # If we have already converted the same kubeconfig before, just return.
    if os.path.isfile(path):
        return path

    try:
        format_kubeconfig_exec_auth(config, path)
        return path
    except Exception as e:  # pylint: disable=broad-except
        # There may be problems with kubeconfig, but the user is not actually
        # using Kubernetes (or SSH Node Pools)
        logger.warning(
            f'Failed to format kubeconfig at {kubeconfig_path}. '
            'Please check if the kubeconfig is valid. This may cause '
            'problems when Kubernetes infra is used. '
            f'Reason: {common_utils.format_exception(e)}')
        return kubeconfig_path


def delete_k8s_resource_with_retry(delete_func: Callable, resource_type: str,
                                   resource_name: str) -> None:
    """Helper to delete Kubernetes resources with 404 handling and retries.

    Args:
        delete_func: Function to call to delete the resource
        resource_type: Type of resource being deleted (e.g. 'service'),
            used in logging
        resource_name: Name of the resource being deleted, used in logging
    """
    max_retries = 3
    retry_delay = 5  # seconds

    for attempt in range(max_retries):
        try:
            delete_func()
            return
        except kubernetes.api_exception() as e:
            if e.status == 404:
                logger.warning(
                    f'terminate_instances: Tried to delete {resource_type} '
                    f'{resource_name}, but the {resource_type} was not '
                    'found (404).')
                return
            elif attempt < max_retries - 1:
                logger.warning(f'terminate_instances: Failed to delete '
                               f'{resource_type} {resource_name} (attempt '
                               f'{attempt + 1}/{max_retries}). Error: {e}. '
                               f'Retrying in {retry_delay} seconds...')
                time.sleep(retry_delay)
            else:
                raise
