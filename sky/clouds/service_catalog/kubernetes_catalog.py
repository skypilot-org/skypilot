"""Kubernetes Catalog.

Kubernetes does not require a catalog of instances, but we need an image catalog
mapping SkyPilot image tags to corresponding container image tags.
"""
import re
import typing
from typing import Dict, List, Optional, Set, Tuple

from sky import check as sky_check
from sky.adaptors import common as adaptors_common
from sky.clouds import Kubernetes
from sky.clouds.service_catalog import CloudFilter
from sky.clouds.service_catalog import common
from sky.provision.kubernetes import utils as kubernetes_utils

if typing.TYPE_CHECKING:
    import pandas as pd
else:
    pd = adaptors_common.LazyImport('pandas')

_PULL_FREQUENCY_HOURS = 7

# We keep pull_frequency_hours so we can remotely update the default image paths
_image_df = common.read_catalog('kubernetes/images.csv',
                                pull_frequency_hours=_PULL_FREQUENCY_HOURS)

# TODO(romilb): Refactor implementation of common service catalog functions from
#   clouds/kubernetes.py to kubernetes_catalog.py


def get_image_id_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    """Returns the image id from the tag."""
    return common.get_image_id_from_tag_impl(_image_df, tag, region)


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
    return list_accelerators_realtime(gpus_only, name_filter, region_filter,
                                      quantity_filter, case_sensitive,
                                      all_regions, require_price)[0]


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
    # TODO(romilb): This should be refactored to use get_kubernetes_node_info()
    #   function from kubernetes_utils.
    del all_regions, require_price  # Unused.
    k8s_cloud = Kubernetes()
    if not any(
            map(k8s_cloud.is_same_cloud,
                sky_check.get_cached_enabled_clouds_or_refresh())
    ) or not kubernetes_utils.check_credentials()[0]:
        return {}, {}, {}

    has_gpu = kubernetes_utils.detect_gpu_resource()
    if not has_gpu:
        return {}, {}, {}

    label_formatter, _ = kubernetes_utils.detect_gpu_label_formatter()
    if not label_formatter:
        return {}, {}, {}

    accelerators_qtys: Set[Tuple[str, int]] = set()
    key = label_formatter.get_label_key()
    nodes = kubernetes_utils.get_kubernetes_nodes()
    # Get the pods to get the real-time GPU usage
    pods = kubernetes_utils.get_kubernetes_pods()
    # Total number of GPUs in the cluster
    total_accelerators_capacity: Dict[str, int] = {}
    # Total number of GPUs currently available in the cluster
    total_accelerators_available: Dict[str, int] = {}
    min_quantity_filter = quantity_filter if quantity_filter else 1

    for node in nodes:
        if key in node.metadata.labels:
            allocated_qty = 0
            accelerator_name = label_formatter.get_accelerator_from_label_value(
                node.metadata.labels.get(key))

            # Check if name_filter regex matches the accelerator_name
            regex_flags = 0 if case_sensitive else re.IGNORECASE
            if name_filter and not re.match(
                    name_filter, accelerator_name, flags=regex_flags):
                continue

            accelerator_count = int(
                node.status.allocatable.get('nvidia.com/gpu', 0))

            # Generate the GPU quantities for the accelerators
            if accelerator_name and accelerator_count > 0:
                for count in range(1, accelerator_count + 1):
                    accelerators_qtys.add((accelerator_name, count))

            for pod in pods:
                # Get all the pods running on the node
                if (pod.spec.node_name == node.metadata.name and
                        pod.status.phase in ['Running', 'Pending']):
                    # Iterate over all the containers in the pod and sum the
                    # GPU requests
                    for container in pod.spec.containers:
                        if container.resources.requests:
                            allocated_qty += int(
                                container.resources.requests.get(
                                    'nvidia.com/gpu', 0))

            accelerators_available = accelerator_count - allocated_qty

            if accelerator_count >= min_quantity_filter:
                quantized_count = (min_quantity_filter *
                                   (accelerator_count // min_quantity_filter))
                if accelerator_name not in total_accelerators_capacity:
                    total_accelerators_capacity[
                        accelerator_name] = quantized_count
                else:
                    total_accelerators_capacity[
                        accelerator_name] += quantized_count

            if accelerator_name not in total_accelerators_available:
                total_accelerators_available[accelerator_name] = 0
            if accelerators_available >= min_quantity_filter:
                quantized_availability = min_quantity_filter * (
                    accelerators_available // min_quantity_filter)
                total_accelerators_available[
                    accelerator_name] += quantized_availability

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
                                    region='kubernetes'))

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
