"""Slurm utilities for SkyPilot."""
import collections
import math
import os
import re
import shlex
import subprocess
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from paramiko.config import SSHConfig

from sky import exceptions
from sky import models
from sky import sky_logging
from sky.adaptors import slurm
from sky.utils import annotations
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import timeline

logger = sky_logging.init_logger(__name__)

# TODO(jwj): Choose commonly used default values.
DEFAULT_SLURM_PATH = '~/.slurm/config'
DEFAULT_CLUSTER_NAME = "localcluster"
DEFAULT_PARTITION = "dev"

# For all job states, please refer to
# https://slurm.schedmd.com/squeue.html#SECTION_JOB-STATE-CODES.
JOB_STATES = {
    "cancelled",
    "completed",
    "failed",
    "pending",
    "running",
}


class SlurmInstanceType:
    """Class to represent the "Instance Type" in a Slurm cluster.

    Since Slurm does not have a notion of instances, we generate
    virtual instance types that represent the resources requested by a
    Slurm worker node.

    This name captures the following resource requests:
        - CPU
        - Memory
        - Accelerators

    The name format is "{n}CPU--{k}GB" where n is the number of vCPUs and
    k is the amount of memory in GB. Accelerators can be specified by
    appending "--{type}:{a}" where type is the accelerator type and a
    is the number of accelerators.
    CPU and memory can be specified as floats. Accelerator count must be int.

    Examples:
        - 4CPU--16GB
        - 0.5CPU--1.5GB
        - 4CPU--16GB--V100:1
    """

    def __init__(self,
                 cpus: float,
                 memory: float,
                 accelerator_count: Optional[int] = None,
                 accelerator_type: Optional[str] = None):
        self.cpus = cpus
        self.memory = memory
        self.accelerator_count = accelerator_count
        self.accelerator_type = accelerator_type

    @property
    def name(self) -> str:
        """Returns the name of the instance."""
        assert self.cpus is not None
        assert self.memory is not None
        name = (f'{common_utils.format_float(self.cpus)}CPU--'
                f'{common_utils.format_float(self.memory)}GB')
        if self.accelerator_count is not None:
            # Replace spaces with underscores in accelerator type to make it a
            # valid logical instance type name.
            assert self.accelerator_type is not None, self.accelerator_count
            acc_name = self.accelerator_type.replace(' ', '_')
            name += f'--{acc_name}:{self.accelerator_count}'
        return name

    @staticmethod
    def is_valid_instance_type(name: str) -> bool:
        """Returns whether the given name is a valid instance type."""
        pattern = re.compile(
            r'^(\d+(\.\d+)?CPU--\d+(\.\d+)?GB)(--[\w\d-]+:\d+)?$')
        return bool(pattern.match(name))

    @classmethod
    def _parse_instance_type(
            cls,
            name: str) -> Tuple[float, float, Optional[int], Optional[str]]:
        """Parses and returns resources from the given InstanceType name.

        Returns:
            cpus | float: Number of CPUs
            memory | float: Amount of memory in GB
            accelerator_count | float: Number of accelerators
            accelerator_type | str: Type of accelerator
        """
        pattern = re.compile(
            r'^(?P<cpus>\d+(\.\d+)?)CPU--(?P<memory>\d+(\.\d+)?)GB(?:--(?P<accelerator_type>[\w\d-]+):(?P<accelerator_count>\d+))?$'  # pylint: disable=line-too-long
        )
        match = pattern.match(name)
        if match is not None:
            cpus = float(match.group('cpus'))
            memory = float(match.group('memory'))
            accelerator_count = match.group('accelerator_count')
            accelerator_type = match.group('accelerator_type')
            if accelerator_count is not None:
                accelerator_count = int(accelerator_count)
                # This is to revert the accelerator types with spaces back to
                # the original format.
                accelerator_type = str(accelerator_type).replace('_', ' ')
            else:
                accelerator_count = None
                accelerator_type = None
            return cpus, memory, accelerator_count, accelerator_type
        else:
            raise ValueError(f'Invalid instance name: {name}')

    @classmethod
    def from_instance_type(cls, name: str) -> 'SlurmInstanceType':
        """Returns an instance name object from the given name."""
        if not cls.is_valid_instance_type(name):
            raise ValueError(f'Invalid instance name: {name}')
        cpus, memory, accelerator_count, accelerator_type = \
            cls._parse_instance_type(name)
        return cls(cpus=cpus,
                   memory=memory,
                   accelerator_count=accelerator_count,
                   accelerator_type=accelerator_type)

    @classmethod
    def from_resources(cls,
                       cpus: float,
                       memory: float,
                       accelerator_count: Union[float, int] = 0,
                       accelerator_type: str = '') -> 'SlurmInstanceType':
        """Returns an instance name object from the given resources.

        If accelerator_count is not an int, it will be rounded up since GPU
        requests in Slurm must be int.

        NOTE: Should we take MIG management into account? See
        https://slurm.schedmd.com/gres.html#MIG_Management.
        """
        name = f'{cpus}CPU--{memory}GB'
        # Round up accelerator_count if it is not an int.
        accelerator_count = math.ceil(accelerator_count)
        if accelerator_count > 0:
            name += f'--{accelerator_type}:{accelerator_count}'
        return cls(cpus=cpus,
                   memory=memory,
                   accelerator_count=accelerator_count,
                   accelerator_type=accelerator_type)

    def __str__(self):
        return self.name

    def __repr__(self):
        return (f'SlurmInstanceType(cpus={self.cpus!r}, '
                f'memory={self.memory!r}, '
                f'accelerator_count={self.accelerator_count!r}, '
                f'accelerator_type={self.accelerator_type!r})')


