"""Slurm utilities for SkyPilot."""
import json
import math
import os
import re
import shlex
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from paramiko.config import SSHConfig

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.adaptors import slurm
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import gpu_names
from sky.utils.db import kv_cache

logger = sky_logging.init_logger(__name__)

DEFAULT_SLURM_PATH = '~/.slurm/config'

_VAR_PATTERN = re.compile(r'\$(\w+|\{[^}]*\})')

SLURM_MARKER_FILE = '.sky_slurm_cluster'
SLURM_CONTAINER_MARKER_FILE = '.sky_slurm_container'

# Regex pattern for parsing GPU GRES strings.
# Format: 'gpu[:acc_type]:acc_count(optional_extra_info)'
# Examples: 'gpu:8', 'gpu:H100:8', 'gpu:nvidia_h100_80gb_hbm3:8(S:0-1)'
_GRES_GPU_PATTERN = re.compile(r'\bgpu:(?:(?P<type>[^:(]+):)?(?P<count>\d+)',
                               re.IGNORECASE)

_SLURM_NODES_INFO_CACHE_TTL = 30 * 60
# Proctrack type is highly unlikely to change.
_SLURM_PROCTRACK_TYPE_CACHE_TTL = 24 * 60 * 60
# Pyxis plugin availability is unlikely to change frequently.
_SLURM_PYXIS_CHECK_CACHE_TTL = 24 * 60 * 60
# FUSE availability is unlikely to change frequently.
_SLURM_FUSE_CHECK_CACHE_TTL = 24 * 60 * 60


def expand_path_vars(path: str, env: Dict[str, str]) -> str:
    """Expand $VAR and ${VAR} in path using the given environment dict.

    Inspired by os.path.expandvars from CPython:
    https://github.com/python/cpython/blob/56c4f10d/Lib/posixpath.py#L284-L334
    Only $name and ${name} forms are expanded. Unknown variables are
    left unchanged.
    """

    def _repl(m: re.Match) -> str:
        name = m.group(1)
        if name.startswith('{') and name.endswith('}'):
            name = name[1:-1]
        return env.get(name, m.group(0))

    return _VAR_PATTERN.sub(_repl, path)


def get_gpu_type_and_count(gres_str: str) -> Tuple[Optional[str], int]:
    """Parses GPU type and count from a GRES string.

    Returns:
        A tuple of (GPU type, GPU count). If no GPU is found, returns (None, 0).
    """
    match = _GRES_GPU_PATTERN.search(gres_str)
    if not match:
        return None, 0
    return match.group('type'), int(match.group('count'))


def pyxis_container_name(cluster_name_on_cloud: str) -> str:
    """Get the pyxis container name that gets passed to --container-name."""
    return cluster_name_on_cloud


# SSH host key filename for sshd.
SLURM_SSHD_HOST_KEY_FILENAME = 'skypilot_host_key'


def get_slurm_ssh_config() -> SSHConfig:
    """Get the Slurm SSH config."""
    slurm_config_path = os.path.expanduser(DEFAULT_SLURM_PATH)
    slurm_config = SSHConfig.from_path(slurm_config_path)
    return slurm_config


def get_identity_file(ssh_config_dict: Dict[str, Any]) -> Optional[str]:
    """Get the first identity file from SSH config, or None if not specified."""
    identity_files = ssh_config_dict.get('identityfile')
    if identity_files:
        return identity_files[0]
    return None


def get_identities_only(ssh_config_dict: Dict[str, Any]) -> bool:
    """Check if IdentitiesOnly is set to yes in SSH config.

    Returns True if IdentitiesOnly is explicitly set to 'yes', False otherwise.
    """
    identities_only = ssh_config_dict.get('identitiesonly', '')
    return identities_only.lower() == 'yes'


