"""PBS Pro / OpenPBS utilities for SkyPilot."""
import json
import math
import os
import re
import subprocess
import time
from typing import (Any, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple,
                    Union)

from paramiko.config import SSHConfig

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import pbs
from sky.skylet import runtime_utils
from sky.utils import annotations
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import gpu_names
from sky.utils import subprocess_utils
from sky.utils.db import kv_cache

logger = sky_logging.init_logger(__name__)

DEFAULT_PBS_PATH = '~/.pbs/config'

_VAR_PATTERN = re.compile(r'\$(\w+|\{[^}]*\})')

# Written to <runtime_dir>/.sky/ on every allocated node; holds the PBS job
# id so that code running on the node (e.g. autodown) can find its own job.
PBS_MARKER_FILE = '.sky_pbs_cluster'

# Per-cluster directory on the shared filesystem holding the keys and port
# files of the user-level sshd that the allocation runs on every node.
SSHD_DIRECTORY_NAME = '.sky_sshd'
SSHD_HOST_KEY_FILENAME = 'host_key'
# Key pair the head node uses to reach the workers of its own allocation.
CLUSTER_KEY_FILENAME = 'cluster_key'
AUTHORIZED_KEYS_FILENAME = 'authorized_keys'
NODE_AGENT_SCRIPT_FILENAME = 'node_agent.sh'
# Node rank -> "<hostname> <sshd port> <runtime dir>", written by the node
# once sshd listens; the provisioner treats a complete set as the ready
# signal.
NODE_PORT_FILE_PREFIX = 'rank'
NODE_PORT_FILE_SUFFIX = '.port'
# Ordered execution hosts of the allocation (first is the head node).
NODE_LIST_FILENAME = '.sky_nodes'

# The PBS custom resource counting GPUs; `ngpus` is the name the PBS cgroup
# hook and most sites use.
DEFAULT_GPU_RESOURCE = 'ngpus'
# Node-level string resources sites commonly use to declare the GPU model.
DEFAULT_GPU_TYPE_RESOURCES = ('gpu_type', 'gputype', 'gpu_model',
                              'accelerator_model', 'accelerator_type')

# Inventory caches: nodes are unlikely to change often; FUSE never does.
_PBS_NODES_INFO_CACHE_TTL = 30 * 60
_PBS_FUSE_CHECK_CACHE_TTL = 24 * 60 * 60

# qsub -l walltime accepts [[hours:]minutes:]seconds.
_WALLTIME_PATTERN = re.compile(r'^\d+(?::\d{1,2}){0,2}$')


class PbsClusterInventory(NamedTuple):
    """Cluster-wide node and queue inventory."""
    nodes: List[Dict[str, Any]]
    default_queue: Optional[str]


def expand_path_vars(path: str, env: Dict[str, str]) -> str:
    """Expand $VAR and ${VAR} in path using the given environment dict.

    Only $name and ${name} forms are expanded; unknown variables are left
    unchanged.
    """

    def _repl(m: re.Match) -> str:
        name = m.group(1)
        if name.startswith('{') and name.endswith('}'):
            name = name[1:-1]
        return env.get(name, m.group(0))

    return _VAR_PATTERN.sub(_repl, path)


def get_pbs_ssh_config() -> SSHConfig:
    """Load the SSH-config-style file listing the PBS clusters."""
    return SSHConfig.from_path(os.path.expanduser(DEFAULT_PBS_PATH))


def get_identity_file(ssh_config_dict: Dict[str, Any]) -> Optional[str]:
    """First identity file from an SSH config host entry, if any."""
    identity_files = ssh_config_dict.get('identityfile')
    if identity_files:
        return identity_files[0]
    return None


def get_identities_only(ssh_config_dict: Dict[str, Any]) -> bool:
    """Whether the host entry sets IdentitiesOnly yes."""
    return ssh_config_dict.get('identitiesonly', '').lower() == 'yes'


def make_client(cluster_name: str) -> 'pbs.PbsClient':
    """A PbsClient for the login node of a cluster in ~/.pbs/config."""
    ssh_config_dict = get_pbs_ssh_config().lookup(cluster_name)
    return make_client_from_ssh_config(ssh_config_dict)