def instance_id(job_id: str, node: str) -> str:
    """Generates the SkyPilot-defined instance ID for Slurm.

    A (job id, node) pair is unique within a Slurm cluster.
    """
    return f'job{job_id}-{node}'


def get_cluster_name_from_config(provider_config: Dict[str, Any]) -> str:
    """Return the cluster name from the provider config.

    The concept of cluster can be mapped to a cloud region.
    """
    return provider_config.get('cluster', DEFAULT_CLUSTER_NAME)


def get_partition_from_config(provider_config: Dict[str, Any]) -> str:
    """Return the partition from the provider config.

    The concept of partition can be mapped to a cloud zone.
    """
    return provider_config.get('partition', DEFAULT_PARTITION)


def get_all_slurm_cluster_names() -> List[str]:
    """Get all Slurm cluster names available in the environment.

    Returns:
        List[str]: The list of Slurm cluster names if available,
            an empty list otherwise.
    """
    try:
        ssh_config = SSHConfig.from_path(os.path.expanduser(DEFAULT_SLURM_PATH))
    except Exception as e:
        raise ValueError(
            f'Failed to load SSH configuration from {DEFAULT_SLURM_PATH}: {common_utils.format_exception(e)}'
        )

    cluster_names = []
    for cluster in ssh_config.get_hostnames():
        if cluster == "*":
            continue

        cluster_names.append(cluster)

    return cluster_names


def check_instance_fits(cluster: str,
                        instance_type: str) -> Tuple[bool, Optional[str]]:
    """Check if the given instance type fits in the given cluster."""
    # Get Slurm node list in the given cluster (region).
    ssh_config = SSHConfig.from_path(os.path.expanduser(DEFAULT_SLURM_PATH))
    ssh_config_dict = ssh_config.lookup(cluster)

    client = slurm.SlurmClient(
        ssh_config_dict['hostname'],
        ssh_config_dict.get('port', 22),
        ssh_config_dict['user'],
        ssh_config_dict['identityfile'][0],
        ssh_proxy_command=ssh_config_dict.get('proxycommand', None),
    )

    nodes = client.info_nodes()

    s = SlurmInstanceType.from_instance_type(instance_type)
    acc_count = s.accelerator_count if s.accelerator_count else 0
    acc_type = s.accelerator_type if s.accelerator_type else None
    if acc_type is not None:
        assert acc_count is not None, (acc_type, acc_count)

        gres_to_match = f'{acc_type}:{acc_count}'.lower()
        gpu_nodes = []
        for node in nodes:
            node_name, node_state, gres_str = node.split()
            # gres_str is like 'gpu:acc_type:acc_count'
            gres_list = gres_str.split(':')
            gres_str = ':'.join(gres_list[1:]).lower()

            # TODO(jwj): Handle status check.

            if gres_str == gres_to_match:
                gpu_nodes.append(node)
        if len(gpu_nodes) == 0:
            return False, f'No GPU nodes found with {acc_type}:{acc_count} on the cluster.'

        candidate_nodes = gpu_nodes
        not_fit_reason_prefix = (f'GPU nodes with {acc_type} do not have '
                                 f'enough CPU (> {s.cpus} CPUs) and/or '
                                 f'memory (> {s.memory} G). ')
    else:
        candidate_nodes = nodes
        not_fit_reason_prefix = (f'No nodes found with enough '
                                 f'CPU (> {s.cpus} CPUs) and/or '
                                 f'memory (> {s.memory} G). ')

    # TODO(jwj): Enable CPU and memory check.
    # fits, reason = check_cpu_mem_fits(s, candidate_nodes)
    # if not fits:
    #     if reason is not None:
    #         reason = not_fit_reason_prefix + reason
    #     return fits, reason
    # else:
    #     return fits, reason

    if len(candidate_nodes) != 0:
        return True, None
    else:
        return False, not_fit_reason_prefix


