# sky/clouds/service_catalog/slurm_catalog.py
"""Slurm Cloud Service Catalog."""

import collections
import re
import subprocess
import typing
from typing import Any, Dict, List, Optional, Tuple

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
def list_accelerator_realtime_slurm(
    name_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    partition_filter: Optional[str] = None,
    case_sensitive: bool = True,
) -> List[Dict[str, Any]]:
    """Fetches real-time accelerator information from the Slurm cluster.

    Uses `sinfo` and `scontrol show node` commands. Handles GRES format like
    'gpu:<count>' or 'gpu:<type>:<count>'. GPU type determination relies on
    GRES configuration visibility via these commands.

    Args:
        name_filter: Regex filter for accelerator names (e.g., 'V100', 'gpu').
        quantity_filter: Minimum number of accelerators required per node.
        partition_filter: Optional filter for Slurm partitions.
        case_sensitive: Whether name_filter is case-sensitive.

    Returns:
        List of dictionaries, each representing GPU info for a node.

    Raises:
        exceptions.NotSupportedError: If Slurm commands are not found or Slurm
            cloud is not enabled/configured.
        RuntimeError: If Slurm commands fail unexpectedly.
        ValueError: If parsing Slurm output fails or no matching GPUs found.
    """
    # 1. Check Slurm availability (Optional but recommended)
    # ... (keep the TODO or implement check)
    logger.debug('Querying Slurm for GPU availability using sinfo/scontrol...')

    # 2. Execute `sinfo` to get node overview
    sinfo_cmd = [
        'sinfo',
        '-N',  # Node-oriented format
        '-h',  # No header
        # Format: NodeName Partition State Gres(raw) CPUAlloc MemAlloc
        # %N: NodeName, %P: Partition, %T: StateCompact, %G: Gres(raw), %C: CPUAlloc/Tot, %m: MemSize(MB)
        # Using Gres (raw) %G as it's more likely to contain GPU count directly
        # Using StateCompact %T (e.g., idle, alloc, mix, drain, down)
        '-o', '%N %P %T %G %C %m'
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
        return []

    # 3. Parse `sinfo` output and potentially call `scontrol`
    slurm_nodes_info = []
    # Updated Regex: Matches 'gpu:<count>' or 'gpu:<type>:<count>'
    # Extracts 'gpu' (group 1), optional type (group 3), count (group 4)
    gres_gpu_pattern = re.compile(r'((gpu)(?::([^:]+))?:(\d+))')
    # Regex for parsing allocated count from GresUsed='gpu:<count>(...)'
    gres_used_count_pattern = re.compile(r'gpu:(\d+)')

    unique_nodes_processed = set()

    for line in sinfo_output.splitlines():
        parts = line.split()
        if len(parts) < 4:
             logger.debug(f'Skipping malformed sinfo line: {line}')
             continue
        node_name, partition, state, gres_str = parts[0], parts[1], parts[2], parts[3]
        cpu_alloc_str = parts[4] if len(parts) > 4 else '0/0/0/0'
        mem_mb_str = parts[5] if len(parts) > 5 else '0'

        if node_name in unique_nodes_processed:
            continue

        if partition_filter and partition != partition_filter:
            continue

        gres_match = gres_gpu_pattern.search(gres_str)
        if not gres_match:
            continue # No 'gpu:...' pattern found in GRES

        # Determine GPU type and count from sinfo GRES match
        # group 1: full match (e.g., gpu:v100:4 or gpu:4)
        # group 2: gres name ('gpu')
        # group 3: optional type ('v100' or None)
        # group 4: count ('4')
        gres_name = gres_match.group(2) # Should be 'gpu'
        gpu_type_from_sinfo = gres_match.group(3) # Might be None
        try:
            total_gpus = int(gres_match.group(4))
        except ValueError:
            logger.warning(f'Could not parse GPU count from GRES {gres_match.group(1)!r} '
                           f'for node {node_name}. Skipping node.')
            continue

        # Use GRES name 'gpu' as default type if specific type is absent in sinfo
        determined_gpu_type = gpu_type_from_sinfo if gpu_type_from_sinfo else gres_name

        # Apply name filter (matching against the determined type)
        # This allows filtering by 'gpu' or a specific type like 'V100' if available
        regex_flags = 0 if case_sensitive else re.IGNORECASE
        if name_filter and not re.match(name_filter, determined_gpu_type, flags=regex_flags):
            continue

        # Apply quantity filter (total GPUs on node)
        if quantity_filter and total_gpus < quantity_filter:
            continue

        # --- Get Allocated GPUs and potentially refine GPU type using `scontrol show node` ---
        allocated_gpus = 0
        node_details = {}
        try:
            scontrol_cmd = ['scontrol', 'show', 'node', node_name]
            scontrol_proc = subprocess.run(scontrol_cmd, check=True, capture_output=True, text=True, timeout=10)
            node_details = _parse_scontrol_node_output(scontrol_proc.stdout)

            # Refine GPU type if scontrol provides more specific GRES info
            scontrol_gres = node_details.get('Gres', '')
            scontrol_gres_match = gres_gpu_pattern.search(scontrol_gres)
            if scontrol_gres_match and scontrol_gres_match.group(3):
                # Found type like 'gpu:v100:4' in scontrol, prefer this type
                determined_gpu_type = scontrol_gres_match.group(3)
                logger.debug(f'Refined GPU type for {node_name} to '
                             f'{determined_gpu_type} from scontrol.')
                # Re-apply name filter if type was refined
                if name_filter and not re.match(name_filter, determined_gpu_type, flags=regex_flags):
                     logger.debug(f'Node {node_name} skipped after refining type to '
                                  f'{determined_gpu_type} due to name filter.')
                     continue # Skip node if refined type doesn't match filter

            # Find allocated GPUs
            alloc_tres = node_details.get('AllocTRES', '')
            gres_used = node_details.get('GresUsed', '')

            alloc_match = re.search(r'gres/gpu=(\d+)', alloc_tres)
            if alloc_match:
                allocated_gpus = int(alloc_match.group(1))
            else:
                # Try parsing GresUsed with the updated regex
                used_match = gres_used_count_pattern.search(gres_used)
                if used_match:
                    allocated_gpus = int(used_match.group(1))

        except FileNotFoundError:
            logger.warning('`scontrol` command not found. Cannot determine allocated GPUs.')
        except subprocess.TimeoutExpired:
             logger.warning(f'`scontrol show node {node_name}` timed out.')
        except subprocess.CalledProcessError as e:
            logger.warning(f'Failed `scontrol show node {node_name}` (code {e.returncode}). '
                           f'Stderr: {e.stderr.strip()}')
            if state not in ('idle', 'mix', 'unk'): allocated_gpus = total_gpus # Fallback

        # --- Calculate Free GPUs ---
        free_gpus = 0
        if state in ('idle', 'mix', 'unk'):
             allocated_gpus = min(allocated_gpus, total_gpus)
             free_gpus = total_gpus - allocated_gpus
             free_gpus = max(0, free_gpus)

        # --- Extract CPU/Memory info ---
        vcpu_total = 0
        try:
            vcpu_total = int(cpu_alloc_str.split('/')[-1])
        except (IndexError, ValueError):
            logger.debug(f'Could not parse total CPUs from {cpu_alloc_str!r}')
        mem_gb = 0.0
        try:
            mem_gb = float(mem_mb_str) / 1024.0
        except ValueError:
             logger.debug(f'Could not parse memory from {mem_mb_str!r}')

        # Append node info
        slurm_nodes_info.append({
            'node_name': node_name,
            'partition': partition,
            'node_state': state,
            'gpu_type': determined_gpu_type, # Use the determined type
            'total_gpus': total_gpus,
            'free_gpus': free_gpus,
            'vcpu_count': vcpu_total,
            'memory_gb': round(mem_gb, 2),
        })
        unique_nodes_processed.add(node_name)

    # 4. Final check and return
    err_msg = '' # Define err_msg before the conditional check
    if not slurm_nodes_info:
        # Customize error message based on filters
        err_msg = 'No matching GPUs found in the Slurm cluster'
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
        raise ValueError(err_msg)

    # Sort results for consistency (optional)
    slurm_nodes_info.sort(key=lambda x: (x.get('partition', ''), x.get('node_name', '')))

    return slurm_nodes_info


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
        slurm_gpu_info_list = list_accelerator_realtime_slurm(
            name_filter=name_filter,
            quantity_filter=quantity_filter,
            partition_filter=partition_filter,
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
                        zone=None # No zone concept in Slurm
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
