"""Slurm utilities for SkyPilot."""
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from paramiko.config import SSHConfig

from sky import exceptions
from sky import sky_logging
from sky.adaptors import slurm
from sky.utils import annotations
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

DEFAULT_SLURM_PATH = '~/.slurm/config'
SLURM_MARKER_FILE = '.sky_slurm_cluster'

# Regex pattern for parsing GPU GRES strings.
# Format: 'gpu:acc_type:acc_count(optional_extra_info)'
# Examples: 'gpu:H100:8', 'gpu:nvidia_h100_80gb_hbm3:8(S:0-1)', 'gpu:a10g:8'
_GRES_GPU_PATTERN = re.compile(r'^gpu:([^:]+):(\d+)')


def get_slurm_ssh_config() -> SSHConfig:
    """Get the Slurm SSH config."""
    slurm_config_path = os.path.expanduser(DEFAULT_SLURM_PATH)
    slurm_config = SSHConfig.from_path(slurm_config_path)
    return slurm_config


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
                accelerator_type = str(accelerator_type).replace(' ', '_')
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


def get_partition_from_config(provider_config: Dict[str, Any]) -> str:
    """Return the partition from the provider config.

    The concept of partition can be mapped to a cloud zone.
    """
    partition = provider_config.get('partition')
    if partition is None:
        raise ValueError('Partition not specified in provider config.')
    return partition


@annotations.lru_cache(scope='request')
def get_cluster_default_partition(cluster_name: str) -> Optional[str]:
    """Get the default partition for a Slurm cluster.

    Queries the Slurm cluster for the partition marked with an asterisk (*)
    in sinfo output. If no default partition is marked, returns None.
    """
    try:
        ssh_config = get_slurm_ssh_config()
        ssh_config_dict = ssh_config.lookup(cluster_name)
    except Exception as e:
        raise ValueError(
            f'Failed to load SSH configuration from {DEFAULT_SLURM_PATH}: '
            f'{common_utils.format_exception(e)}') from e

    client = slurm.SlurmClient(
        ssh_config_dict['hostname'],
        int(ssh_config_dict.get('port', 22)),
        ssh_config_dict['user'],
        ssh_config_dict['identityfile'][0],
        ssh_proxy_command=ssh_config_dict.get('proxycommand', None),
        ssh_proxy_jump=ssh_config_dict.get('proxyjump', None),
    )

    return client.get_default_partition()


def get_all_slurm_cluster_names() -> List[str]:
    """Get all Slurm cluster names available in the environment.

    Returns:
        List[str]: The list of Slurm cluster names if available,
            an empty list otherwise.
    """
    try:
        ssh_config = get_slurm_ssh_config()
    except FileNotFoundError:
        return []
    except Exception as e:
        raise ValueError(
            f'Failed to load SSH configuration from {DEFAULT_SLURM_PATH}: '
            f'{common_utils.format_exception(e)}') from e

    cluster_names = []
    for cluster in ssh_config.get_hostnames():
        if cluster == '*':
            continue

        cluster_names.append(cluster)

    return cluster_names


def _check_cpu_mem_fits(
        candidate_instance_type: SlurmInstanceType,
        node_list: List[slurm.NodeInfo]) -> Tuple[bool, Optional[str]]:
    """Checks if instance fits on candidate nodes based on CPU and memory.

    We check capacity (not allocatable) because availability can change
    during scheduling, and we want to let the Slurm scheduler handle that.
    """
    # We log max CPU and memory found on the GPU nodes for debugging.
    max_cpu = 0
    max_mem_gb = 0.0

    for node_info in node_list:
        node_cpus = node_info.cpus
        node_mem_gb = node_info.memory_gb

        if node_cpus > max_cpu:
            max_cpu = node_cpus
            max_mem_gb = node_mem_gb

        if (node_cpus >= candidate_instance_type.cpus and
                node_mem_gb >= candidate_instance_type.memory):
            return True, None

    return False, (f'Max found: {max_cpu} CPUs, '
                   f'{common_utils.format_float(max_mem_gb)}G memory')


