"""Utility functions for Slurm provisioning."""
import collections
from typing import Any, Dict, List, Optional, Tuple

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

    partition_data: Dict[str, Dict[
        str, List[common.InstanceTypeInfo]]] = collections.defaultdict(
            lambda: collections.defaultdict(list))

    for gpu_type, instances in qtys_map.items():
        for instance in instances:
            partition = instance.region
            if partition is None:
                partition = 'unknown_partition'
            partition_data[partition][gpu_type].append(instance)

    result_list: List[Tuple[str, List[models.RealtimeGpuAvailability]]] = []

    for partition, gpu_type_map in sorted(partition_data.items()):
        availability_list: List[models.RealtimeGpuAvailability] = []
        for gpu_type, instances in sorted(gpu_type_map.items()):
            counts = sorted(
                list(set(inst.accelerator_count for inst in instances)))
            capacity = total_capacity.get(gpu_type, 0)
            available = total_available.get(gpu_type, 0)

            availability_list.append(
                models.RealtimeGpuAvailability(
                    gpu_type,
                    counts,
                    capacity,
                    available,
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
