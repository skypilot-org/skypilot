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

    Uses `sinfo` and `scontrol show node` commands. Handles GRES format like
    'gpu:<count>' or 'gpu:<type>:<count>'.

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

    # 1. Check Slurm availability (Optional but recommended)
    # ... (keep the TODO or implement check)
    logger.debug('Querying Slurm for GPU availability using sinfo/scontrol...')

    # 2. Execute `sinfo` to get node overview
    sinfo_cmd = [
        'sinfo',
        '-N',  # Node-oriented format
        '-h',  # No header
        # Format: NodeName State Gres
        # %N: NodeName, %t: State, %G: Gres
        '-o', '%N %t %G'
    ]
    try:
        sinfo_proc = subprocess.run(sinfo_cmd,
                                    check=True,
                                    capture_output=True,
                                    text=True,
                                    timeout=20)
        sinfo_output = sinfo_proc.stdout.strip()
    except FileNotFoundError:
        raise exceptions.NotSupportedError(
            'Slurm command `sinfo` not found. '
            'Is Slurm client installed and configured in PATH?')
    except subprocess.TimeoutExpired:
         raise RuntimeError('`sinfo` command timed out.')
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f'Failed to run `sinfo`: {e}\nStderr: {e.stderr}') from e

    if not sinfo_output:
        logger.warning('`sinfo -N` returned no output. No nodes found?')
        return {}, {}, {}

    # 3. Parse `sinfo` output and potentially call `scontrol`
    slurm_nodes_info = []
    # Updated Regex: Matches 'gpu:<count>' or 'gpu:<type>:<count>'
    # Extracts 'gpu' (group 1), optional type (group 3), count (group 4)
    gres_gpu_pattern = re.compile(r'((gpu)(?::([^:]+))?:(\d+))')

    unique_nodes_processed = set()

    # Get partition info for each node
    partition_cmd = ['sinfo', '-N', '-h', '-o', '%N %P']
    try:
        partition_proc = subprocess.run(partition_cmd,
                                      check=True,
                                      capture_output=True,
                                      text=True,
                                      timeout=20)
        partition_output = partition_proc.stdout.strip()
        node_to_partition = {}
        for line in partition_output.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                node_name, partition = parts[0], parts[1]
                node_to_partition[node_name] = partition
    except Exception as e:
        logger.warning(f'Failed to get partition info: {e}')
        node_to_partition = {}

    for line in sinfo_output.splitlines():
        parts = line.split()
        if len(parts) < 3:
             logger.debug(f'Skipping malformed sinfo line: {line}')
             continue
        node_name, state, gres_str = parts[0], parts[1], parts[2]

        # Skip node if already processed (can happen with multi-partition nodes in sinfo output)
        if node_name in unique_nodes_processed:
            continue

        partition = node_to_partition.get(node_name, '')
        if partition_filter and partition != partition_filter:
            continue

        gres_match = gres_gpu_pattern.search(gres_str)
        if not gres_match:
            continue # No 'gpu:...' pattern found in GRES

        # Determine GPU type and count from sinfo GRES match
        gpu_type_from_sinfo = gres_match.group(3) # Might be None
        try:
            total_gpus = int(gres_match.group(4))
        except ValueError:
            logger.warning(f'Could not parse GPU count from GRES {gres_match.group(1)!r} '
                           f'for node {node_name}. Skipping node.')
            continue

        # Get allocated GPUs based on node state
        # State codes: https://slurm.schedmd.com/sinfo.html#SECTION_NODE-STATE-CODES
        allocated_gpus = 0
        if state in ('alloc', 'mix', 'drain', 'drng', 'drained', 'resv', 'comp'):
            # For states where resources might be used, query squeue for jobs on this specific node
            try:
                squeue_cmd = ['squeue', '-w', node_name, '-h', '-o', '%b'] # Get GRES request (%b)
                squeue_proc = subprocess.run(squeue_cmd,
                                           check=True,
                                           capture_output=True,
                                           text=True,
                                           timeout=20)
                squeue_output = squeue_proc.stdout.strip()
                
                node_allocated_gpus = 0
                if squeue_output:
                    # Regex to find gpu count in gres string like 'gpu:2', 'gpu:a10g:2', etc.
                    # It accounts for potential type specification.
                    job_gres_pattern = re.compile(r'gpu(?::[^:]+)*:(\d+)')
                    for line in squeue_output.splitlines():
                        gres_match = job_gres_pattern.search(line)
                        if gres_match:
                            node_allocated_gpus += int(gres_match.group(1))
                    allocated_gpus = node_allocated_gpus
                    logger.debug(f'Node {node_name} ({state}): Found {allocated_gpus} allocated GPUs via squeue.')
                else:
                    logger.debug(f'Node {node_name} ({state}): No jobs found via squeue, assuming 0 allocated GPUs.')
                    allocated_gpus = 0

            except Exception as e:
                logger.warning(f'Failed to get job allocation for node {node_name} via squeue: {e}')
                # Fallback: If squeue fails, conservatively assume allocation based on state
                if state == 'alloc':
                    allocated_gpus = total_gpus
                elif state == 'mix':
                     allocated_gpus = total_gpus // 2 # Best guess for mix
                else: # drain, drained, comp, resv - often means unavailable
                     allocated_gpus = total_gpus
        
        elif state == 'idle':
            allocated_gpus = 0
        # For states like down, maint, etc., free_gpus will be handled later

        # Use GRES name 'gpu' as default type if specific type is absent in sinfo
        determined_gpu_type = gpu_type_from_sinfo if gpu_type_from_sinfo else 'gpu'

        # Apply name filter
        regex_flags = 0 if case_sensitive else re.IGNORECASE
        if name_filter and not re.match(name_filter, determined_gpu_type, flags=regex_flags):
            continue

        # Apply quantity filter
        if quantity_filter and total_gpus < quantity_filter:
            continue

        # Calculate free GPUs
        free_gpus = total_gpus - allocated_gpus if state not in ('down', 'drain', 'drng', 'maint') else 0
        free_gpus = max(0, free_gpus)  # Ensure non-negative

        # Get CPU and memory info
        try:
            scontrol_cmd = ['scontrol', 'show', 'node', node_name]
            scontrol_proc = subprocess.run(scontrol_cmd,
                                         check=True,
                                         capture_output=True,
                                         text=True,
                                         timeout=20)
            node_info = _parse_scontrol_node_output(scontrol_proc.stdout)
            vcpu_total = int(node_info.get('CPUTot', '0'))
            mem_gb = float(node_info.get('RealMemory', '0')) / 1024.0
        except Exception as e:
            logger.warning(f'Failed to get CPU/memory info for {node_name}: {e}')
            vcpu_total = 0
            mem_gb = 0.0

        # Append node info
        slurm_nodes_info.append({
            'node_name': node_name,
            'partition': partition,
            'node_state': state,
            'gpu_type': determined_gpu_type,
            'total_gpus': total_gpus,
            'free_gpus': free_gpus,
            'vcpu_count': vcpu_total,
            'memory_gb': round(mem_gb, 2),
        })
        unique_nodes_processed.add(node_name)

    # 4. Check if any nodes were found after filtering
    if not slurm_nodes_info:
        # Customize error message based on filters
        err_msg = 'No matching GPU nodes found in the Slurm cluster'
        filters_applied = []
        if name_filter:
            filters_applied.append(f'name={name_filter!r}')
        if quantity_filter:
             filters_applied.append(f'quantity>={quantity_filter}')
        if partition_filter:
             filters_applied.append(f'partition={partition_filter!r}')
        if filters_applied:
            err_msg += f' with filters ({", ".join(filters_applied)})'
        err_msg += '.'
        # Example: err_msg += '\\nCheck Slurm configuration (`sky check`) and node states (`sinfo`).'
        logger.error(err_msg) # Log as error as it indicates no usable resources found
        raise ValueError(err_msg)

    # 5. Aggregate results into the required format
    qtys_map: Dict[str, Set[common.InstanceTypeInfo]] = collections.defaultdict(set)
    total_capacity: Dict[str, int] = collections.defaultdict(int)
    total_available: Dict[str, int] = collections.defaultdict(int)

    for node_info in slurm_nodes_info:
        gpu_type = node_info['gpu_type']
        node_total_gpus = node_info['total_gpus']
        node_free_gpus = node_info['free_gpus']
        partition = node_info['partition']

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
