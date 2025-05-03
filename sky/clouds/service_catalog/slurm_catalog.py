# sky/clouds/service_catalog/slurm_catalog.py
"""Slurm Cloud Service Catalog."""

import collections
import re
import subprocess
from typing import Any, Dict, List, Optional, Set, Tuple

from sky import sky_logging
from sky.clouds.service_catalog import common
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

# === Slurm-specific Catalog Implementation ===


# Helper function to parse scontrol output (can be moved to utils if needed)
def _parse_scontrol_node_output(output: str) -> Dict[str, str]:
    """Parses the key=value output of 'scontrol show node'."""
    node_info = {}
    # Split by space, handling values that might contain spaces if quoted
    # This is a simplified parser; scontrol output can be complex.
    parts = output.split()
    for part in parts:
        if '=' in part:
            key, value = part.split('=', 1)
            # Simple quote removal, might need refinement
            value = value.strip('\'"')
            node_info[key] = value
    return node_info


def get_slurm_node_info_list() -> List[Dict[str, Any]]:
    """Gathers detailed information about each node in the Slurm cluster."""
    # 1. Get node state and GRES using sinfo
    sinfo_cmd = ['sinfo', '-N', '-h', '-o', '%N %t %G']
    try:
        sinfo_proc = subprocess.run(sinfo_cmd,
                                    check=True,
                                    capture_output=True,
                                    text=True,
                                    timeout=20)
        sinfo_output = sinfo_proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired,
            subprocess.CalledProcessError) as e:
        logger.error(f'Failed to run `sinfo`: {e}')
        raise RuntimeError(f'Failed to run `sinfo`: {e}') from e

    if not sinfo_output:
        logger.warning('`sinfo -N` returned no output. No nodes found?')
        return []

    # 2. Get partition info
    node_to_partition = {}
    partition_cmd = ['sinfo', '-N', '-h', '-o', '%N %P']
    try:
        partition_proc = subprocess.run(partition_cmd,
                                        check=True,
                                        capture_output=True,
                                        text=True,
                                        timeout=20)
        partition_output = partition_proc.stdout.strip()
        for line in partition_output.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                node_to_partition[parts[0]] = parts[1]
    except Exception as e:  # pylint: disable=broad-except
        logger.warning('Failed to get partition info: '
                       f'{common_utils.format_exception(e, use_bracket=True)}')

    # 3. Process each node
    slurm_nodes_info = []
    unique_nodes_processed = set()
    gres_gpu_pattern = re.compile(r'((gpu)(?::([^:]+))?:(\d+))')

    for line in sinfo_output.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        node_name, state, gres_str = parts[0], parts[1], parts[2]

        if node_name in unique_nodes_processed:
            continue
        unique_nodes_processed.add(node_name)

        # Apply partition filter
        partition = node_to_partition.get(node_name, '')

        # Extract GPU info from GRES
        gres_match = gres_gpu_pattern.search(gres_str)

        total_gpus = 0
        gpu_type_from_sinfo = 'gpu'  # Default if no type in GRES
        if gres_match:
            try:
                total_gpus = int(gres_match.group(4))
                gpu_type_from_sinfo = gres_match.group(3) if gres_match.group(
                    3) else 'gpu'
            except ValueError:
                logger.warning(
                    f'Could not parse GPU count from GRES for {node_name}.')

        # Get allocated GPUs via squeue
        allocated_gpus = 0
        if state in ('alloc', 'mix', 'drain', 'drng', 'drained', 'resv',
                     'comp'):
            try:
                squeue_cmd = ['squeue', '-w', node_name, '-h', '-o', '%b']
                squeue_proc = subprocess.run(squeue_cmd,
                                             check=True,
                                             capture_output=True,
                                             text=True,
                                             timeout=20)
                squeue_output = squeue_proc.stdout.strip()
                if squeue_output:
                    job_gres_pattern = re.compile(r'gpu(?::[^:]+)*:(\d+)')
                    for job_line in squeue_output.splitlines():
                        gres_job_match = job_gres_pattern.search(job_line)
                        if gres_job_match:
                            allocated_gpus += int(gres_job_match.group(1))
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    f'Failed to get squeue allocation for {node_name}: {e}. '
                    'Falling back.')
                # Fallback based on state if squeue fails
                if state == 'alloc':
                    allocated_gpus = total_gpus
                elif state == 'mix':
                    allocated_gpus = total_gpus // 2
                else:
                    # Conservative for drain/resv/comp
                    allocated_gpus = total_gpus
        elif state == 'idle':
            allocated_gpus = 0

        free_gpus = total_gpus - allocated_gpus if state not in ('down',
                                                                 'drain',
                                                                 'drng',
                                                                 'maint') else 0
        free_gpus = max(0, free_gpus)

        # Get CPU/Mem info via scontrol
        vcpu_total = 0
        mem_gb = 0.0
        try:
            scontrol_cmd = ['scontrol', 'show', 'node', node_name]
            scontrol_proc = subprocess.run(scontrol_cmd,
                                           check=True,
                                           capture_output=True,
                                           text=True,
                                           timeout=20)
            node_details = _parse_scontrol_node_output(scontrol_proc.stdout)
            vcpu_total = int(node_details.get('CPUTot', '0'))
            mem_gb = float(node_details.get('RealMemory', '0')) / 1024.0
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'Failed to get CPU/memory info for {node_name}: {e}')

        slurm_nodes_info.append({
            'node_name': node_name,
            'partition': partition,
            'node_state': state,
            'gpu_type': gpu_type_from_sinfo,
            'total_gpus': total_gpus,
            'free_gpus': free_gpus,
            'vcpu_count': vcpu_total,
            'memory_gb': round(mem_gb, 2),
        })

    return slurm_nodes_info


# Renamed from _list_accelerator_realtime for clarity
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

    # Use region_filter as partition_filter for Slurm
    partition_filter = region_filter

    # Call the helper function to get node info
    slurm_nodes_info = get_slurm_node_info_list()

    if not slurm_nodes_info:
        # Customize error message based on filters
        err_msg = 'No matching GPU nodes found in the Slurm cluster'
        filters_applied = []
        if name_filter:
            filters_applied.append(f'gpu_name={name_filter!r}')
        if quantity_filter:
            filters_applied.append(f'quantity>={quantity_filter}')
        if partition_filter:
            filters_applied.append(f'partition={partition_filter!r}')
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

        # Create InstanceTypeInfo for this configuration
        if node_total_gpus > 0:  # Only add counts > 0
            instance_info = common.InstanceTypeInfo(
                instance_type=None,  # Slurm doesn't have instance types
                accelerator_name=gpu_type,
                accelerator_count=node_total_gpus,
                cpu_count=node_info['vcpu_count'],
                memory=node_info['memory_gb'],
                price=0.0,  # Slurm doesn't have price info
                region=partition,  # Use partition as region
                cloud='slurm',  # Specify cloud as 'slurm'
                device_memory=0.0,  # We don't have GPU memory info from Slurm
                spot_price=0.0,  # Slurm doesn't have spot pricing
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
        if partition_filter:
            filters_applied.append(f'partition={partition_filter!r}')
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