def get_ssh_control_path(ssh_config_dict: Dict[str, Any]) -> Optional[str]:
    """The ControlPath of a host entry, expanded, or None.

    Login nodes are often password-only. A user opens a master connection
    by hand (``ssh -F ~/.pbs/config -fN <cluster>``) and SkyPilot reuses it
    through this path instead of prompting.
    """
    control_path = ssh_config_dict.get('controlpath')
    if not control_path:
        return None
    return os.path.expanduser(control_path)


def make_client_from_ssh_config(
        ssh_config_dict: Dict[str, Any]) -> 'pbs.PbsClient':
    return pbs.PbsClient(
        ssh_config_dict['hostname'],
        int(ssh_config_dict.get('port', 22)),
        ssh_config_dict['user'],
        get_identity_file(ssh_config_dict),
        ssh_proxy_command=ssh_config_dict.get('proxycommand', None),
        ssh_proxy_jump=ssh_config_dict.get('proxyjump', None),
        identities_only=get_identities_only(ssh_config_dict),
        ssh_control_path=get_ssh_control_path(ssh_config_dict),
    )


def make_login_node_runner(
        ssh_config: Dict[str, Any]) -> command_runner.SSHCommandRunner:
    """An SSH runner for a login node, from a ``provider.ssh`` config."""
    identities_only = ssh_config.get('identities_only', False)
    return command_runner.SSHCommandRunner(
        (ssh_config['hostname'], int(ssh_config['port'])),
        ssh_config['user'],
        ssh_config.get('private_key'),
        ssh_proxy_command=ssh_config.get('proxycommand'),
        ssh_proxy_jump=ssh_config.get('proxyjump'),
        enable_interactive_auth=True,
        disable_identities_only=not identities_only,
        ssh_control_path=ssh_config.get('control_path'),
    )


def get_all_pbs_cluster_names() -> List[str]:
    """Host aliases in ~/.pbs/config; empty if the file does not exist."""
    try:
        ssh_config = get_pbs_ssh_config()
    except FileNotFoundError:
        return []
    except Exception as e:
        raise ValueError(
            f'Failed to load SSH configuration from {DEFAULT_PBS_PATH}: '
            f'{common_utils.format_exception(e)}') from e
    return [host for host in ssh_config.get_hostnames() if host != '*']


def resolve_sky_base_dir(cluster: str, client: 'pbs.PbsClient') -> str:
    """Absolute shared directory holding the SkyPilot state of a cluster."""
    workdir = skypilot_config.get_effective_region_config(cloud='pbs',
                                                          region=cluster,
                                                          keys=('workdir',),
                                                          default_value=None)
    if workdir is not None:
        workdir = expand_path_vars(workdir, client.get_env())
        if not os.path.isabs(workdir):
            raise RuntimeError('Resolved PBS workdir must be absolute, got '
                               f'{workdir!r}.')
        return workdir
    remote_home_dir = client.get_remote_home_dir()
    if not os.path.isabs(remote_home_dir):
        raise RuntimeError('PBS remote home directory must be absolute, got '
                           f'{remote_home_dir!r}.')
    return remote_home_dir


def get_gpu_resource(cluster: str) -> str:
    """The PBS resource name that counts GPUs on a cluster."""
    return skypilot_config.get_effective_region_config(
        cloud='pbs',
        region=cluster,
        keys=('gpu_resource',),
        default_value=DEFAULT_GPU_RESOURCE)


def get_gpu_type_resources(cluster: str) -> Tuple[str, ...]:
    """The node resources, in priority order, that may name the GPU type."""
    configured = skypilot_config.get_effective_region_config(
        cloud='pbs',
        region=cluster,
        keys=('gpu_type_resource',),
        default_value=None)
    if configured:
        return (configured,)
    return DEFAULT_GPU_TYPE_RESOURCES


@annotations.lru_cache(scope='request')
def get_pbs_nodes_info(cluster: str) -> List[pbs.NodeInfo]:
    """Execution hosts of a cluster, cached for a short while."""
    gpu_resource = get_gpu_resource(cluster)
    gpu_type_resources = get_gpu_type_resources(cluster)
    # The fields are part of the key so that a cached entry written by an
    # older NodeInfo is not reused.
    cache_key = (f'pbs:nodes_info:{cluster}:{gpu_resource}:'
                 f'{",".join(gpu_type_resources)}:'
                 f'{",".join(pbs.NodeInfo._fields)}')
    cached = kv_cache.get_cache_entry(cache_key)
    if cached is not None:
        logger.debug(f'PBS nodes info found in cache ({cache_key})')
        return [pbs.NodeInfo(**item) for item in json.loads(cached)]

    nodes_info = make_client(cluster).info_nodes(gpu_resource,
                                                 gpu_type_resources)
    try:
        kv_cache.add_or_update_cache_entry(
            cache_key, json.dumps([n._asdict() for n in nodes_info]),
            time.time() + _PBS_NODES_INFO_CACHE_TTL)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to cache PBS nodes info for {cluster}: '
                     f'{common_utils.format_exception(e)}')
    return nodes_info


