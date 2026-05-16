"""Deterministic Ray + sshd ports and loopback IPs for hostNetwork pods.

When a Kubernetes pod runs with ``hostNetwork: true`` it shares the host's
network namespace, so a second SkyPilot pod scheduled to the same node would
collide on Ray's default ports (6380, 8266, 8076, ...) and on the node's own
sshd (host:22).

Rather than probing for free ports at pod startup and shipping the head's
choice to the workers through a ConfigMap (which needs an in-cluster service
account with namespace-wide RBAC), every pod independently derives an
*identical* port set and a per-pod loopback IP from
``sha256(cluster_name_on_cloud)`` and its rank. Because the head is always rank
0, every worker can compute the head's GCS port and loopback IP locally — no
ConfigMap, no RBAC, no head->worker handoff, no in-cluster API calls.

The derived numbers are baked into the rendered cluster YAML at provision time
(see ``sky/provision/instance_setup.py`` and ``kubernetes-ray.yml.j2``); only a
worker's own rank is resolved at runtime, since a single worker pod spec serves
every worker.

Scheme::

    h         = sha256(cluster_name_on_cloud)
    port_base = 20000 + (h[:2] as uint16 % 120) * 100   # 20000..31900
    port      = port_base + rank * 10 + offset           # offset table below
    node_ip   = 127.<h[0] | 0x80>.<h[1]>.<rank>

Trade-offs (accepted, same failure mode as a probe's TOCTOU — fails loudly with
``EADDRINUSE``, never silently mis-routes):

- Birthday-paradox bucket collision between two *unrelated* SkyPilot clusters on
  one node: with 120 buckets the chance of any collision reaches ~50% at ~13
  co-located clusters. A non-issue for RDMA/RoCE hosts (one or two clusters per
  node).
- More than 10 pods of the *same* cluster co-located on one node would overflow
  the 100-port window into the neighbouring bucket. Also a non-issue for the
  typical one-pod-per-node RDMA layout.
- Cross-K8s-node hostNetwork is unchanged and still unsupported: a worker's
  ``127.x.y.0`` dial-back to the head's loopback is only routable when head and
  worker share the host's netns (same node).

Stdlib-only so it stays importable on both the client and the API server
without pulling in heavy dependencies.
"""
import dataclasses
import functools
import hashlib
import re
from typing import Any, Dict, Optional, Tuple

# The deterministic port band. Chosen to sit clear of Ray's defaults (6380,
# 8266, 8076), the skylet gRPC port (46590), and the Linux ephemeral range
# (typically 32768+), while leaving Ray's own ``--min-worker-port 11002``
# untouched.
_PORT_RANGE_START = 20000
_NUM_BUCKETS = 120
_PORTS_PER_BUCKET = 100
_PORTS_PER_RANK = 10

# Largest rank that still fits inside a cluster's 100-port window
# (rank * 10 + max_offset must stay < _PORTS_PER_BUCKET).
MAX_RANK = _PORTS_PER_BUCKET // _PORTS_PER_RANK - 1

# Offset of each port within a pod's per-rank 10-port slot. ``sshd`` is parked
# at the top of the slot so the contiguous low offsets stay available for Ray.
# Offset 8 is intentionally reserved for a future Ray port.
PORT_OFFSETS: Dict[str, int] = {
    'gcs': 0,
    'dashboard': 1,
    'node_manager': 2,
    'object_manager': 3,
    'runtime_env_agent': 4,
    'dashboard_agent_listen': 5,
    'metrics_export': 6,
    'ray_client_server': 7,
    'sshd': 9,
}

_WORKER_POD_RE = re.compile(r'-worker(\d+)$')

# Env var a worker pod's bootstrap sets to its own rank (see
# ``worker_rank_shell``), used to index the deterministic per-rank port slot
# at runtime. A worker's rank is the one value not known at provision time —
# a single rendered worker pod spec serves every worker — so it is the only
# part of the scheme resolved in-pod rather than baked into the YAML.
WORKER_RANK_ENV = 'SKYPILOT_NODE_RANK'


@dataclasses.dataclass(frozen=True)
class HostNetworkPorts:
    """The deterministic port set for a single hostNetwork pod (one rank)."""
    gcs: int
    dashboard: int
    node_manager: int
    object_manager: int
    runtime_env_agent: int
    dashboard_agent_listen: int
    metrics_export: int
    ray_client_server: int
    sshd: int


def is_host_network(pod_config: Optional[Dict[str, Any]]) -> bool:
    """Returns True if the merged pod_config requests ``hostNetwork: true``."""
    if not pod_config:
        return False
    spec = pod_config.get('spec') or {}
    return bool(spec.get('hostNetwork', False))


# Cached: the digest is a pure function of the name, and the derivation
# helpers each recompute it (port_base, node_ip_prefix, derive_ports,
# derive_node_ip), so a single build() or a get_cluster_info() loop over N
# pods would otherwise rehash the same name 4N times. Bounded so a long-lived
# API server process can't grow it without limit.
@functools.lru_cache(maxsize=1024)
def _cluster_digest(cluster_name_on_cloud: str) -> bytes:
    return hashlib.sha256(cluster_name_on_cloud.encode('utf-8')).digest()


def port_base(cluster_name_on_cloud: str) -> int:
    """The start of the cluster's 100-port window (rank 0, offset 0)."""
    h = _cluster_digest(cluster_name_on_cloud)
    bucket = int.from_bytes(h[:2], 'big') % _NUM_BUCKETS
    return _PORT_RANGE_START + bucket * _PORTS_PER_BUCKET