def _get_slurm_node_info_list(slurm_cluster_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Gathers detailed information about each node in the Slurm cluster."""
    # 1. Get node state and GRES using sinfo
    slurm_config = SSHConfig.from_path(os.path.expanduser(DEFAULT_SLURM_PATH))
    if slurm_cluster_name is None:
        slurm_cluster_names = get_all_slurm_cluster_names()
        if slurm_cluster_names:
            slurm_cluster_name = slurm_cluster_names[0]
    if slurm_cluster_name is None:
        raise ValueError(f'No Slurm cluster name found in the {DEFAULT_SLURM_PATH} configuration.')
    slurm_config_dict = slurm_config.lookup(slurm_cluster_name)
    logger.debug(f'Slurm config dict: {slurm_config_dict}')
    slurm_client = slurm.SlurmClient(
        slurm_config_dict['hostname'],
        slurm_config_dict.get('port', 22),
        slurm_config_dict['user'],
        slurm_config_dict['identityfile'][0],
        ssh_proxy_command=slurm_config_dict.get('proxycommand', None),
    )
    sinfo_output = slurm_client.info_nodes()

    if not sinfo_output:
        logger.warning(
            f'`sinfo -N` returned no output on cluster {slurm_cluster_name}. No nodes found?'
        )
        return []

    # 2. Get partition info
    node_to_partition = {}
    partitions = slurm_client.info_partitions()
    logger.debug(f'Partitions: {partitions}')
    for line in partitions:
        parts = line.split()
        if len(parts) >= 2:
            node_to_partition[parts[0]] = parts[1]

    # 3. Process each node
    slurm_nodes_info = []
    unique_nodes_processed = set()
    gres_gpu_pattern = re.compile(r'((gpu)(?::([^:]+))?:(\d+))')

    for line in sinfo_output:
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
        # TODO(zhwu): move to enum
        if state in ('alloc', 'mix', 'drain', 'drng', 'drained', 'resv',
                     'comp'):
            try:
                node_jobs = slurm_client.get_node_jobs(node_name)
                if node_jobs:
                    job_gres_pattern = re.compile(r'gpu(?::[^:]+)*:(\d+)')
                    for job_line in node_jobs:
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
            node_details = slurm_client.node_details(node_name)
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


# def slurm_gpu_availability(
#         slurm_cluster_name: str,
#         name_filter: Optional[str] = None,
#         quantity_filter: Optional[int] = None,
#         env_vars: Optional[Dict[str, str]] = None,
#         **kwargs) -> List[Tuple[str, List[models.RealtimeGpuAvailability]]]:
#     """Gets Slurm real-time GPU availability grouped by partition.

#     This function calls the Slurm backend to fetch GPU info.

#     Args:
#         name_filter: Optional name filter for GPUs.
#         quantity_filter: Optional quantity filter for GPUs.
#         env_vars: Environment variables (may be needed for backend).
#         kwargs: Additional keyword arguments.

#     Returns:
#         A list of tuples, where each tuple contains:
#         - partition_name (str): The name of the Slurm partition.
#         - availability_list (List[models.RealtimeGpuAvailability]): A list
#             of RealtimeGpuAvailability objects for that partition.
#         Example structure:
#         [
#             ('gpu_partition_1', [
#                 RealtimeGpuAvailability(gpu='V100', counts=[1, 2, 4, 8],
#                                         capacity=16, available=10),
#                 RealtimeGpuAvailability(gpu='A100', counts=[1, 2, 4, 8],
#                                         capacity=8, available=0),
#             ]),
#             ('gpu_partition_2', [
#                 RealtimeGpuAvailability(gpu='V100', counts=[1, 2, 4],
#                                         capacity=4, available=4),
#             ])
#         ]

#     Raises:
#         ValueError: If Slurm is not configured or no matching GPUs are found.
#         exceptions.NotSupportedError: If Slurm is not enabled or configured.
#     """
#     del env_vars, kwargs  # Currently unused

#     result_list: List[Tuple[str, List[models.RealtimeGpuAvailability]]] = []

#     # Get all node info once to avoid repeated calls in the loop
#     try:
#         all_nodes_info = _get_slurm_node_info_list(slurm_cluster_name)
#     except (RuntimeError, exceptions.NotSupportedError) as e:
#         logger.warning(f'Could not retrieve any Slurm node info: {e}')
#         all_nodes_info = []

#     # Group nodes by partition
#     nodes_by_partition: Dict[str,
#                              List[Dict[str,
#                                        Any]]] = collections.defaultdict(list)
#     for node_info in all_nodes_info:
#         partition = node_info.get('partition', 'unknown_partition')
#         nodes_by_partition[partition].append(node_info)

#     for partition, nodes_in_partition in sorted(nodes_by_partition.items()):
#         availability_list: List[models.RealtimeGpuAvailability] = []

#         # Calculate partition-specific totals and observed counts
#         partition_total_capacity: Dict[str, int] = collections.defaultdict(int)
#         partition_total_available: Dict[str, int] = collections.defaultdict(int)
#         partition_gpu_counts: Dict[str, Set[int]] = collections.defaultdict(set)
#         max_free_gpus_per_node_type: Dict[str,
#                                           int] = collections.defaultdict(int)

#         for node_info in nodes_in_partition:
#             gpu_type = node_info.get('gpu_type')
#             # Apply name_filter here if provided
#             if not gpu_type or (name_filter is not None and
#                                 name_filter.lower() != gpu_type.lower()):
#                 continue  # Skip nodes without GPU type or not matching filter

#             total_gpus = node_info.get('total_gpus', 0)
#             free_gpus = node_info.get('free_gpus', 0)

#             partition_total_capacity[gpu_type] += total_gpus
#             partition_total_available[gpu_type] += free_gpus
#             if total_gpus > 0:
#                 partition_gpu_counts[gpu_type].add(total_gpus)
#             # Track max free GPUs on a single node for this type
#             max_free_gpus_per_node_type[gpu_type] = max(
#                 max_free_gpus_per_node_type[gpu_type], free_gpus)

#         # Create RealtimeGpuAvailability objects for each GPU type in this
#         # partition
#         for gpu_type in sorted(partition_gpu_counts.keys()):
#             # Get max free GPUs on a single node for this type
#             max_requestable_on_single_node = max_free_gpus_per_node_type.get(
#                 gpu_type, 0)

#             # Apply quantity_filter here if provided
#             if (quantity_filter is not None and
#                     max_requestable_on_single_node < quantity_filter):
#                 continue  # Skip GPU type if max free is less than filter

#             # Generate powers of 2 for requestable quantities (1, 2, 4, 8, etc.)
#             # plus the actual maximum if it's not a power of 2
#             requestable_quantities = []
#             count = 1
#             while count <= max_requestable_on_single_node:
#                 requestable_quantities.append(count)
#                 count *= 2

#             # Add the actual maximum if not already included
#             if requestable_quantities and requestable_quantities[
#                     -1] != max_requestable_on_single_node:
#                 requestable_quantities.append(max_requestable_on_single_node)

#             # Sort the quantities for consistent ordering
#             requestable_quantities.sort()

#             capacity = partition_total_capacity.get(gpu_type, 0)
#             available = partition_total_available.get(gpu_type, 0)

#             availability_list.append(
#                 models.RealtimeGpuAvailability(
#                     gpu_type,
#                     requestable_quantities,
#                     capacity,
#                     available,
#                 ))

#         if availability_list:
#             result_list.append((partition, availability_list))

#     # Check if any GPUs were found after processing all nodes/partitions
#     if not result_list:
#         err_msg = 'No GPUs found in the Slurm cluster matching the criteria.'
#         filters = []
#         if name_filter:
#             filters.append(f'name={name_filter!r}')
#         if quantity_filter:
#             filters.append(f'quantity>={quantity_filter}')
#         if filters:
#             err_msg = (f'Resource matching filters ({", ".join(filters)}) '
#                        f'not found in the Slurm cluster.')
#         raise ValueError(err_msg)

#     return result_list


def slurm_node_info(slurm_cluster_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Gets detailed information for each node in the Slurm cluster.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, each containing node info.
    """
    try:
        node_list = _get_slurm_node_info_list(slurm_cluster_name=slurm_cluster_name)
    except (RuntimeError, exceptions.NotSupportedError) as e:
        logger.debug(f'Could not retrieve Slurm node info: {e}')
        return []
    return node_list


def get_all_partitions(cluster_name: str) -> List[str]:
    """Gets all partitions in the Slurm cluster."""
    node_list = slurm_node_info(cluster_name)
    return [node['partition'] for node in node_list]