def check_fuse_enabled(cluster: str) -> bool:
    """Whether /dev/fuse exists on the cluster's login node (cached)."""
    cache_key = f'pbs:fuse_enabled:{cluster}'
    cached = kv_cache.get_cache_entry(cache_key)
    if cached is not None:
        return cached == 'true'
    enabled = make_client(cluster).check_fuse_enabled()
    try:
        kv_cache.add_or_update_cache_entry(
            cache_key, 'true' if enabled else 'false',
            time.time() + _PBS_FUSE_CHECK_CACHE_TTL)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to cache PBS FUSE check for {cluster}: '
                     f'{common_utils.format_exception(e)}')
    return enabled


class PbsInstanceType:
    """The virtual "instance type" of a PBS allocation chunk.

    PBS has no instances; the name encodes the per-node resource request:
    "{n}CPU--{k}GB" plus an optional "--{type}:{a}" accelerator suffix.
    CPU and memory may be floats; the accelerator count is an int.

    Examples:
        - 4CPU--16GB
        - 0.5CPU--1.5GB
        - 4CPU--16GB--V100:1
    """

    _PATTERN = re.compile(
        r'^(?P<cpus>\d+(\.\d+)?)CPU--(?P<memory>\d+(\.\d+)?)GB'
        r'(?:--(?P<accelerator_type>[\w\d-]+):(?P<accelerator_count>\d+))?$')

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
        name = (f'{common_utils.format_float(self.cpus)}CPU--'
                f'{common_utils.format_float(self.memory)}GB')
        if self.accelerator_count is not None:
            assert self.accelerator_type is not None, self.accelerator_count
            acc_name = self.accelerator_type.replace(' ', '_')
            name += f'--{acc_name}:{self.accelerator_count}'
        return name

    @classmethod
    def is_valid_instance_type(cls, name: str) -> bool:
        return cls._PATTERN.match(name) is not None

    @classmethod
    def from_instance_type(cls, name: str) -> 'PbsInstanceType':
        match = cls._PATTERN.match(name)
        if match is None:
            raise ValueError(f'Invalid instance name: {name}')
        accelerator_count = match.group('accelerator_count')
        accelerator_type = match.group('accelerator_type')
        if accelerator_count is not None:
            return cls(cpus=float(match.group('cpus')),
                       memory=float(match.group('memory')),
                       accelerator_count=int(accelerator_count),
                       accelerator_type=str(accelerator_type))
        return cls(cpus=float(match.group('cpus')),
                   memory=float(match.group('memory')))

    @classmethod
    def from_resources(cls,
                       cpus: float,
                       memory: float,
                       accelerator_count: Union[float, int] = 0,
                       accelerator_type: str = '') -> 'PbsInstanceType':
        """Build from resources; a fractional GPU count is rounded up."""
        accelerator_count = math.ceil(accelerator_count)
        if accelerator_count > 0:
            return cls(cpus=cpus,
                       memory=memory,
                       accelerator_count=accelerator_count,
                       accelerator_type=accelerator_type)
        return cls(cpus=cpus, memory=memory)

    def __str__(self):
        return self.name

    def __repr__(self):
        return (f'PbsInstanceType(cpus={self.cpus!r}, '
                f'memory={self.memory!r}, '
                f'accelerator_count={self.accelerator_count!r}, '
                f'accelerator_type={self.accelerator_type!r})')


def instance_id(job_id: str, node: str) -> str:
    """SkyPilot instance id of one node of a PBS job; unique per cluster."""
    return f'job{job_id}-{node}'


def get_pbs_cluster_from_config(provider_config: Dict[str, Any]) -> str:
    pbs_cluster = provider_config.get('cluster')
    if pbs_cluster is None:
        raise ValueError('PBS cluster not specified in provider config.')
    return pbs_cluster


def get_queue_from_config(provider_config: Dict[str, Any]) -> str:
    """The queue (SkyPilot zone) from the provider config."""
    queue = provider_config.get('queue')
    if queue is None:
        raise ValueError('Queue not specified in provider config.')
    return queue