def derive_ports(cluster_name_on_cloud: str, rank: int) -> HostNetworkPorts:
    """Deterministic port set for the pod at ``rank`` (head=0, worker i=i)."""
    if rank < 0:
        raise ValueError(f'rank must be non-negative, got {rank}')
    base = port_base(cluster_name_on_cloud) + rank * _PORTS_PER_RANK
    return HostNetworkPorts(
        **{name: base + off for name, off in PORT_OFFSETS.items()})


def node_ip_prefix(cluster_name_on_cloud: str) -> str:
    """The ``127.<b1>.<b2>.`` prefix; append a rank to get a pod's loopback.

    Lets the (single) worker pod spec compute ``<prefix><rank>`` at runtime,
    since one rendered worker command serves every worker.
    """
    h = _cluster_digest(cluster_name_on_cloud)
    return f'127.{h[0] | 0x80}.{h[1]}.'


def derive_node_ip(cluster_name_on_cloud: str, rank: int) -> str:
    """Per-pod loopback IP ``127.<b1>.<b2>.<rank>``.

    The whole of ``127.0.0.0/8`` is routed to ``lo`` by the Linux kernel, so no
    ``CAP_NET_ADMIN`` is needed and every pod sharing the host netns can dial
    every other pod's loopback. ``b1`` has its high bit set so the address
    never lands in ``127.0.0.x`` (where the kernel's own ``127.0.0.1`` lives).
    """
    if rank < 0:
        raise ValueError(f'rank must be non-negative, got {rank}')
    return f'{node_ip_prefix(cluster_name_on_cloud)}{rank}'


def worker_rank_shell() -> str:
    """Bash that sets ``$SKYPILOT_NODE_RANK`` from the pod's downward-API name.

    ``${SKYPILOT_POD_NAME##*-worker}`` strips up to and including the last
    ``-worker`` (``cl-worker3`` -> ``3``); the ``case`` guard keeps any
    non-worker name at rank 0. Kept to one line for the YAML block scalar.
    """
    return (f'{WORKER_RANK_ENV}=0; '
            f'case "$SKYPILOT_POD_NAME" in '
            f'*-worker*[0-9]) '
            f'{WORKER_RANK_ENV}="${{SKYPILOT_POD_NAME##*-worker}}";; '
            f'esac')


@dataclasses.dataclass(frozen=True)
class HostNetworkRayParams:
    """Deterministic values spliced into the rendered Ray start commands.

    Built once at provision time (sky/clouds/kubernetes.py). The head's values
    are fully resolved literals (its rank is known: 0). The worker's are bash
    expressions evaluated in-pod against ``$SKYPILOT_NODE_RANK`` because one
    rendered worker command serves every worker.
    """
    head_ports: HostNetworkPorts
    head_node_ip: str
    # Ray-port-role -> bash arithmetic expression, e.g.
    # ``$(( 20500 + SKYPILOT_NODE_RANK * 10 + 2 ))``.
    worker_port_exprs: Dict[str, str]
    # ``127.<b1>.<b2>.$SKYPILOT_NODE_RANK``.
    worker_node_ip_expr: str
    # Bash that defines ``$SKYPILOT_NODE_RANK`` (prepended to the worker cmd).
    rank_shell: str

    @classmethod
    def build(cls, cluster_name_on_cloud: str) -> 'HostNetworkRayParams':
        base = port_base(cluster_name_on_cloud)
        prefix = node_ip_prefix(cluster_name_on_cloud)
        worker_port_exprs = {
            role: f'$(( {base} + {WORKER_RANK_ENV} * '
            f'{_PORTS_PER_RANK} + {off} ))'
            for role, off in PORT_OFFSETS.items()
        }
        return cls(
            head_ports=derive_ports(cluster_name_on_cloud, rank=0),
            head_node_ip=derive_node_ip(cluster_name_on_cloud, rank=0),
            worker_port_exprs=worker_port_exprs,
            worker_node_ip_expr=f'{prefix}${WORKER_RANK_ENV}',
            rank_shell=worker_rank_shell(),
        )


def rank_from_pod_name(pod_name: str) -> int:
    """Rank from a SkyPilot K8s pod name: ``-head`` -> 0, ``-worker<i>`` -> i.

    Anchored at the end so a cluster name that itself contains ``-worker`` or
    ``-head`` doesn't confuse the parse. Unrecognized names default to rank 0
    (treated as the head's slot) rather than raising — a wrong-but-deterministic
    rank still beats crashing a status refresh.
    """
    m = _WORKER_POD_RE.search(pod_name)
    if m is not None:
        return int(m.group(1))
    return 0


def cluster_and_rank_from_pod_name(pod_name: str) -> Tuple[str, int]:
    """Split a SkyPilot K8s pod name into (cluster_name_on_cloud, rank).

    ``<cluster>-head`` -> ``(<cluster>, 0)``; ``<cluster>-worker<i>`` ->
    ``(<cluster>, i)``. Lets a caller that only has the pod name (e.g. the SSH
    proxy) recompute the same deterministic ports the pod itself derived.
    """
    m = _WORKER_POD_RE.search(pod_name)
    if m is not None:
        return pod_name[:m.start()], int(m.group(1))
    if pod_name.endswith('-head'):
        return pod_name[:-len('-head')], 0
    return pod_name, 0