@annotations.lru_cache(scope='request')
def _get_slurm_nodes_info(cluster: str) -> List[slurm.NodeInfo]:
    cache_key = f'slurm:nodes_info:{cluster}'
    cached = kv_cache.get_cache_entry(cache_key)
    if cached is not None:
        logger.debug(f'Slurm nodes info found in cache ({cache_key})')
        return [slurm.NodeInfo(**item) for item in json.loads(cached)]

    ssh_config = get_slurm_ssh_config()
    ssh_config_dict = ssh_config.lookup(cluster)
    client = slurm.SlurmClient(
        ssh_config_dict['hostname'],
        int(ssh_config_dict.get('port', 22)),
        ssh_config_dict['user'],
        get_identity_file(ssh_config_dict),
        ssh_proxy_command=ssh_config_dict.get('proxycommand', None),
        ssh_proxy_jump=ssh_config_dict.get('proxyjump', None),
        identities_only=get_identities_only(ssh_config_dict),
    )
    nodes_info = client.info_nodes()

    try:
        # Nodes in a cluster are unlikely to change frequently, so cache
        # the result for a short period of time.
        kv_cache.add_or_update_cache_entry(
            cache_key, json.dumps([n._asdict() for n in nodes_info]),
            time.time() + _SLURM_NODES_INFO_CACHE_TTL)
    except Exception as e:  # pylint: disable=broad-except
        # Catch the error and continue.
        # Failure to cache the result is not critical to the
        # success of this function.
        logger.debug(f'Failed to cache slurm nodes info for {cluster}: '
                     f'{common_utils.format_exception(e)}')

    return nodes_info


def get_proctrack_type(cluster: str) -> Optional[str]:
    """Get the ProctrackType setting from Slurm configuration."""
    cache_key = f'slurm:proctrack_type:{cluster}'
    cached = kv_cache.get_cache_entry(cache_key)
    if cached is not None:
        logger.debug(f'Slurm proctrack type found in cache ({cache_key})')
        return cached

    ssh_config = get_slurm_ssh_config()
    ssh_config_dict = ssh_config.lookup(cluster)
    client = slurm.SlurmClient(
        ssh_config_dict['hostname'],
        int(ssh_config_dict.get('port', 22)),
        ssh_config_dict['user'],
        get_identity_file(ssh_config_dict),
        ssh_proxy_command=ssh_config_dict.get('proxycommand', None),
        ssh_proxy_jump=ssh_config_dict.get('proxyjump', None),
        identities_only=get_identities_only(ssh_config_dict),
    )
    proctrack_type = client.get_proctrack_type()

    if proctrack_type is not None:
        try:
            kv_cache.add_or_update_cache_entry(
                cache_key, proctrack_type,
                time.time() + _SLURM_PROCTRACK_TYPE_CACHE_TTL)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to cache slurm proctrack type for {cluster}: '
                         f'{common_utils.format_exception(e)}')

    return proctrack_type


def _check_cluster_feature(
    cluster: str,
    feature_name: str,
    check_fn: Callable[[slurm.SlurmClient], bool],
    cache_ttl: int,
) -> bool:
    """Check if a feature is available on a Slurm cluster, with caching.

    Args:
        cluster: Name of the Slurm cluster.
        feature_name: Short name for the feature (used in cache key and logs).
        check_fn: A callable that takes a SlurmClient and returns True if
            the feature is available.
        cache_ttl: Time-to-live for the cache entry in seconds.
    """
    cache_key = f'slurm:{feature_name}_enabled:{cluster}'
    cached = kv_cache.get_cache_entry(cache_key)
    if cached is not None:
        logger.debug(f'Slurm {feature_name} check found in cache '
                     f'({cache_key})')
        return cached == 'true'

    ssh_config = get_slurm_ssh_config()
    ssh_config_dict = ssh_config.lookup(cluster)
    client = slurm.SlurmClient(
        ssh_config_dict['hostname'],
        int(ssh_config_dict.get('port', 22)),
        ssh_config_dict['user'],
        get_identity_file(ssh_config_dict),
        ssh_proxy_command=ssh_config_dict.get('proxycommand', None),
        ssh_proxy_jump=ssh_config_dict.get('proxyjump', None),
        identities_only=get_identities_only(ssh_config_dict),
    )
    enabled = check_fn(client)

    try:
        kv_cache.add_or_update_cache_entry(cache_key,
                                           'true' if enabled else 'false',
                                           time.time() + cache_ttl)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to cache slurm {feature_name} check for '
                     f'{cluster}: {common_utils.format_exception(e)}')

    return enabled


