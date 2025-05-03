"""Utility functions for Slurm provisioning."""
import collections
from typing import Any, Dict, List, Optional, Tuple, Set

from sky import exceptions
from sky import models
from sky import sky_logging
from sky.clouds.service_catalog import common
from sky.clouds.service_catalog import slurm_catalog
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)


def slurm_gpu_availability(
        name_filter: Optional[str] = None,
        quantity_filter: Optional[int] = None,
        env_vars: Optional[Dict[str, str]] = None,
        **kwargs) -> List[Tuple[str, List[models.RealtimeGpuAvailability]]]:
    """Gets Slurm real-time GPU availability grouped by partition.

    This function calls the Slurm backend to fetch GPU info.

    Args:
        name_filter: Optional name filter for GPUs.
        quantity_filter: Optional quantity filter for GPUs.
        env_vars: Environment variables (may be needed for backend).
        kwargs: Additional keyword arguments.

    Returns:
        A list of tuples, where each tuple contains:
        - partition_name (str): The name of the Slurm partition.
        - availability_list (List[models.RealtimeGpuAvailability]): A list
            of RealtimeGpuAvailability objects for that partition.
        Example structure:
        [
            ('gpu_partition_1', [
                RealtimeGpuAvailability(gpu='V100', counts=[4, 8],
                                        capacity=16, available=10),
                RealtimeGpuAvailability(gpu='A100', counts=[8],
                                        capacity=8, available=0),
            ]),
            ('gpu_partition_2', [
                RealtimeGpuAvailability(gpu='V100', counts=[4],
                                        capacity=4, available=4),
            ])
        ]

    Raises:
        ValueError: If Slurm is not configured or no matching GPUs are found.
        exceptions.NotSupportedError: If Slurm is not enabled or configured.
    """
    del env_vars, kwargs  # Currently unused

    try:
        qtys_map, total_capacity, total_available = (
            slurm_catalog.list_accelerators_realtime(
                gpus_only=True,
                name_filter=name_filter,
                region_filter=None,  # Handled internally by grouping
                quantity_filter=quantity_filter,
                case_sensitive=False))
    except exceptions.NotSupportedError as e:
        logger.error(f'Failed to query Slurm GPU availability: {e}')
        raise
    except Exception as e:
        msg = (f'Error querying Slurm GPU availability: '
               f'{common_utils.format_exception(e, use_bracket=True)}')
        logger.error(msg)
        raise ValueError(msg) from e

    if not qtys_map:
        err_msg = 'No GPUs found in the Slurm cluster.'
        if name_filter is not None or quantity_filter is not None:
            filters = []
            if name_filter:
                filters.append(f'name={name_filter!r}')
            if quantity_filter:
                filters.append(f'quantity>={quantity_filter}')
            err_msg = (f'Resource matching filters ({", ".join(filters)}) '
                       f'not found in the Slurm cluster.')
        raise ValueError(err_msg)

    result_list: List[Tuple[str, List[models.RealtimeGpuAvailability]]] = []

    # Get all node info once to avoid repeated calls in the loop
    try:
        all_nodes_info = slurm_catalog.get_slurm_node_info_list()
    except (RuntimeError, exceptions.NotSupportedError) as e:
        logger.warning(f'Could not retrieve any Slurm node info: {e}')
        all_nodes_info = []

    # Group nodes by partition
    nodes_by_partition: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for node_info in all_nodes_info:
        partition = node_info.get('partition', 'unknown_partition')
        nodes_by_partition[partition].append(node_info)

    for partition, nodes_in_partition in sorted(nodes_by_partition.items()):
        availability_list: List[models.RealtimeGpuAvailability] = []
        
        # Calculate partition-specific totals and observed counts
        partition_total_capacity: Dict[str, int] = collections.defaultdict(int)
        partition_total_available: Dict[str, int] = collections.defaultdict(int)
        partition_gpu_counts: Dict[str, Set[int]] = collections.defaultdict(set)
        max_free_gpus_per_node_type: Dict[str, int] = collections.defaultdict(int)

        for node_info in nodes_in_partition:
            gpu_type = node_info.get('gpu_type')
            if not gpu_type: continue # Skip nodes without a GPU type reported
            
            total_gpus = node_info.get('total_gpus', 0)
            free_gpus = node_info.get('free_gpus', 0)

            partition_total_capacity[gpu_type] += total_gpus
            partition_total_available[gpu_type] += free_gpus
            if total_gpus > 0:
                 partition_gpu_counts[gpu_type].add(total_gpus)
            # Track max free GPUs on a single node for this type
            max_free_gpus_per_node_type[gpu_type] = max(max_free_gpus_per_node_type[gpu_type], free_gpus)

        # Create RealtimeGpuAvailability objects for each GPU type in this partition
        for gpu_type in sorted(partition_gpu_counts.keys()):
            # Generate requestable quantities based on max *free* GPUs on a single node
            max_requestable_on_single_node = max_free_gpus_per_node_type.get(gpu_type, 0)
            requestable_quantities = list(range(1, max_requestable_on_single_node + 1))
            
            capacity = partition_total_capacity.get(gpu_type, 0)
            available = partition_total_available.get(gpu_type, 0)

            availability_list.append(
                models.RealtimeGpuAvailability(
                    gpu_type,
                    requestable_quantities, # Use calculated requestable quantities
                    capacity, # Partition-specific total
                    available, # Partition-specific free
                ))
                
        if availability_list:
             result_list.append((partition, availability_list))

    if not result_list:
        raise ValueError('No GPUs found after processing Slurm cluster data.')

    return result_list


def slurm_node_info() -> List[Dict[str, Any]]:
    """Gets detailed information for each node in the Slurm cluster.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, each containing node info.
    """
    try:
        node_list = slurm_catalog.get_slurm_node_info_list()
    except (RuntimeError, exceptions.NotSupportedError) as e:
        logger.warning(f'Could not retrieve Slurm node info: {e}')
        return []
    return node_list