# Queues rarely change; cache them for an hour.
@annotations.ttl_cache(scope='global', timer=time.time, maxsize=128, ttl=3600)
def get_queue_infos(cluster_name: str) -> Dict[str, pbs.PbsQueue]:
    """Execution queues of a cluster, by name."""
    try:
        queues = make_client(cluster_name).get_queues_info()
    except Exception as e:  # pylint: disable=broad-except
        raise ValueError(f'Failed to get queues for cluster {cluster_name}: '
                         f'{common_utils.format_exception(e)}') from e
    return {queue.name: queue for queue in queues}


def get_queues(cluster_name: str) -> List[str]:
    """Enabled, started queues; the default queue comes first.

    Routing queues are included: on many sites they are the only queue a
    user may submit to, and PBS picks the execution queue from the
    resources. Queues that only take jobs from a routing queue are left
    out, since qsub rejects them.
    """
    default = []
    others = []
    for queue in get_queue_infos(cluster_name).values():
        if not (queue.enabled and queue.started):
            continue
        if queue.from_route_only:
            continue
        if queue.is_default:
            default.append(queue.name)
        else:
            others.append(queue.name)
    return default + sorted(others)


def get_queue_info(cluster_name: str,
                   queue_name: str) -> Optional[pbs.PbsQueue]:
    return get_queue_infos(cluster_name).get(queue_name)


def get_cluster_default_queue(cluster_name: str) -> Optional[str]:
    for queue in get_queue_infos(cluster_name).values():
        if queue.is_default:
            return queue.name
    return None


def lookup_gpu_queue_map(cluster: str, acc_type: str) -> Optional[List[str]]:
    """Queues configured for a GPU type under ``gpu_queue_map``.

    Global and per-cluster maps are merged (per-cluster wins); the lookup
    is case-insensitive. None when the type is not mapped.
    """
    gpu_queue_map = skypilot_config.get_effective_region_config(
        cloud='pbs', keys=('gpu_queue_map',), region=cluster, merge_dicts=True)
    if gpu_queue_map is None:
        return None
    acc_type_lower = acc_type.lower()
    for map_gpu, map_queues in gpu_queue_map.items():
        if map_gpu.lower() == acc_type_lower:
            if isinstance(map_queues, str):
                return [map_queues]
            return list(map_queues)
    return None


def lookup_cpu_queue(cluster: str) -> Optional[str]:
    """The queue configured for CPU-only tasks, if any."""
    return skypilot_config.get_effective_region_config(cloud='pbs',
                                                       keys=('cpu_queue',),
                                                       region=cluster,
                                                       default_value=None)


def nodes_for_queue(nodes: Sequence[pbs.NodeInfo],
                    queue: Optional[str],
                    is_routing_queue: bool = False,
                    qlists: Optional[Set[str]] = None) -> List[pbs.NodeInfo]:
    """Nodes that can run jobs of a queue.

    A node with a ``queue`` attribute only serves that queue; the others
    serve every queue. A routing queue forwards to any execution queue, so
    every node counts for it. When ``qlists`` is given, the node must also
    carry one of those Qlist tags (see ``_queue_qlists``).
    """
    if queue is None:
        return list(nodes)
    result = []
    for node in nodes:
        if (not is_routing_queue and node.queue is not None and
                node.queue != queue):
            continue
        if qlists is not None:
            node_tags = {
                tag.strip() for tag in (node.qlist or '').split(',') if tag
            }
            if not node_tags & qlists:
                continue
        result.append(node)
    return result


def _is_routing_queue(cluster: str, queue: Optional[str]) -> bool:
    if queue is None:
        return False
    try:
        info = get_queue_info(cluster, queue)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to get queue info for {cluster}/{queue}: {e}')
        return False
    return info is not None and info.queue_type.lower() == 'route'


def _queue_qlists(cluster: str, queue: Optional[str]) -> Optional[Set[str]]:
    """Qlist tags the jobs of a queue may land on; None when unrestricted.

    Sites that tie nodes to queues with a Qlist resource set
    ``default_chunk.Qlist`` on each execution queue and
    ``resources_available.Qlist`` on the nodes. A routing queue may land on
    the nodes of any of its destinations.
    """
    if queue is None:
        return None
    try:
        infos = get_queue_infos(cluster)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to get queue info for {cluster}/{queue}: {e}')
        return None
    info = infos.get(queue)
    if info is None:
        return None
    if info.queue_type.lower() != 'route':
        return {info.qlist} if info.qlist else None
    tags: Set[str] = set()
    for destination in info.route_destinations:
        dest_info = infos.get(destination)
        if dest_info is None or dest_info.qlist is None:
            # An unrestricted destination: any node may do.
            return None
        tags.add(dest_info.qlist)
    return tags or None