def check_instance_fits(
        cluster: str,
        instance_type: str,
        partition: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Check if the given instance type fits in the given cluster/partition.

    Args:
        cluster: Name of the Slurm cluster.
        instance_type: The instance type to check.
        partition: Optional partition name. If None, checks all partitions.

    Returns:
        Tuple of (fits, reason) where fits is True if available.
    """
    # Get Slurm node list in the given cluster (region).
    try:
        ssh_config = get_slurm_ssh_config()
    except FileNotFoundError:
        return (False, f'Could not query Slurm cluster {cluster} '
                f'because the Slurm configuration file '
                f'{DEFAULT_SLURM_PATH} does not exist.')
    except Exception as e:  # pylint: disable=broad-except
        return (False, f'Could not query Slurm cluster {cluster} '
                f'because Slurm SSH configuration at {DEFAULT_SLURM_PATH} '
                f'could not be loaded: {common_utils.format_exception(e)}.')
    ssh_config_dict = ssh_config.lookup(cluster)

    client = slurm.SlurmClient(
        ssh_config_dict['hostname'],
        int(ssh_config_dict.get('port', 22)),
        ssh_config_dict['user'],
        ssh_config_dict['identityfile'][0],
        ssh_proxy_command=ssh_config_dict.get('proxycommand', None),
        ssh_proxy_jump=ssh_config_dict.get('proxyjump', None),
    )

    nodes = client.info_nodes()
    default_partition = get_cluster_default_partition(cluster)

    def is_default_partition(node_partition: str) -> bool:
        if default_partition is None:
            return False

        # info_nodes does not strip the '*' from the default partition name.
        # But non-default partition names can also end with '*',
        # so we need to check whether the partition name without the '*'
        # is the same as the default partition name.
        return (node_partition.endswith('*') and
                node_partition[:-1] == default_partition)

    partition_suffix = ''
    if partition is not None:
        filtered = []
        for node_info in nodes:
            node_partition = node_info.partition
            if is_default_partition(node_partition):
                # Strip '*' from default partition name.
                node_partition = node_partition[:-1]
            if node_partition == partition:
                filtered.append(node_info)
        nodes = filtered
        partition_suffix = f' in partition {partition}'

    slurm_instance_type = SlurmInstanceType.from_instance_type(instance_type)
    acc_count = (slurm_instance_type.accelerator_count
                 if slurm_instance_type.accelerator_count is not None else 0)
    acc_type = slurm_instance_type.accelerator_type
    candidate_nodes = nodes
    not_fit_reason_prefix = (
        f'No nodes found with enough '
        f'CPU (> {slurm_instance_type.cpus} CPUs) and/or '
        f'memory (> {slurm_instance_type.memory} G){partition_suffix}. ')
    if acc_type is not None:
        assert acc_count is not None, (acc_type, acc_count)

        gpu_nodes = []
        for node_info in nodes:
            gres_str = node_info.gres
            # Extract the GPU type and count from the GRES string
            match = _GRES_GPU_PATTERN.match(gres_str)
            if not match:
                continue

            node_acc_type = match.group(1).lower()
            node_acc_count = int(match.group(2))

            # TODO(jwj): Handle status check.

            # Check if the node has the requested GPU type and at least the
            # requested count
            if (node_acc_type == acc_type.lower() and
                    node_acc_count >= acc_count):
                gpu_nodes.append(node_info)
        if len(gpu_nodes) == 0:
            return (False,
                    f'No GPU nodes found with at least {acc_type}:{acc_count} '
                    f'on the cluster.')

        candidate_nodes = gpu_nodes
        not_fit_reason_prefix = (
            f'GPU nodes with {acc_type}{partition_suffix} do not have '
            f'enough CPU (> {slurm_instance_type.cpus} CPUs) and/or '
            f'memory (> {slurm_instance_type.memory} G). ')

    # Check if CPU and memory requirements are met on at least one
    # candidate node.
    fits, reason = _check_cpu_mem_fits(slurm_instance_type, candidate_nodes)
    if not fits and reason is not None:
        reason = not_fit_reason_prefix + reason
    return fits, reason


# GRES names are highly unlikely to change within a cluster.
# TODO(kevin): Cache using sky/utils/db/kv_cache.py too.
@annotations.lru_cache(scope='global', maxsize=10)
def get_gres_gpu_type(cluster: str, requested_gpu_type: str) -> str:
    """Get the actual GPU type as it appears in the cluster's GRES.

    Args:
        cluster: Name of the Slurm cluster.
        requested_gpu_type: The GPU type requested by the user.

    Returns:
        The actual GPU type as it appears in the cluster's GRES string.
        Falls back to the requested type if not found.
    """
    try:
        ssh_config = get_slurm_ssh_config()
        ssh_config_dict = ssh_config.lookup(cluster)
        client = slurm.SlurmClient(
            ssh_config_dict['hostname'],
            int(ssh_config_dict.get('port', 22)),
            ssh_config_dict['user'],
            ssh_config_dict['identityfile'][0],
            ssh_proxy_command=ssh_config_dict.get('proxycommand', None),
            ssh_proxy_jump=ssh_config_dict.get('proxyjump', None),
        )

        nodes = client.info_nodes()

        for node_info in nodes:
            match = _GRES_GPU_PATTERN.match(node_info.gres)
            if match:
                node_gpu_type = match.group(1)
                if node_gpu_type.lower() == requested_gpu_type.lower():
                    return node_gpu_type
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(
            'Failed to determine the exact GPU GRES type from the Slurm '
            f'cluster {cluster!r}. Falling back to '
            f'{requested_gpu_type.lower()!r}. This may cause issues if the '
            f'casing is incorrect. Error: {common_utils.format_exception(e)}')

    # GRES names are more commonly in lowercase from what we've seen so far.
    return requested_gpu_type.lower()


def _get_slurm_node_info_list(
        slurm_cluster_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Gathers detailed information about each node in the Slurm cluster.

    Raises:
        FileNotFoundError: If the Slurm configuration file does not exist.
        ValueError: If no Slurm cluster name is found in the Slurm
                    configuration file.
    """
    # 1. Get node state and GRES using sinfo

    # can raise FileNotFoundError if config file does not exist.
    slurm_config = get_slurm_ssh_config()
    if slurm_cluster_name is None:
        slurm_cluster_names = get_all_slurm_cluster_names()
        if slurm_cluster_names:
            slurm_cluster_name = slurm_cluster_names[0]
    if slurm_cluster_name is None:
        raise ValueError(
            f'No Slurm cluster name found in the {DEFAULT_SLURM_PATH} '
            f'configuration.')
    slurm_config_dict = slurm_config.lookup(slurm_cluster_name)
    logger.debug(f'Slurm config dict: {slurm_config_dict}')
    slurm_client = slurm.SlurmClient(
        slurm_config_dict['hostname'],
        int(slurm_config_dict.get('port', 22)),
        slurm_config_dict['user'],
        slurm_config_dict['identityfile'][0],
        ssh_proxy_command=slurm_config_dict.get('proxycommand', None),
        ssh_proxy_jump=slurm_config_dict.get('proxyjump', None),
    )
    node_infos = slurm_client.info_nodes()

    if not node_infos:
        logger.warning(
            f'`sinfo -N` returned no output on cluster {slurm_cluster_name}. '
            f'No nodes found?')
        return []

    # 2. Process each node, aggregating partitions per node
    slurm_nodes_info: Dict[str, Dict[str, Any]] = {}
    gres_gpu_pattern = re.compile(r'((gpu)(?::([^:]+))?:(\d+))')

    for node_info in node_infos:
        node_name = node_info.node
        state = node_info.state
        gres_str = node_info.gres
        partition = node_info.partition

        if node_name in slurm_nodes_info:
            slurm_nodes_info[node_name]['partitions'].append(partition)
            continue

        # Extract GPU info from GRES
        gres_match = gres_gpu_pattern.search(gres_str)

        total_gpus = 0
        gpu_type_from_sinfo = None  # Default to None for CPU-only nodes
        if gres_match:
            try:
                total_gpus = int(gres_match.group(4))
                if gres_match.group(3):
                    gpu_type_from_sinfo = gres_match.group(3).upper()
                # If total_gpus > 0 but no type, default to 'GPU'
                elif total_gpus > 0:
                    gpu_type_from_sinfo = 'GPU'
            except ValueError:
                logger.warning(
                    f'Could not parse GPU count from GRES for {node_name}.')

        # Get allocated GPUs via squeue
        allocated_gpus = 0
        # TODO(zhwu): move to enum
        if state in ('alloc', 'mix', 'drain', 'drng', 'drained', 'resv',
                     'comp'):
            try:
                jobs_gres = slurm_client.get_jobs_gres(node_name)
                if jobs_gres:
                    job_gres_pattern = re.compile(r'gpu(?::[^:]+)*:(\d+)')
                    for job_line in jobs_gres:
                        gres_job_match = job_gres_pattern.search(job_line)
                        if gres_job_match:
                            allocated_gpus += int(gres_job_match.group(1))
            except Exception as e:  # pylint: disable=broad-except
                if state == 'alloc':
                    # We can infer allocated GPUs only if the node is
                    # in 'alloc' state.
                    allocated_gpus = total_gpus
                else:
                    # Otherwise, just raise the error.
                    raise e
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

        slurm_nodes_info[node_name] = {
            'node_name': node_name,
            'slurm_cluster_name': slurm_cluster_name,
            'partitions': [partition],
            'node_state': state,
            'gpu_type': gpu_type_from_sinfo,
            'total_gpus': total_gpus,
            'free_gpus': free_gpus,
            'vcpu_count': vcpu_total,
            'memory_gb': round(mem_gb, 2),
        }

    for node_info in slurm_nodes_info.values():
        partitions = node_info.pop('partitions')
        node_info['partition'] = ','.join(str(p) for p in partitions)

    return list(slurm_nodes_info.values())


def slurm_node_info(
        slurm_cluster_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Gets detailed information for each node in the Slurm cluster.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, each containing node info.
    """
    try:
        node_list = _get_slurm_node_info_list(
            slurm_cluster_name=slurm_cluster_name)
    except (RuntimeError, exceptions.NotSupportedError) as e:
        logger.debug(f'Could not retrieve Slurm node info: {e}')
        return []
    return node_list


def is_inside_slurm_cluster() -> bool:
    # Check for the marker file in the current home directory. When run by
    # the skylet on a compute node, the HOME environment variable is set to
    # the cluster's sky home directory by the SlurmCommandRunner.
    marker_file = os.path.join(os.path.expanduser('~'), SLURM_MARKER_FILE)
    return os.path.exists(marker_file)


@annotations.lru_cache(scope='request')
def get_partitions(cluster_name: str) -> List[str]:
    """Get unique partition names available in a Slurm cluster.

    Args:
        cluster_name: Name of the Slurm cluster.

    Returns:
        List of unique partition names available in the cluster.
        The default partition appears first,
        and the rest are sorted alphabetically.
    """
    try:
        slurm_config = SSHConfig.from_path(
            os.path.expanduser(DEFAULT_SLURM_PATH))
        slurm_config_dict = slurm_config.lookup(cluster_name)

        client = slurm.SlurmClient(
            slurm_config_dict['hostname'],
            int(slurm_config_dict.get('port', 22)),
            slurm_config_dict['user'],
            slurm_config_dict['identityfile'][0],
            ssh_proxy_command=slurm_config_dict.get('proxycommand', None),
            ssh_proxy_jump=slurm_config_dict.get('proxyjump', None),
        )

        partitions_info = client.get_partitions_info()
        default_partitions = []
        other_partitions = []
        for partition in partitions_info:
            if partition.is_default:
                default_partitions.append(partition.name)
            else:
                other_partitions.append(partition.name)
        return default_partitions + sorted(other_partitions)
    except Exception as e:  # pylint: disable=broad-except
        raise ValueError(
            f'Failed to get partitions for cluster '
            f'{cluster_name}: {common_utils.format_exception(e)}') from e
