"""PBS Catalog.

PBS has no instance catalog; the virtual instance types are built from the
requested resources and prices come from ``pbs.pricing`` in the config.
"""

import collections
import re
from typing import Dict, List, Optional, Set, Tuple

from sky import check as sky_check
from sky import clouds as sky_clouds
from sky import sky_logging
from sky.catalog import common
from sky.clouds import cloud
from sky.provision.pbs import utils as pbs_utils
from sky.utils import resources_utils

logger = sky_logging.init_logger(__name__)

_DEFAULT_NUM_VCPUS = 2
_DEFAULT_MEMORY_CPU_RATIO = 1


def instance_type_exists(instance_type: str) -> bool:
    return pbs_utils.PbsInstanceType.is_valid_instance_type(instance_type)


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None) -> Optional[str]:
    del disk_tier, zone, local_disk, use_spot, max_hourly_cost, region
    instance_cpus = float(
        cpus.strip('+')) if cpus is not None else _DEFAULT_NUM_VCPUS
    if memory is not None:
        if memory.endswith('+'):
            instance_mem = float(memory[:-1])
        elif memory.endswith('x'):
            instance_mem = float(memory[:-1]) * instance_cpus
        else:
            instance_mem = float(memory)
    else:
        instance_mem = instance_cpus * _DEFAULT_MEMORY_CPU_RATIO
    return pbs_utils.PbsInstanceType(instance_cpus, instance_mem).name


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """GPUs of the allowed PBS clusters; empty when PBS is not enabled."""
    return list_accelerators_realtime(gpus_only, name_filter, region_filter,
                                      quantity_filter, case_sensitive,
                                      all_regions, require_price)[0]


def list_accelerators_realtime(
    gpus_only: bool = True,
    name_filter: Optional[str] = None,
    region_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = False,
) -> Tuple[Dict[str, List[common.InstanceTypeInfo]], Dict[str, int], Dict[str,
                                                                          int]]:
    """Real-time GPU inventory of the PBS clusters.

    Returns (qtys_map, total_capacity, total_available) keyed by GPU type.
    Unlike a cloud catalog this contacts the clusters' login nodes; it never
    raises when nothing is found, so that a generic ``sky show-gpus`` keeps
    working for the other clouds.
    """
    del gpus_only, all_regions, require_price

    enabled_clouds = sky_check.get_cached_enabled_clouds_or_refresh(
        cloud.CloudCapability.COMPUTE)
    if not sky_clouds.cloud_in_iterable(sky_clouds.PBS(), enabled_clouds):
        return {}, {}, {}

    if region_filter is None:
        clusters_to_query = sky_clouds.PBS.existing_allowed_clusters(
            silent=True)
    else:
        clusters_to_query = [region_filter]
    if not clusters_to_query:
        return {}, {}, {}

    nodes_info = [
        info for cluster in clusters_to_query
        for info in pbs_utils.pbs_node_info(pbs_cluster_name=cluster)
    ]

    qtys_map: Dict[str,
                   Set[common.InstanceTypeInfo]] = collections.defaultdict(set)
    total_capacity: Dict[str, int] = collections.defaultdict(int)
    total_available: Dict[str, int] = collections.defaultdict(int)
    regex_flags = 0 if case_sensitive else re.IGNORECASE
    for node_info in nodes_info:
        gpu_type = node_info['gpu_type']
        node_total_gpus = node_info['total_gpus']
        if gpu_type is None or node_total_gpus <= 0:
            continue
        if name_filter and not re.match(
                name_filter, gpu_type, flags=regex_flags):
            continue
        if quantity_filter and node_total_gpus < quantity_filter:
            continue
        queue = node_info['queue'] or None
        pricing = _get_pricing(region=node_info['pbs_cluster_name'],
                               zone=queue)
        per_accel = common.get_hourly_cost_from_pricing(
            pricing,
            cpus=0,
            memory=0,
            accelerator_name=gpu_type,
            accelerator_count=1,
        )
        counts = []
        count = 1
        while count <= node_total_gpus:
            counts.append(count)
            count *= 2
        if counts[-1] != node_total_gpus:
            counts.append(node_total_gpus)
        for cnt in counts:
            qtys_map[gpu_type].add(
                common.InstanceTypeInfo(
                    instance_type=None,
                    accelerator_name=gpu_type,
                    accelerator_count=cnt,
                    cpu_count=node_info['vcpu_count'],
                    memory=node_info['memory_gb'],
                    price=per_accel * cnt,
                    region=node_info['pbs_cluster_name'],
                    cloud='pbs',
                    device_memory=0.0,
                    spot_price=per_accel * cnt,
                ))
        total_capacity[gpu_type] += node_total_gpus
        total_available[gpu_type] += node_info['free_gpus']

    final_qtys_map = {
        gpu: sorted(instances, key=lambda x: x.accelerator_count)
        for gpu, instances in qtys_map.items()
    }
    return final_qtys_map, dict(total_capacity), dict(total_available)


def _get_pricing(region: Optional[str], zone: Optional[str] = None) -> Dict:
    """Pricing for a cluster/queue: cloud < cluster < queue, deep-merged."""
    paths: List[Tuple[str, ...]] = [('pbs', 'pricing')]
    if region is not None:
        paths.append(('pbs', 'cluster_configs', region, 'pricing'))
    if region is not None and zone is not None:
        paths.append(
            ('pbs', 'cluster_configs', region, 'queue_configs', zone, 'pricing'))
    return common.resolve_pricing_config(*paths)


def get_hourly_cost(instance_type: str,
                    use_spot: bool,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    del use_spot  # PBS has no spot pricing.
    instance = pbs_utils.PbsInstanceType.from_instance_type(instance_type)
    return common.get_hourly_cost_from_pricing(
        _get_pricing(region, zone),
        cpus=instance.cpus,
        memory=instance.memory,
        accelerator_name=instance.accelerator_type,
        accelerator_count=instance.accelerator_count,
    )


def validate_region_zone(
    region_name: Optional[str],
    zone_name: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    return (region_name, zone_name)