def _check_cpu_mem_fits(
        candidate_instance_type: PbsInstanceType,
        node_list: Sequence[pbs.NodeInfo]) -> Tuple[bool, Optional[str]]:
    """Whether some node has enough CPUs and memory in total.

    Capacity, not availability: the PBS scheduler handles contention.
    """
    max_cpu = 0
    max_mem_gb = 0.0
    for node_info in node_list:
        if node_info.cpus > max_cpu:
            max_cpu = node_info.cpus
            max_mem_gb = node_info.memory_gb
        if (node_info.cpus >= candidate_instance_type.cpus and
                node_info.memory_gb >= candidate_instance_type.memory):
            return True, None
    return False, (f'Max found: {max_cpu} CPUs, '
                   f'{common_utils.format_float(max_mem_gb)}G memory')


def resolve_gpu_type(
    cluster: str,
    requested_gpu_type: str,
    requested_count: int = 1,
    queue: Optional[str] = None,
) -> Optional[str]:
    """Resolve a canonical GPU name to the raw type the nodes declare.

    Returns the raw value to put in the select chunk, or None when the
    cluster declares no GPU type resource at all (GPUs are then requested
    by count only).

    Raises:
        exceptions.ResourcesUnavailableError: If the nodes declare GPU types
            but none matches the request.
    """
    nodes = nodes_for_queue(get_pbs_nodes_info(cluster), queue,
                            _is_routing_queue(cluster, queue),
                            _queue_qlists(cluster, queue))
    all_gpu_types: Dict[str, int] = {}
    candidates: Dict[str, int] = {}
    for node_info in nodes:
        if node_info.gpus <= 0 or node_info.gpu_type is None:
            continue
        raw_type = node_info.gpu_type
        all_gpu_types[raw_type] = all_gpu_types.get(raw_type, 0) + 1
        if node_info.gpus < requested_count:
            continue
        if gpu_names.accelerator_name_matches(requested_gpu_type, raw_type):
            candidates[raw_type] = candidates.get(raw_type, 0) + 1
    if not all_gpu_types:
        # Untyped GPUs: nothing to resolve against.
        return None
    if not candidates:
        queue_msg = f' in queue {queue!r}' if queue else ''
        raise exceptions.ResourcesUnavailableError(
            f'No GPU nodes matching {requested_gpu_type!r} '
            f'(count>={requested_count}) found on PBS cluster '
            f'{cluster!r}{queue_msg}. Discovered GPU types on cluster: '
            f'{sorted(all_gpu_types.keys())}')
    chosen = min(candidates,
                 key=lambda rt: (
                     rt.lower() != requested_gpu_type.lower(),
                     -candidates[rt],
                     rt,
                 ))
    logger.debug(f'Resolved {requested_gpu_type!r} -> {chosen!r} on PBS '
                 f'cluster {cluster!r} (candidates: {dict(candidates)}).')
    return chosen