def check_pyxis_enabled(cluster: str) -> bool:
    """Check if the Pyxis SPANK plugin is installed on a Slurm cluster.

    Pyxis is required for Docker container support on Slurm. This function
    caches the result per cluster since the plugin availability is unlikely
    to change frequently.
    """
    return _check_cluster_feature(cluster, 'pyxis',
                                  lambda c: c.check_pyxis_enabled(),
                                  _SLURM_PYXIS_CHECK_CACHE_TTL)


def check_fuse_enabled(cluster: str) -> bool:
    """Check if FUSE is available on a Slurm cluster.

    FUSE is required for storage mounting (MOUNT/MOUNT_CACHED modes) via
    tools like goofys and rclone. This function caches the result per
    cluster since FUSE availability is unlikely to change frequently.
    """
    return _check_cluster_feature(cluster, 'fuse',
                                  lambda c: c.check_fuse_enabled(),
                                  _SLURM_FUSE_CHECK_CACHE_TTL)


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


def get_slurm_cluster_from_config(provider_config: Dict[str, Any]) -> str:
    """Return the Slurm cluster from the provider config.
    """
    slurm_cluster = provider_config.get('cluster')
    if slurm_cluster is None:
        raise ValueError('Slurm cluster not specified in provider config.')
    return slurm_cluster


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
        get_identity_file(ssh_config_dict),
        ssh_proxy_command=ssh_config_dict.get('proxycommand', None),
        ssh_proxy_jump=ssh_config_dict.get('proxyjump', None),
        identities_only=get_identities_only(ssh_config_dict),
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
        nodes = _get_slurm_nodes_info(cluster)
    except FileNotFoundError:
        return (False, f'Could not query Slurm cluster {cluster} '
                f'because the Slurm configuration file '
                f'{DEFAULT_SLURM_PATH} does not exist.')
    except Exception as e:  # pylint: disable=broad-except
        return (False, f'Could not query Slurm cluster {cluster} '
                f'because Slurm SSH configuration at {DEFAULT_SLURM_PATH} '
                f'could not be loaded: {common_utils.format_exception(e)}.')

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

        # Resolve to the exact raw GRES type that will be used at deploy
        # time, so the CPU/memory fitness check below runs against the
        # same nodes that Slurm will actually schedule on.
        try:
            resolved_type = resolve_gres_gpu_type(cluster, acc_type, acc_count,
                                                  partition)
        except exceptions.ResourcesUnavailableError as e:
            return (False, str(e))

        # Filter to nodes carrying the resolved raw type with enough GPUs.
        gpu_nodes = []
        for node_info in nodes:
            node_acc_type, node_acc_count = get_gpu_type_and_count(
                node_info.gres)
            if (node_acc_type == resolved_type and node_acc_count >= acc_count):
                gpu_nodes.append(node_info)

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


# Vendor prefixes stripped during normalization for matching purposes.
_GPU_VENDOR_PREFIXES = ('nvidia', 'amd', 'intel', 'tesla')


