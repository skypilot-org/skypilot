# sky/clouds/service_catalog/slurm_catalog.py
"""Slurm Cloud Service Catalog."""

import collections
import re
import subprocess
import typing
from typing import Any, Dict, List, Optional, Tuple, Set

from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.clouds import cloud
# Re-use common catalog utilities if applicable, otherwise create Slurm-specific ones
from sky.clouds.service_catalog import common
from sky.utils import common_utils
# Need a way to run Slurm commands, potentially via a Slurm backend/utils module
# from sky.provision.slurm import utils as slurm_utils (placeholder)

logger = sky_logging.init_logger(__name__)

if typing.TYPE_CHECKING:
    # Avoid cyclical import
    from sky.clouds import slurm as slurm_cloud


# === Slurm-specific Catalog Implementation ===

# Placeholder: Image functions might not be directly applicable to Slurm
# depending on how containerization/environments are handled.
def get_image_id_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    """Returns the image id from the tag. (Placeholder for Slurm)."""
    del tag, region  # Unused
    logger.warning('Slurm image catalog (get_image_id_from_tag) '
                   'is not implemented.')
    # If Slurm uses specific container images registered in SkyPilot,
    # implement logic similar to Kubernetes/Cloud catalogs here.
    return None


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid. (Placeholder for Slurm)."""
    del tag, region  # Unused
    logger.warning('Slurm image catalog (is_image_tag_valid) '
                   'is not implemented.')
    # Return True for now, assuming users manage their Slurm environments/images
    return True


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


def get_slurm_node_info_list(
    partition_filter: Optional[str] = None,
    name_filter: Optional[str] = None, # Filter by node name (regex)
    case_sensitive: bool = True,
    gpus_only: bool = True, # Only include nodes with GPUs by default
) -> List[Dict[str, Any]]:
    """Gathers detailed information about each node in the Slurm cluster."""
    logger.debug(f'Querying Slurm for node info (partition: {partition_filter}, name: {name_filter})')

    # 1. Get node state and GRES using sinfo
    sinfo_cmd = ['sinfo', '-N', '-h', '-o', '%N %t %G']
    try:
        sinfo_proc = subprocess.run(sinfo_cmd, check=True, capture_output=True, text=True, timeout=20)
        sinfo_output = sinfo_proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        logger.error(f'Failed to run `sinfo`: {e}')
        raise RuntimeError(f'Failed to run `sinfo`: {e}') from e

    if not sinfo_output:
        logger.warning('`sinfo -N` returned no output. No nodes found?')
        return []

    # 2. Get partition info
    node_to_partition = {}
    partition_cmd = ['sinfo', '-N', '-h', '-o', '%N %P']
    try:
        partition_proc = subprocess.run(partition_cmd, check=True, capture_output=True, text=True, timeout=20)
        partition_output = partition_proc.stdout.strip()
        for line in partition_output.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                node_to_partition[parts[0]] = parts[1]
    except Exception as e:
        logger.warning(f'Failed to get partition info: {e}')

    # 3. Process each node
    slurm_nodes_info = []
    unique_nodes_processed = set()
    gres_gpu_pattern = re.compile(r'((gpu)(?::([^:]+))?:(\d+))')

    for line in sinfo_output.splitlines():
        parts = line.split()
        if len(parts) < 3: continue
        node_name, state, gres_str = parts[0], parts[1], parts[2]

        if node_name in unique_nodes_processed: continue
        unique_nodes_processed.add(node_name)

        # Apply partition filter
        partition = node_to_partition.get(node_name, '')
        if partition_filter and partition != partition_filter:
            continue

        # Apply node name filter
        if name_filter and not re.search(name_filter, node_name):
            continue

        # Extract GPU info from GRES
        gres_match = gres_gpu_pattern.search(gres_str)
        if gpus_only and not gres_match:
            continue # Skip if GPUs are required but not found

        total_gpus = 0
        gpu_type_from_sinfo = 'gpu' # Default if no type in GRES
        if gres_match:
            try:
                total_gpus = int(gres_match.group(4))
                gpu_type_from_sinfo = gres_match.group(3) if gres_match.group(3) else 'gpu'
            except ValueError:
                logger.warning(f'Could not parse GPU count from GRES for {node_name}.')
                if gpus_only: continue # Skip if GPUs required but count is bad

        # Get allocated GPUs via squeue
        allocated_gpus = 0
        if state in ('alloc', 'mix', 'drain', 'drng', 'drained', 'resv', 'comp'):
            try:
                squeue_cmd = ['squeue', '-w', node_name, '-h', '-o', '%b']
                squeue_proc = subprocess.run(squeue_cmd, check=True, capture_output=True, text=True, timeout=20)
                squeue_output = squeue_proc.stdout.strip()
                if squeue_output:
                    job_gres_pattern = re.compile(r'gpu(?::[^:]+)*:(\d+)')
                    for job_line in squeue_output.splitlines():
                        gres_job_match = job_gres_pattern.search(job_line)
                        if gres_job_match:
                            allocated_gpus += int(gres_job_match.group(1))
            except Exception as e:
                logger.warning(f'Failed to get squeue allocation for {node_name}: {e}. Falling back.')
                # Fallback based on state if squeue fails
                if state == 'alloc': allocated_gpus = total_gpus
                elif state == 'mix': allocated_gpus = total_gpus // 2
                else: allocated_gpus = total_gpus # Conservative for drain/resv/comp
        elif state == 'idle':
            allocated_gpus = 0

        free_gpus = total_gpus - allocated_gpus if state not in ('down', 'drain', 'drng', 'maint') else 0
        free_gpus = max(0, free_gpus)

        # Get CPU/Mem info via scontrol
        vcpu_total = 0
        mem_gb = 0.0
        try:
            scontrol_cmd = ['scontrol', 'show', 'node', node_name]
            scontrol_proc = subprocess.run(scontrol_cmd, check=True, capture_output=True, text=True, timeout=20)
            node_details = _parse_scontrol_node_output(scontrol_proc.stdout)
            vcpu_total = int(node_details.get('CPUTot', '0'))
            mem_gb = float(node_details.get('RealMemory', '0')) / 1024.0
        except Exception as e:
            logger.warning(f'Failed to get CPU/memory info for {node_name}: {e}')

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
) -> Tuple[Dict[str, List[common.InstanceTypeInfo]], Dict[str, int], Dict[str, int]]:
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
        - qtys_map: Maps GPU type to set of InstanceTypeInfo objects for unique counts found per node.
        - total_capacity: Maps GPU type to total count across all nodes.
        - total_available: Maps GPU type to total free count across all nodes.
    """
    # Use region_filter as partition_filter for Slurm
    partition_filter = region_filter

    # Call the helper function to get node info
    slurm_nodes_info = get_slurm_node_info_list(
        partition_filter=partition_filter,
        name_filter=None, # Name filter applied later on GPU type
        case_sensitive=case_sensitive,
        gpus_only=gpus_only
    )

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
        logger.error(err_msg) # Log as error as it indicates no usable resources found
        raise ValueError(err_msg)

    # Aggregate results into the required format
    qtys_map: Dict[str, Set[common.InstanceTypeInfo]] = collections.defaultdict(set)
    total_capacity: Dict[str, int] = collections.defaultdict(int)
    total_available: Dict[str, int] = collections.defaultdict(int)

    for node_info in slurm_nodes_info:
        gpu_type = node_info['gpu_type']
        node_total_gpus = node_info['total_gpus']
        node_free_gpus = node_info['free_gpus']
        partition = node_info['partition']

        # Apply name filter to the determined GPU type
        regex_flags = 0 if case_sensitive else re.IGNORECASE
        if name_filter and not re.match(name_filter, gpu_type, flags=regex_flags):
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
        if name_filter: filters_applied.append(f'gpu_name={name_filter!r}')
        if quantity_filter: filters_applied.append(f'quantity>={quantity_filter}')
        if partition_filter: filters_applied.append(f'partition={partition_filter!r}')
        if filters_applied: err_msg += f' with filters ({", ".join(filters_applied)})'
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