def check_instance_fits(
        cluster: str,
        instance_type: str,
        queue: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Whether some node of the cluster (and queue) can host the instance."""
    try:
        nodes = get_pbs_nodes_info(cluster)
    except FileNotFoundError:
        return (False, f'Could not query PBS cluster {cluster} because the '
                f'PBS configuration file {DEFAULT_PBS_PATH} does not exist.')
    except Exception as e:  # pylint: disable=broad-except
        return (False, f'Could not query PBS cluster {cluster}: '
                f'{common_utils.format_exception(e)}.')
    nodes = nodes_for_queue(nodes, queue, _is_routing_queue(cluster, queue),
                            _queue_qlists(cluster, queue))
    queue_suffix = f' in queue {queue}' if queue is not None else ''
    if not nodes:
        return False, (f'No nodes serve{queue_suffix} on PBS cluster '
                       f'{cluster!r}.')

    pbs_instance_type = PbsInstanceType.from_instance_type(instance_type)
    acc_count = pbs_instance_type.accelerator_count or 0
    acc_type = pbs_instance_type.accelerator_type
    req_str = (f'CPU (>= {pbs_instance_type.cpus} CPUs) and/or memory '
               f'(>= {pbs_instance_type.memory} G)')
    candidate_nodes = list(nodes)
    not_fit_reason_prefix = (
        f'No nodes found with enough {req_str}{queue_suffix}. ')
    if acc_type is not None:
        if lookup_gpu_queue_map(cluster, acc_type) is not None:
            # Mapped types are requested by count only.
            resolved_type = None
        else:
            try:
                resolved_type = resolve_gpu_type(cluster, acc_type, acc_count,
                                                 queue)
            except exceptions.ResourcesUnavailableError as e:
                return False, str(e)
        candidate_nodes = [
            node for node in nodes if node.gpus >= acc_count and
            (resolved_type is None or node.gpu_type == resolved_type)
        ]
        if not candidate_nodes:
            return False, (f'No nodes with {acc_count}x {acc_type}'
                           f'{queue_suffix} on PBS cluster {cluster!r}.')
        not_fit_reason_prefix = (f'GPU nodes with {acc_type}{queue_suffix} do '
                                 f'not have enough {req_str}. ')
    fits, reason = _check_cpu_mem_fits(pbs_instance_type, candidate_nodes)
    if not fits and reason is not None:
        reason = not_fit_reason_prefix + reason
    return fits, reason


def _build_pbs_node_info_list(
        pbs_cluster_name: str,
        nodes: Sequence[pbs.NodeInfo]) -> List[Dict[str, Any]]:
    """Node inventory dicts, in the shape the catalog and CLI expect."""
    if not nodes:
        logger.warning(
            f'pbsnodes returned no nodes on cluster {pbs_cluster_name}.')
        return []
    node_infos = []
    for node in nodes:
        gpu_type = None
        if node.gpus > 0:
            gpu_type = (gpu_names.canonicalize_raw_gpu_name(node.gpu_type)
                        if node.gpu_type is not None else 'GPU')
        available = pbs.is_node_available(node.state)
        free_gpus = max(0, node.gpus - node.assigned_gpus) if available else 0
        free_vcpus = (max(0, node.cpus -
                          node.assigned_cpus) if available else 0)
        node_infos.append({
            'node_name': node.node,
            'pbs_cluster_name': pbs_cluster_name,
            'queue': node.queue or '',
            'node_state': node.state,
            'gpu_type': gpu_type,
            'total_gpus': node.gpus,
            'free_gpus': free_gpus,
            'vcpu_count': node.cpus,
            'memory_gb': round(node.memory_gb, 2),
            'free_vcpus': free_vcpus,
        })
    return node_infos


def get_pbs_cluster_inventory(pbs_cluster_name: str) -> PbsClusterInventory:
    """Nodes and the default queue of a cluster, in one SSH call."""
    client = make_client(pbs_cluster_name)
    snapshot = client.get_inventory_snapshot(
        get_gpu_resource(pbs_cluster_name),
        get_gpu_type_resources(pbs_cluster_name))
    default_queue = None
    if snapshot.queues is not None:
        default_queue = next(
            (queue.name for queue in snapshot.queues if queue.is_default), None)
    return PbsClusterInventory(nodes=_build_pbs_node_info_list(
        pbs_cluster_name, snapshot.nodes),
                               default_queue=default_queue)


def pbs_cluster_names() -> List[str]:
    """Configured and allowed PBS clusters; no login node is contacted."""
    return clouds.PBS.existing_allowed_clusters()


def pbs_node_info(
        pbs_cluster_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Node inventory of one cluster, or of every allowed cluster.

    Raises:
        exceptions.CommandError: If a single cluster is requested and
            querying it fails. Aggregating over clusters never raises, so an
            unreachable cluster does not hide the others.
    """
    if pbs_cluster_name is not None:
        try:
            return _build_pbs_node_info_list(
                pbs_cluster_name, get_pbs_nodes_info(pbs_cluster_name))
        except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired,
                exceptions.NotSupportedError) as e:
            logger.debug(f'Could not retrieve PBS node info: {e}')
            return []

    clusters_to_query = clouds.PBS.existing_allowed_clusters()
    if not clusters_to_query:
        return []

    def _query_cluster(cluster_name: str) -> List[Dict[str, Any]]:
        try:
            return _build_pbs_node_info_list(cluster_name,
                                             get_pbs_nodes_info(cluster_name))
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'Skipping PBS cluster {cluster_name!r} while collecting '
                f'node info: {common_utils.format_exception(e)}')
            return []

    node_info_lists = subprocess_utils.run_in_parallel(_query_cluster,
                                                       clusters_to_query)
    return [
        node_info for node_info_list in node_info_lists
        for node_info in node_info_list
    ]


