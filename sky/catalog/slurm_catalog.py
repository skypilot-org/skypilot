"""Slurm Catalog."""

import collections
import re
from typing import Dict, List, Optional, Set, Tuple

from sky import check as sky_check
from sky import clouds as sky_clouds
from sky import sky_logging
from sky.catalog import common
from sky.clouds import cloud
from sky.provision.slurm import utils as slurm_utils
from sky.utils import resources_utils

logger = sky_logging.init_logger(__name__)

_DEFAULT_NUM_VCPUS = 2
_DEFAULT_MEMORY_CPU_RATIO = 1


def instance_type_exists(instance_type: str) -> bool:
    """Check if the given instance type is valid for Slurm."""
    return slurm_utils.SlurmInstanceType.is_valid_instance_type(instance_type)


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[
                                  resources_utils.DiskTier] = None,
                              region: Optional[str] = None,
                              zone: Optional[str] = None) -> Optional[str]:
    # Delete unused parameters.
    del disk_tier, region, zone

    # Slurm provisions resources via --cpus-per-task and --mem.
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
    virtual_instance_type = slurm_utils.SlurmInstanceType(
        instance_cpus, instance_mem).name
    return virtual_instance_type


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """List accelerators in Slurm clusters.

    Returns a dictionary mapping GPU type to a list of InstanceTypeInfo objects.
    """
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
    """Fetches real-time accelerator information from the Slurm cluster.

    Uses the `get_slurm_node_info_list` helper function.

    Args:
        gpus_only: If True, only return GPU accelerators.
        name_filter: Regex filter for accelerator names (e.g., 'V100', 'gpu').
        region_filter: Optional filter for Slurm partitions.
        quantity_filter: Minimum number of accelerators required per node.
        case_sensitive: Whether name_filter is case-sensitive.
        all_regions: Unused in Slurm context.
        require_price: Unused in Slurm context.

    Returns:
        A tuple of three dictionaries:
        - qtys_map: Maps GPU type to set of InstanceTypeInfo objects for unique
          counts found per node.
        - total_capacity: Maps GPU type to total count across all nodes.
        - total_available: Maps GPU type to total free count across all nodes.
    """
    del gpus_only, all_regions, require_price

    enabled_clouds = sky_check.get_cached_enabled_clouds_or_refresh(
        cloud.CloudCapability.COMPUTE)
    if not sky_clouds.cloud_in_iterable(sky_clouds.Slurm(), enabled_clouds):
        return {}, {}, {}

    if region_filter is None:
        # Get the first available cluster as default
        all_clusters = slurm_utils.get_all_slurm_cluster_names()
        if not all_clusters:
            return {}, {}, {}
        slurm_cluster = all_clusters[0]
    else:
        slurm_cluster = region_filter

    slurm_nodes_info = slurm_utils.slurm_node_info(
        slurm_cluster_name=slurm_cluster)

    if not slurm_nodes_info:
        # Customize error message based on filters
        err_msg = 'No matching GPU nodes found in the Slurm cluster'
        filters_applied = []
        if name_filter:
            filters_applied.append(f'gpu_name={name_filter!r}')
        if quantity_filter:
            filters_applied.append(f'quantity>={quantity_filter}')
        if filters_applied:
            err_msg += f' with filters ({", ".join(filters_applied)})'
        err_msg += '.'
        logger.error(
            err_msg)  # Log as error as it indicates no usable resources found
        raise ValueError(err_msg)

    # Aggregate results into the required format
    qtys_map: Dict[str,
                   Set[common.InstanceTypeInfo]] = collections.defaultdict(set)
    total_capacity: Dict[str, int] = collections.defaultdict(int)
    total_available: Dict[str, int] = collections.defaultdict(int)

    for node_info in slurm_nodes_info:
        gpu_type = node_info['gpu_type']
        node_total_gpus = node_info['total_gpus']
        node_free_gpus = node_info['free_gpus']
        partition = node_info['partition']

        # Apply name filter to the determined GPU type
        regex_flags = 0 if case_sensitive else re.IGNORECASE
        if name_filter and not re.match(
                name_filter, gpu_type, flags=regex_flags):
            continue

        # Apply quantity filter (total GPUs on node must meet this)
        if quantity_filter and node_total_gpus < quantity_filter:
            continue

        # Apply partition filter if specified
        # TODO(zhwu): when a node is in multiple partitions, the partition
        # mapping from node to partition does not work.
        # if partition_filter and partition != partition_filter:
        #     continue

        # Create InstanceTypeInfo objects for various GPU counts
        # Similar to Kubernetes, generate powers of 2 up to node_total_gpus
        if node_total_gpus > 0:
            count = 1
            while count <= node_total_gpus:
                instance_info = common.InstanceTypeInfo(
                    instance_type=None,  # Slurm doesn't have instance types
                    accelerator_name=gpu_type,
                    accelerator_count=count,
                    cpu_count=node_info['vcpu_count'],
                    memory=node_info['memory_gb'],
                    price=0.0,  # Slurm doesn't have price info
                    region=partition,  # Use partition as region
                    cloud='slurm',  # Specify cloud as 'slurm'
                    device_memory=0.0,  # No GPU memory info from Slurm
                    spot_price=0.0,  # Slurm doesn't have spot pricing
                )
                qtys_map[gpu_type].add(instance_info)
                count *= 2

            # Add the actual total if it's not already included
            # (e.g., if node has 12 GPUs, include counts 1, 2, 4, 8, 12)
            if count // 2 != node_total_gpus:
                instance_info = common.InstanceTypeInfo(
                    instance_type=None,
                    accelerator_name=gpu_type,
                    accelerator_count=node_total_gpus,
                    cpu_count=node_info['vcpu_count'],
                    memory=node_info['memory_gb'],
                    price=0.0,
                    region=partition,
                    cloud='slurm',
                    device_memory=0.0,
                    spot_price=0.0,
                )
                qtys_map[gpu_type].add(instance_info)

        # Map of GPU type -> total count across all matched nodes
        total_capacity[gpu_type] += node_total_gpus

        # Map of GPU type -> total *free* count across all matched nodes
        total_available[gpu_type] += node_free_gpus

    # Check if any GPUs were found after applying filters
    if not total_capacity:
        err_msg = 'No matching GPU nodes found in the Slurm cluster'
        filters_applied = []
        if name_filter:
            filters_applied.append(f'gpu_name={name_filter!r}')
        if quantity_filter:
            filters_applied.append(f'quantity>={quantity_filter}')
        if filters_applied:
            err_msg += f' with filters ({", ".join(filters_applied)})'
        err_msg += '.'
        logger.error(err_msg)
        raise ValueError(err_msg)

    # Convert sets of InstanceTypeInfo to sorted lists
    final_qtys_map = {
        gpu: sorted(list(instances), key=lambda x: x.accelerator_count)
        for gpu, instances in qtys_map.items()
    }

    logger.debug(f'Aggregated Slurm GPU Info: '
                 f'qtys={final_qtys_map}, '
                 f'capacity={dict(total_capacity)}, '
                 f'available={dict(total_available)}')

    return final_qtys_map, dict(total_capacity), dict(total_available)


def validate_region_zone(
    region_name: Optional[str],
    zone_name: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    return (region_name, zone_name)