def _normalize_gpu_name(name: str) -> str:
    """Normalize a GPU name for fuzzy comparison.

    Strips vendor prefixes, normalizes separators, and lowercases. Used only
    for matching, never for submission.

    Examples:
        'nvidia_h100_80gb_hbm3' -> 'h100-80gb-hbm3'
        'H100'                -> 'h100'
        'A100-SXM-80GB'       -> 'a100-sxm-80gb'
    """
    result = name.lower().replace('_', '-')
    for prefix in _GPU_VENDOR_PREFIXES:
        if result.startswith(prefix + '-'):
            result = result[len(prefix) + 1:]
            break
    return result


def _is_segment_subsequence(segments_a: List[str],
                            segments_b: List[str]) -> bool:
    """Check if segments_a appears as an ordered subsequence of segments_b.

    Each segment must match exactly (preventing e.g. 'l4' matching 'l40').

    Examples:
        (['h100'], ['h100', '80gb', 's'])          -> True
        (['a100', '80gb'], ['a100', 'sxm4', '80gb']) -> True
        (['v100', '32gb'], ['v100', 'pcie', '16gb']) -> False
        (['l4'], ['l40'])                           -> False
    """
    # The iterator is stateful: once an element is consumed it won't be
    # revisited, so matches are always found in left-to-right order.
    b_iter = iter(segments_b)
    for seg in segments_a:
        # Scan forward through b_iter for a matching segment.
        for b_seg in b_iter:
            if seg == b_seg:
                break
        else:
            # for...else: runs when b_iter is exhausted without finding
            # seg, meaning segments_a is not a subsequence.
            return False
    return True


def _accelerator_name_matches_slurm(requested_acc: str,
                                    candidate_raw: str) -> bool:
    """Check if a requested accelerator name matches a Slurm GRES raw type.

    Matching rules (checked in order):
    1. Case-insensitive exact match of raw strings.
    2. Normalized forms are equal (vendor-prefix stripped, separators unified).
    3. Segment subsequence: the shorter name's dash-segments appear in order
       within the longer name's segments. Handles both prefix cases
       (H100 ~ H100-80GB-S) and non-contiguous memory variants
       (A100-80GB ~ A100-SXM4-80GB, V100-32GB ~ V100-PCIE-32GB).
       Exact segment matching prevents false positives (L4 ≠ L40).

    Args:
        requested_acc: The accelerator name requested by the user
            (e.g. 'H100', 'A100-80GB').
        candidate_raw: A raw GRES GPU type string from Slurm node metadata
            (e.g. 'NVIDIA_H100_80GB_HBM3').

    Returns:
        True if the names are considered matching.
    """
    # 1. Exact case-insensitive match.
    if requested_acc.lower() == candidate_raw.lower():
        return True

    # 2. Normalized equality.
    req_norm = _normalize_gpu_name(requested_acc)
    cand_norm = _normalize_gpu_name(candidate_raw)
    if req_norm == cand_norm:
        return True

    # 3. Segment subsequence (bidirectional): either side's segments may
    #    be a subsequence of the other (e.g. user says 'A100-80GB' and
    #    cluster has 'a100-sxm4-80gb', or vice-versa).
    req_segments = req_norm.split('-')
    cand_segments = cand_norm.split('-')
    if len(req_segments) < len(cand_segments):
        return _is_segment_subsequence(req_segments, cand_segments)
    if len(cand_segments) < len(req_segments):
        return _is_segment_subsequence(cand_segments, req_segments)
    return False


