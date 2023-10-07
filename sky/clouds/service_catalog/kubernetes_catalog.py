"""Kubernetes Catalog.

Kubernetes does not require a catalog of instances, but we need an image catalog
mapping SkyPilot image tags to corresponding container image tags.
"""
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from sky import global_user_state
from sky.clouds import Kubernetes
from sky.clouds.service_catalog import CloudFilter
from sky.clouds.service_catalog import common
from sky.utils import kubernetes_utils

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
        case_sensitive: bool = True
) -> Dict[str, List[common.InstanceTypeInfo]]:
    k8s_cloud = Kubernetes()
    if not any(
            map(k8s_cloud.is_same_cloud, global_user_state.get_enabled_clouds())
    ) or not kubernetes_utils.check_credentials()[0]:
        return {}

    has_gpu = kubernetes_utils.detect_gpu_resource()
    if not has_gpu:
        return {}

    label_formatter, _ = kubernetes_utils.detect_gpu_label_formatter()
    if not label_formatter:
        return {}

    accelerators: Set[Tuple[str, int]] = set()
    key = label_formatter.get_label_key()
    nodes = kubernetes_utils.get_kubernetes_nodes()
    for node in nodes:
        if key in node.metadata.labels:
            accelerator_name = label_formatter.get_accelerator_from_label_value(
                node.metadata.labels.get(key))
            accelerator_count = int(
                node.status.allocatable.get('nvidia.com/gpu', 0))

            if accelerator_name and accelerator_count > 0:
                for count in range(1, accelerator_count + 1):
                    accelerators.add((accelerator_name, count))

    result = []
    for accelerator_name, accelerator_count in accelerators:
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

    return common.list_accelerators_impl('Kubernetes', df, gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive)


def validate_region_zone(
    region_name: Optional[str],
    zone_name: Optional[str],
    clouds: CloudFilter = None  # pylint: disable=unused-argument
) -> Tuple[Optional[str], Optional[str]]:
    return (region_name, zone_name)