# --- Implementations for other catalog functions (mostly stubs for Slurm) ---

def list_accelerators(
    gpus_only: bool,
    name_filter: Optional[str],
    region_filter: Optional[str], # Mapped to partition_filter for Slurm
    quantity_filter: Optional[int],
    case_sensitive: bool = True,
    all_regions: bool = False, # Not applicable to Slurm
    require_price: bool = True, # Not applicable to Slurm
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Lists accelerators available in the Slurm cluster.

    Returns dictionary mapping accelerator names to InstanceTypeInfo objects.
    Since Slurm doesn't have fixed \"instance types\", this synthesizes
    InstanceTypeInfo based on observed node configurations. Price is set to 0.
    """
    del all_regions, require_price # Unused for Slurm
    if not gpus_only:
        # Currently only supports listing GPUs for Slurm
        logger.warning('Listing non-GPU accelerators for Slurm is not '
                       'currently supported. Returning GPUs only.')

    # Use region_filter as partition_filter for Slurm
    partition_filter = region_filter

    try:
        # Use the function with actual command logic now
        slurm_gpu_info_list = list_accelerators_realtime(
            gpus_only=gpus_only,
            name_filter=name_filter,
            region_filter=partition_filter,
            quantity_filter=quantity_filter,
            case_sensitive=case_sensitive
        )
    except ValueError as e:
         # If no GPUs found matching filters
         logger.info(f'No matching Slurm GPUs found: {e}')
         return {}
    except exceptions.NotSupportedError as e:
         # If Slurm commands not found or Slurm not enabled
         logger.warning(f'Could not list Slurm accelerators: {e}')
         return {}
    except RuntimeError as e:
         # If Slurm command execution failed
         logger.error(f'Error listing Slurm accelerators: {e}')
         # Re-raise or return empty? Returning empty might be safer for CLI.
         return {}


    # Group info by GPU type to synthesize InstanceTypeInfo
    grouped_info: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for node_info in slurm_gpu_info_list:
        # Only consider nodes that have GPUs (already filtered by list_accelerator_realtime_slurm)
        grouped_info[node_info['gpu_type']].append(node_info)

    results: Dict[str, List[common.InstanceTypeInfo]] = collections.defaultdict(list)
    unique_configs: Dict[Tuple[str, int, str], common.InstanceTypeInfo] = {} # Include partition in key

    for gpu_type, nodes in grouped_info.items():
        # Group by partition as well to create distinct entries per partition
        nodes_by_partition: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
        for node in nodes:
            nodes_by_partition[node['partition']].append(node)

        for partition, partition_nodes in nodes_by_partition.items():
            possible_counts = sorted(list(set(n['total_gpus'] for n in partition_nodes)))
            for count in possible_counts:
                # Create a representative InstanceTypeInfo for this gpu_type/count/partition config
                representative_node = next(n for n in partition_nodes if n['total_gpus'] == count)
                key = (gpu_type, count, partition) # Use partition in key
                if key not in unique_configs:
                    info = common.InstanceTypeInfo(
                        cloud='Slurm',
                        instance_type=None, # No direct instance type concept
                        accelerator_name=gpu_type,
                        accelerator_count=count,
                        cpu_count=representative_node.get('vcpu_count'),
                        device_memory=None, # Placeholder - could try parsing nvidia-smi if needed
                        memory=representative_node.get('memory_gb'),
                        price=0.0,
                        spot_price=0.0,
                        region=partition, # Use partition as region
                    )
                    unique_configs[key] = info
                    results[gpu_type].append(info) # Append to list for the GPU type

    # Sort the InstanceTypeInfo list for each GPU type (e.g., by count)
    for gpu in results:
        results[gpu].sort(key=lambda info: (info.region or '', info.accelerator_count))


    return dict(results)


def instance_type_exists(instance_type: str) -> bool:
    """Checks if a Slurm 'instance type' exists (Not applicable)."""
    del instance_type # Slurm does not have instance types
    logger.debug('instance_type_exists is not applicable to Slurm.')
    return False


def validate_region_zone(
        region_name: Optional[str], # Partition for Slurm
        zone_name: Optional[str] # Not applicable for Slurm
        ) -> Tuple[Optional[str], Optional[str]]:
    """Validates Slurm partition. Zone is ignored."""
    del zone_name # Zone is not applicable
    # Basic validation: Ensure region_name (partition) is a string if not None
    if region_name is not None and not isinstance(region_name, str):
         raise ValueError(f'Invalid Slurm partition (region): {region_name!r}. Must be a string.')
    # TODO: Add actual check if partition exists via sinfo -p <partition> if needed
    logger.debug(f'Validating Slurm partition (region): {region_name}. '
                 'Zone is ignored.')
    return region_name, None


def get_hourly_cost(instance_type: str, # Not applicable
                    use_spot: bool = False, # Not applicable
                    region: Optional[str] = None, # Partition
                    zone: Optional[str] = None # Not applicable
                   ) -> float:
    """Returns the hourly cost for Slurm resources (returns 0)."""
    del instance_type, use_spot, region, zone
    logger.debug('get_hourly_cost is not applicable to Slurm, returning 0.')
    return 0.0


def get_vcpus_mem_from_instance_type(
        instance_type: str # Not applicable
        ) -> Tuple[Optional[float], Optional[float]]:
    """Returns vCPUs and memory for Slurm (returns None, None)."""
    del instance_type
    logger.debug('get_vcpus_mem_from_instance_type is not applicable to Slurm.')
    return None, None


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[Any] = None) -> Optional[str]:
    """Returns default instance type for Slurm (returns None)."""
    del cpus, memory, disk_tier
    logger.debug('get_default_instance_type is not applicable to Slurm.')
    return None


def get_accelerators_from_instance_type(
        instance_type: str # Not applicable
        ) -> Optional[Dict[str, int]]:
    """Returns accelerators for Slurm (returns None)."""
    del instance_type
    logger.debug('get_accelerators_from_instance_type is not applicable to '
                 'Slurm.')
    return None


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None, # Currently ignored for Slurm
    memory: Optional[str] = None, # Currently ignored for Slurm
    use_spot: bool = False, # Not applicable
    region: Optional[str] = None, # Partition
    zone: Optional[str] = None # Not applicable
) -> Tuple[Optional[List[str]], List[str]]:
    """Returns Slurm node configs matching accelerator requirements (Not Implemented)."""
    # This function is complex for Slurm as it maps to 'instance types'.
    # Returning None as the mapping is non-trivial.
    del acc_name, acc_count, cpus, memory, use_spot, region, zone
    logger.warning('get_instance_type_for_accelerator is not implemented '
                   'for Slurm. Returning (None, []).')
    return None, []


def get_accelerator_hourly_cost(
    acc_name: str,
    acc_count: int,
    use_spot: bool = False, # Not applicable
    region: Optional[str] = None, # Partition
    zone: Optional[str] = None # Not applicable
) -> float:
    """Returns the hourly cost of Slurm accelerators (returns 0)."""
    del acc_name, acc_count, use_spot, region, zone
    logger.debug('get_accelerator_hourly_cost is not applicable to Slurm, '
                 'returning 0.')
    return 0.0


# Other functions that are highly cloud-specific and likely not applicable to Slurm
# can be added as stubs returning None or raising NotImplementedError if needed.

# def regions(...) -> List[cloud.Region]: ... # Needs adaptation for partitions
# def get_region_zones_for_instance_type(...) -> List[cloud.Region]: ...
# def get_region_zones_for_accelerators(...) -> List[cloud.Region]: ...
# def check_accelerator_attachable_to_host(...): ... # Not applicable