def canonicalize_raw_gpu_name(raw_name: str) -> str:
    """Convert a raw Slurm GRES GPU type to a canonical display name.

    Iterates CANONICAL_GPU_NAMES (most-specific first) and returns the
    first canonical name whose normalized form matches the raw string.
    Falls back to uppercasing.

    Matching rules (checked in order for each canonical name):
    1. Normalized equality (vendor-prefix stripped, separators unified).
    2. Segment subsequence: canonical's dash-segments appear in order within
       the raw name's segments. One-directional only (canonical into raw)
       because the list is ordered most-specific first.

    Examples:
        'nvidia_h100_80gb_hbm3'  -> 'H100-80GB'
        'nvidia_a100_sxm4_80gb'  -> 'A100-80GB'
        'nvidia_l40s'            -> 'L40S'
        'H100'                   -> 'H100'
        'unknown_custom_gpu'     -> 'UNKNOWN_CUSTOM_GPU'
    """
    raw_norm = _normalize_gpu_name(raw_name)
    raw_segments = raw_norm.split('-')

    for canonical in gpu_names.CANONICAL_GPU_NAMES:
        can_norm = _normalize_gpu_name(canonical)

        # 1. Normalized equality (also covers exact case-insensitive matches
        #    since normalization lowercases and unifies separators).
        if can_norm == raw_norm:
            return canonical

        # 2. Canonical segments are a subsequence of raw segments.
        # One-directional: only check canonical-into-raw, not the reverse,
        # because the list is ordered most-specific first (e.g. 'H100-80GB'
        # before 'H100') and a reverse match would cause 'H100' to
        # incorrectly resolve to 'H100-80GB'.
        can_segments = can_norm.split('-')
        if len(can_segments) < len(raw_segments):
            if _is_segment_subsequence(can_segments, raw_segments):
                return canonical

    return raw_name.upper()