def is_inside_pbs_cluster() -> bool:
    """Whether this process runs on a node of a SkyPilot PBS allocation."""
    return os.path.exists(
        runtime_utils.get_runtime_dir_path(os.path.join('.sky',
                                                        PBS_MARKER_FILE)))


def read_inside_cluster_job_id() -> Optional[str]:
    """The PBS job id recorded in the runtime marker, if any."""
    try:
        with open(runtime_utils.get_runtime_dir_path(
                os.path.join('.sky', PBS_MARKER_FILE)),
                  'r',
                  encoding='utf-8') as f:
            job_id = f.read().strip()
    except OSError:
        return None
    return job_id or None


def validate_walltime(value: str) -> None:
    """Reject a malformed qsub walltime before submission.

    Raises:
        ValueError: If the value is not [[hours:]minutes:]seconds.
    """
    if not _WALLTIME_PATTERN.fullmatch(value):
        raise ValueError(
            f'Invalid pbs.qsub_options.walltime {value!r}. Accepted formats: '
            'seconds, minutes:seconds, hours:minutes:seconds.')


def sky_cluster_home_dir(base_dir: str, cluster_name_on_cloud: str) -> str:
    """Shared home directory of a SkyPilot cluster on the PBS cluster."""
    return f'{base_dir}/.sky_clusters/{cluster_name_on_cloud}'


def sshd_dir(cluster_home_dir: str) -> str:
    return f'{cluster_home_dir}/{SSHD_DIRECTORY_NAME}'


def node_port_file(cluster_home_dir: str, rank: int) -> str:
    return (f'{sshd_dir(cluster_home_dir)}/{NODE_PORT_FILE_PREFIX}{rank}'
            f'{NODE_PORT_FILE_SUFFIX}')


class NodeEndpoint(NamedTuple):
    """How to reach one node of an allocation."""
    hostname: str
    port: int
    # The node-local SkyPilot runtime directory the node agent chose.
    runtime_dir: str


def parse_node_port_files(output: str) -> Dict[int, NodeEndpoint]:
    """Parse "rank<i>.port <hostname> <port> <runtime dir>" lines by rank.

    Malformed or partially written lines are skipped: a node writes its
    file atomically, but the listing may race a node that is still coming
    up.
    """
    endpoints: Dict[int, NodeEndpoint] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        name, host, port, runtime_dir = parts
        if not (name.startswith(NODE_PORT_FILE_PREFIX) and
                name.endswith(NODE_PORT_FILE_SUFFIX)):
            continue
        rank_str = name[len(NODE_PORT_FILE_PREFIX):-len(NODE_PORT_FILE_SUFFIX)]
        try:
            endpoints[int(rank_str)] = NodeEndpoint(host, int(port),
                                                    runtime_dir)
        except ValueError:
            continue
    return endpoints


def list_node_port_files_command(cluster_home_dir: str) -> str:
    """Shell command printing one "rank<i>.port <hostname> <port>" per node."""
    directory = sshd_dir(cluster_home_dir)
    return (f'for f in {directory}/{NODE_PORT_FILE_PREFIX}*'
            f'{NODE_PORT_FILE_SUFFIX}; do '
            f'[ -f "$f" ] && echo "$(basename "$f") $(cat "$f")"; '
            f'done; true')


def build_login_node_proxy_command(ssh_config: Dict[str, Any]) -> str:
    """An ssh ProxyCommand tunnelling through the cluster's login node.

    Used as the first hop to the user-level sshd on an allocated node:
    ``ssh <login node> -W [%h]:%p``. Built from the same runner that the
    control commands use, so the hop shares its control master (and the
    user's own ControlPath, if the PBS config sets one).

    Args:
        ssh_config: The ``provider.ssh`` section of the cluster config.
    """
    runner = make_login_node_runner(ssh_config)
    base = runner.ssh_base_command(
        ssh_mode=command_runner.SshMode.NON_INTERACTIVE,
        port_forward=None,
        connect_timeout=None)
    return ' '.join(base[:-1] + ['-W', '\'[%h]:%p\'', base[-1]])
