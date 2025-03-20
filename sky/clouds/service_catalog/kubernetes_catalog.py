"""Kubernetes Catalog.

Kubernetes does not require a catalog of instances, but we need an image catalog
mapping SkyPilot image tags to corresponding container image tags.
"""
import re
import typing
from typing import Dict, List, Optional, Set, Tuple

from sky import check as sky_check
from sky import clouds as sky_clouds
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.adaptors import kubernetes
from sky.clouds import cloud
from sky.clouds.service_catalog import CloudFilter
from sky.clouds.service_catalog import common
from sky.provision.kubernetes import utils as kubernetes_utils

logger = sky_logging.init_logger(__name__)

if typing.TYPE_CHECKING:
    import pandas as pd
else:
    pd = adaptors_common.LazyImport('pandas')

logger = sky_logging.init_logger(__name__)

_PULL_FREQUENCY_HOURS = 7

# We keep pull_frequency_hours so we can remotely update the default image paths
_image_df = common.read_catalog('kubernetes/images.csv',
                                pull_frequency_hours=_PULL_FREQUENCY_HOURS)

# TODO(romilb): Refactor implementation of common service catalog functions from
#   clouds/kubernetes.py to kubernetes_catalog.py


def get_image_id_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    """Returns the image id from the tag."""
    global _image_df
    image_id = common.get_image_id_from_tag_impl(_image_df, tag, region)
    if image_id is None:
        # Refresh the image catalog and try again, if the image tag is not
        # found.
        logger.debug('Refreshing the image catalog and trying again.')
        _image_df = common.read_catalog('kubernetes/images.csv',
                                        pull_frequency_hours=0)
        image_id = common.get_image_id_from_tag_impl(_image_df, tag, region)
    return image_id


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid."""
    return common.is_image_tag_valid_impl(_image_df, tag, region)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    # TODO(romilb): We should consider putting a lru_cache() with TTL to
    #   avoid multiple calls to kubernetes API in a short period of time (e.g.,
    #   from the optimizer).
    return _list_accelerators(gpus_only,
                              name_filter,
                              region_filter,
                              quantity_filter,
                              case_sensitive,
                              all_regions,
                              require_price,
                              realtime=False)[0]


def list_accelerators_realtime(
    gpus_only: bool,
    name_filter: Optional[str],
    region_filter: Optional[str],
    quantity_filter: Optional[int],
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = True
) -> Tuple[Dict[str, List[common.InstanceTypeInfo]], Dict[str, int], Dict[str,
                                                                          int]]:
    return _list_accelerators(gpus_only,
                              name_filter,
                              region_filter,
                              quantity_filter,
                              case_sensitive,
                              all_regions,
                              require_price,
                              realtime=True)


def _list_accelerators(
    gpus_only: bool,
    name_filter: Optional[str],
    region_filter: Optional[str],
    quantity_filter: Optional[int],
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = True,
    realtime: bool = False
) -> Tuple[Dict[str, List[common.InstanceTypeInfo]], Dict[str, int], Dict[str,
                                                                          int]]:
    """List accelerators in the Kubernetes cluster.

    If realtime is True, the function will query the cluster to fetch real-time
    GPU usage, which is returned in total_accelerators_available. Note that
    this may require an expensive list_pod_for_all_namespaces call, which
    requires cluster-wide pod read permissions.

    If the user does not have sufficient permissions to list pods in all
    namespaces, the function will return free GPUs as -1.

    Returns:
        A tuple of three dictionaries:
        - qtys_map: Dict mapping accelerator names to lists of InstanceTypeInfo
            objects with quantity information.
        - total_accelerators_capacity: Dict mapping accelerator names to their
            total capacity in the cluster.
        - total_accelerators_available: Dict mapping accelerator names to their
            current availability. Returns -1 for each accelerator if
            realtime=False or if insufficient permissions.
    """
    # TODO(romilb): This should be refactored to use get_kubernetes_node_info()
    #   function from kubernetes_utils.
    del all_regions, require_price  # Unused.

    # First check if Kubernetes is enabled. This ensures k8s python client is
    # installed. Do not put any k8s-specific logic before this check.
    enabled_clouds = sky_check.get_cached_enabled_clouds_or_refresh(
        cloud.CloudCapability.COMPUTE)
    if not sky_clouds.cloud_in_iterable(sky_clouds.Kubernetes(),
                                        enabled_clouds):
        return {}, {}, {}

    # TODO(zhwu): this should return all accelerators in multiple kubernetes
    # clusters defined by allowed_contexts.
    if region_filter is None:
        context = kubernetes_utils.get_current_kube_config_context_name()
        if context is None and kubernetes_utils.is_incluster_config_available():
            # If context is None and we are running in a kubernetes pod, use the
            # in-cluster context as the current context.
            context = kubernetes.in_cluster_context_name()
    else:
        context = region_filter
    if context is None:
        return {}, {}, {}

    # Verify that the credentials are still valid.
    if not kubernetes_utils.check_credentials(context)[0]:
        return {}, {}, {}

    has_gpu = kubernetes_utils.detect_accelerator_resource(context)
    if not has_gpu:
        return {}, {}, {}

    lf, _ = kubernetes_utils.detect_gpu_label_formatter(context)
    if not lf:
        return {}, {}, {}

    accelerators_qtys: Set[Tuple[str, int]] = set()
    keys = lf.get_label_keys()
    nodes = kubernetes_utils.get_kubernetes_nodes(context=context)
    pods = None
    if realtime:
        # Get the pods to get the real-time GPU usage
        try:
            pods = kubernetes_utils.get_all_pods_in_kubernetes_cluster(
                context=context)
        except kubernetes.api_exception() as e:
            if e.status == 403:
                logger.warning(
                    'Failed to get pods in the Kubernetes cluster '
                    '(forbidden). Please check if your account has '
                    'necessary permissions to list pods. Realtime GPU '
                    'availability information may be incorrect.')
            else:
                raise
    # Total number of GPUs in the cluster
    total_accelerators_capacity: Dict[str, int] = {}
    # Total number of GPUs currently available in the cluster
    total_accelerators_available: Dict[str, int] = {}
    min_quantity_filter = quantity_filter if quantity_filter else 1

    for node in nodes:
        for key in keys:
            if key in node.metadata.labels:
                allocated_qty = 0
                accelerator_name = lf.get_accelerator_from_label_value(
                    node.metadata.labels.get(key))

                # Exclude multi-host TPUs from being processed.
                # TODO(Doyoung): Remove the logic when adding support for
                # multi-host TPUs.
                if kubernetes_utils.is_multi_host_tpu(node.metadata.labels):
                    continue

                # Check if name_filter regex matches the accelerator_name
                regex_flags = 0 if case_sensitive else re.IGNORECASE
                if name_filter and not re.match(
                        name_filter, accelerator_name, flags=regex_flags):
                    continue

                # Generate the accelerator quantities
                accelerator_count = (
                    kubernetes_utils.get_node_accelerator_count(
                        node.status.allocatable))

                if accelerator_name and accelerator_count > 0:
                    # TPUs are counted in a different way compared to GPUs.
                    # Multi-node GPUs can be split into smaller units and be
                    # provisioned, but TPUs are considered as an atomic unit.
                    if kubernetes_utils.is_tpu_on_gke(accelerator_name):
                        accelerators_qtys.add(
                            (accelerator_name, accelerator_count))
                    else:
                        count = 1
                        while count <= accelerator_count:
                            accelerators_qtys.add((accelerator_name, count))
                            count *= 2
                        # Add the accelerator count if it's not already in the
                        # set (e.g., if there's 12 GPUs, we should have qtys 1,
                        # 2, 4, 8, 12)
                        if accelerator_count not in accelerators_qtys:
                            accelerators_qtys.add(
                                (accelerator_name, accelerator_count))

                if accelerator_count >= min_quantity_filter:
                    quantized_count = (
                        min_quantity_filter *
                        (accelerator_count // min_quantity_filter))
                    if accelerator_name not in total_accelerators_capacity:
                        total_accelerators_capacity[
                            accelerator_name] = quantized_count
                    else:
                        total_accelerators_capacity[
                            accelerator_name] += quantized_count

                if pods is None:
                    # If we can't get the pods, we can't get the GPU usage
                    total_accelerators_available[accelerator_name] = -1
                    continue

                for pod in pods:
                    # Get all the pods running on the node
                    if (pod.spec.node_name == node.metadata.name and
                            pod.status.phase in ['Running', 'Pending']):
                        # Iterate over all the containers in the pod and sum
                        # the GPU requests
                        for container in pod.spec.containers:
                            if container.resources.requests:
                                allocated_qty += (
                                    kubernetes_utils.get_node_accelerator_count(
                                        container.resources.requests))

                accelerators_available = accelerator_count - allocated_qty

                # Initialize the entry if it doesn't exist yet
                if accelerator_name not in total_accelerators_available:
                    total_accelerators_available[accelerator_name] = 0

                if accelerators_available >= min_quantity_filter:
                    quantized_availability = min_quantity_filter * (
                        accelerators_available // min_quantity_filter)
                    total_accelerators_available[accelerator_name] = (
                        total_accelerators_available.get(accelerator_name, 0) +
                        quantized_availability)

    result = []

    # Generate dataframe for common.list_accelerators_impl
    for accelerator_name, accelerator_count in accelerators_qtys:
        result.append(
            common.InstanceTypeInfo(cloud='Kubernetes',
                                    instance_type=None,
                                    accelerator_name=accelerator_name,
                                    accelerator_count=accelerator_count,
                                    cpu_count=None,
                                    device_memory=None,
                                    memory=None,
                                    price=0.0,
                                    spot_price=0.0,
                                    region=context))

    df = pd.DataFrame(result,
                      columns=[
                          'Cloud', 'InstanceType', 'AcceleratorName',
                          'AcceleratorCount', 'vCPUs', 'DeviceMemoryGiB',
                          'MemoryGiB', 'Price', 'SpotPrice', 'Region'
                      ])
    df['GpuInfo'] = True

    # Use common.list_accelerators_impl to get InstanceTypeInfo objects used
    # by sky show-gpus when cloud is not specified.
    qtys_map = common.list_accelerators_impl('Kubernetes', df, gpus_only,
                                             name_filter, region_filter,
                                             quantity_filter, case_sensitive)
    return qtys_map, total_accelerators_capacity, total_accelerators_available


def validate_region_zone(
    region_name: Optional[str],
    zone_name: Optional[str],
    clouds: CloudFilter = None  # pylint: disable=unused-argument
) -> Tuple[Optional[str], Optional[str]]:
    return (region_name, zone_name)