def resolve_gres_gpu_type(
    cluster: str,
    requested_gpu_type: str,
    requested_count: int = 1,
    partition: Optional[str] = None,
) -> str:
    """Resolve a canonical GPU name to the raw GRES type on a Slurm cluster.

    Queries live node metadata and applies fuzzy matching to find the actual
    GRES GPU type string that the Slurm scheduler expects. The resolved raw
    type is used directly in ``#SBATCH --gres=gpu:<raw_type>:<count>``.

    Selection policy when multiple raw types match (deterministic):
        1. Prefer exact case-insensitive raw match.
        2. Prefer the raw type with the most supporting nodes.
        3. Tie-break lexicographically by raw type string.

    Args:
        cluster: Name of the Slurm cluster (SSH config host).
        requested_gpu_type: The GPU type requested by the user (canonical or
            raw, e.g. 'H100', 'A100-80GB', 'nvidia_h100_80gb_hbm3').
        requested_count: Minimum number of GPUs per node required.
        partition: If set, only consider nodes in this partition.

    Returns:
        The raw GRES GPU type string as it appears on the cluster.

    Raises:
        exceptions.ResourcesUnavailableError: If no matching GPU type is found.
    """
    nodes = _get_slurm_nodes_info(cluster)
    default_partition = get_cluster_default_partition(cluster)

    # Collect all GPU types from every node (for error messages) and
    # matching candidates (for selection) in a single pass.
    all_gpu_types: Dict[str, int] = {}
    candidates: Dict[str, int] = {}
    for node_info in nodes:
        if partition is not None:
            node_part = node_info.partition
            if (default_partition is not None and node_part.endswith('*') and
                    node_part[:-1] == default_partition):
                node_part = node_part[:-1]
            if node_part != partition:
                continue

        node_acc_type, node_acc_count = get_gpu_type_and_count(node_info.gres)
        if node_acc_type is None:
            continue
        all_gpu_types[node_acc_type] = all_gpu_types.get(node_acc_type, 0) + 1
        if node_acc_count < requested_count:
            continue
        if _accelerator_name_matches_slurm(requested_gpu_type, node_acc_type):
            candidates[node_acc_type] = candidates.get(node_acc_type, 0) + 1

    if not candidates:
        partition_msg = f' in partition {partition!r}' if partition else ''
        if all_gpu_types:
            discovered_msg = (f' Discovered GPU types on cluster: '
                              f'{sorted(all_gpu_types.keys())}')
        else:
            discovered_msg = ' No GPU nodes found on cluster.'
        raise exceptions.ResourcesUnavailableError(
            f'No GPU nodes matching {requested_gpu_type!r} '
            f'(count>={requested_count}) found on Slurm cluster '
            f'{cluster!r}{partition_msg}.{discovered_msg}')

    # Selection: prefer exact match, then highest node count, then
    # alphabetical.
    chosen = min(
        candidates,
        key=lambda rt: (
            # 0 (exact match) before 1 (fuzzy)
            rt.lower() != requested_gpu_type.lower(),
            # prioritize GPU type with more nodes
            -candidates[rt],
            # alphabetical tie-break
            rt,
        ))
    logger.debug(f'Resolved {requested_gpu_type!r} -> {chosen!r} '
                 f'on cluster {cluster!r} (candidates: {dict(candidates)}).')
    return chosen


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
        slurm_cluster_names = clouds.Slurm.existing_allowed_clusters()
        if not slurm_cluster_names:
            return []
        slurm_cluster_name = slurm_cluster_names[0]
    slurm_config_dict = slurm_config.lookup(slurm_cluster_name)
    logger.debug(f'Slurm config dict: {slurm_config_dict}')
    slurm_client = slurm.SlurmClient(
        slurm_config_dict['hostname'],
        int(slurm_config_dict.get('port', 22)),
        slurm_config_dict['user'],
        get_identity_file(slurm_config_dict),
        ssh_proxy_command=slurm_config_dict.get('proxycommand', None),
        ssh_proxy_jump=slurm_config_dict.get('proxyjump', None),
        identities_only=get_identities_only(slurm_config_dict),
    )
    node_infos = slurm_client.info_nodes()

    if not node_infos:
        logger.warning(
            f'`sinfo -N` returned no output on cluster {slurm_cluster_name}. '
            f'No nodes found?')
        return []

    # 2. Process each node, aggregating partitions per node
    slurm_nodes_info: Dict[str, Dict[str, Any]] = {}

    nodes_to_jobs_gres = slurm_client.get_all_jobs_gres()
    for node_info in node_infos:
        node_name = node_info.node
        state = node_info.state
        gres_str = node_info.gres
        partition = node_info.partition

        if node_name in slurm_nodes_info:
            slurm_nodes_info[node_name]['partitions'].append(partition)
            continue

        # Extract GPU info from GRES
        node_gpu_type, total_gpus = get_gpu_type_and_count(gres_str)
        if total_gpus > 0:
            if node_gpu_type is not None:
                node_gpu_type = canonicalize_raw_gpu_name(node_gpu_type)
            else:
                node_gpu_type = 'GPU'

        # Get allocated GPUs
        allocated_gpus = 0
        # TODO(zhwu): move to enum
        if state in ('alloc', 'mix', 'drain', 'drng', 'drained', 'resv',
                     'comp'):
            jobs_gres = nodes_to_jobs_gres.get(node_name, [])
            if jobs_gres:
                for job_line in jobs_gres:
                    _, job_gpu_count = get_gpu_type_and_count(job_line)
                    allocated_gpus += job_gpu_count
            elif state == 'alloc':
                # If no GRES info found but node is fully allocated,
                # assume all GPUs are in use.
                allocated_gpus = total_gpus
        elif state == 'idle':
            allocated_gpus = 0

        free_gpus = total_gpus - allocated_gpus if state not in ('down',
                                                                 'drain',
                                                                 'drng',
                                                                 'maint') else 0
        free_gpus = max(0, free_gpus)

        slurm_nodes_info[node_name] = {
            'node_name': node_name,
            'slurm_cluster_name': slurm_cluster_name,
            'partitions': [partition],
            'node_state': state,
            'gpu_type': node_gpu_type,
            'total_gpus': total_gpus,
            'free_gpus': free_gpus,
            'vcpu_count': node_info.cpus,
            'memory_gb': round(node_info.memory_gb, 2),
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
    except (FileNotFoundError, RuntimeError, exceptions.NotSupportedError) as e:
        logger.debug(f'Could not retrieve Slurm node info: {e}')
        return []
    return node_list


def is_inside_slurm_cluster() -> bool:
    # Check for the marker file in the current home directory. When run by
    # the skylet on a compute node, the HOME environment variable is set to
    # the cluster's sky home directory by the SlurmCommandRunner.
    marker_file = os.path.join(os.path.expanduser('~'), SLURM_MARKER_FILE)
    return os.path.exists(marker_file)


def get_partitions(cluster_name: str) -> List[str]:
    """Get unique partition names available in a Slurm cluster.

    Args:
        cluster_name: Name of the Slurm cluster.

    Returns:
        List of unique partition names available in the cluster.
        The default partition appears first,
        and the rest are sorted alphabetically.
    """
    partitions_info = get_partition_infos(cluster_name)
    default_partitions = []
    other_partitions = []
    for partition in partitions_info.values():
        if partition.is_default:
            default_partitions.append(partition.name)
        else:
            other_partitions.append(partition.name)
    return default_partitions + sorted(other_partitions)


def get_partition_info(cluster_name: str,
                       partition_name: str) -> Optional[slurm.SlurmPartition]:
    return get_partition_infos(cluster_name=cluster_name).get(partition_name)


# Cache the partitions for 1 hour, we do not expect the
# partitions to change frequently.
@annotations.ttl_cache(scope='global', timer=time.time, maxsize=10, ttl=60 * 60)
def get_partition_infos(cluster_name: str) -> Dict[str, slurm.SlurmPartition]:
    """Get the partition information for a Slurm cluster.

    Args:
        cluster_name: Name of the Slurm cluster.

    Returns:
        List of partition information.
    """
    try:
        slurm_config = SSHConfig.from_path(
            os.path.expanduser(DEFAULT_SLURM_PATH))
        slurm_config_dict = slurm_config.lookup(cluster_name)

        client = slurm.SlurmClient(
            slurm_config_dict['hostname'],
            int(slurm_config_dict.get('port', 22)),
            slurm_config_dict['user'],
            get_identity_file(slurm_config_dict),
            ssh_proxy_command=slurm_config_dict.get('proxycommand', None),
            ssh_proxy_jump=slurm_config_dict.get('proxyjump', None),
            identities_only=get_identities_only(slurm_config_dict),
        )

        partitions_info = client.get_partitions_info()
    except Exception as e:  # pylint: disable=broad-except
        raise ValueError(
            f'Failed to get partitions for cluster '
            f'{cluster_name}: {common_utils.format_exception(e)}') from e

    return {partition.name: partition for partition in partitions_info}


def format_slurm_duration(duration_seconds: Optional[int]) -> str:
    """Format the duration in seconds into a Slurm duration string.
    Slurm duration string is in the format of [days-]hours:minutes:seconds.

    if duration_seconds is None, return 'UNLIMITED'.

    Example:
        format_slurm_duration(10000) -> 0-02:46:40
        format_slurm_duration(100000) -> 1-03:46:40
        format_slurm_duration(1000000) -> 11-13:46:40
        format_slurm_duration(None) -> 'UNLIMITED'

    Args:
        duration_seconds: The duration in seconds.

    Returns:
        The duration in a Slurm duration string.
    """
    if duration_seconds is None:
        return 'UNLIMITED'
    days = duration_seconds // (24 * 3600)
    hours = (duration_seconds % (24 * 3600)) // 3600
    minutes = (duration_seconds % 3600) // 60
    seconds = duration_seconds % 60
    return f'{days}-{hours:02}:{minutes:02}:{seconds:02}'


def srun_sshd_command(
    job_id: str,
    target_node: str,
    unix_user: str,
    cluster_name_on_cloud: str,
    is_container_image: bool,
) -> str:
    """Build srun command for launching sshd -i inside a Slurm job.

    This is used by the API server to proxy SSH connections to Slurm jobs
    via sshd running in inetd mode within srun.

    Args:
        job_id: The Slurm job ID
        target_node: The target compute node hostname
        unix_user: The Unix user for the job
        cluster_name_on_cloud: SkyPilot cluster name on Slurm side.
        is_container_image: Whether the cluster is on containers.

    Returns:
        List of command arguments to be extended to ssh base command
    """
    # We use ~username to ensure we use the real home of the user ssh'ing in,
    # because we override the home directory in SlurmCommandRunner.run.
    user_home_ssh_dir = f'~{unix_user}/.ssh'

    # TODO(kevin): SSH sessions don't inherit Slurm env vars (SLURM_*, CUDA_*,
    # etc.) because sshd/dropbear spawns a fresh shell. Fix by capturing env
    # to a file and sourcing it.

    if is_container_image:
        # Dropbear + socat bridge for container mode.
        # See slurm-ray.yml.j2 for why we use Dropbear instead of OpenSSH.
        # Dropbear's -i (inetd) mode expects a socket fd on stdin, but srun
        # provides pipes. socat bridges stdin/stdout to a TCP socket.
        ssh_bootstrap_cmd = (
            # Find dropbear in PATH
            'DROPBEAR=$(command -v dropbear); '
            'if [ -z "$DROPBEAR" ]; then '
            'echo "dropbear not found" >&2; exit 1; fi; '
            # Find a free port in the ephemeral range
            'while :; do '
            'PORT=$((30000 + RANDOM % 30000)); '
            'ss -tln | awk \'{print $4}\' | grep -q ":$PORT$" || break; '
            'done; '
            # Start dropbear and wait for it to bind
            '"$DROPBEAR" -F -s -R -p "127.0.0.1:$PORT" & '
            'DROPBEAR_PID=$!; '
            'trap "kill $DROPBEAR_PID 2>/dev/null" EXIT; '
            'for i in $(seq 1 50); do '
            'ss -tlnp 2>/dev/null | grep -q ":$PORT.*pid=$DROPBEAR_PID" '
            '&& break; sleep 0.1; done; '
            'if ! ss -tlnp 2>/dev/null | '
            'grep -q ":$PORT.*pid=$DROPBEAR_PID"; then '
            'echo "Error: Timed out waiting for dropbear to start." >&2; '
            'exit 1; fi; '
            'socat STDIO TCP:127.0.0.1:$PORT')
        return shlex.join([
            'srun',
            '--overlap',
            '--quiet',
            '--unbuffered',
            '--jobid',
            job_id,
            '--nodes=1',
            '--ntasks=1',
            '--ntasks-per-node=1',
            '-w',
            target_node,
            '--container-remap-root',
            f'--container-name='
            f'{pyxis_container_name(cluster_name_on_cloud)}:exec',
            '/bin/bash',
            '-c',
            ssh_bootstrap_cmd,
        ])

    # Non-container: OpenSSH sshd
    return shlex.join([
        'srun',
        '--quiet',
        '--unbuffered',
        '--overlap',
        '--jobid',
        job_id,
        '-w',
        target_node,
        '/usr/sbin/sshd',
        '-i',  # Uses stdin/stdout
        '-e',  # Writes errors to stderr
        '-f',  # Use /dev/null to avoid reading system sshd_config
        '/dev/null',
        '-h',
        f'{user_home_ssh_dir}/{SLURM_SSHD_HOST_KEY_FILENAME}',
        '-o',
        f'AuthorizedKeysFile={user_home_ssh_dir}/authorized_keys',
        '-o',
        'PasswordAuthentication=no',
        '-o',
        'PubkeyAuthentication=yes',
        # If UsePAM is enabled, we will not be able to run sshd(8)
        # as a non-root user.
        # See https://man7.org/linux/man-pages/man5/sshd_config.5.html
        '-o',
        'UsePAM=no',
        '-o',
        f'AcceptEnv={constants.SKY_CLUSTER_NAME_ENV_VAR_KEY}',
    ])
