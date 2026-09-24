"""Server-side port assignment for hostNetwork pods.

Under ``hostNetwork`` a pod shares the node's network namespace, so two
SkyPilot clusters scheduled to one node collide on Ray's ports. This module
assigns each pod a block and the caller declares it as ``hostPort`` in the pod
spec; the scheduler's NodePorts predicate then refuses to co-schedule two pods
wanting the same host port, so the collision becomes a scheduling constraint
rather than a runtime failure.

The port *names* live in ``host_network_probe`` -- the in-pod script binds
them, this module assigns them, and one list serves both.
"""
import os
import random
from typing import Any, Dict, List, Optional, Tuple

from sky import skypilot_config
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import host_network_probe

# Below the node's ephemeral range (Linux default 32768-60999) and below the
# default NodePort range (30000-32767).
#
# The ephemeral floor is the load-bearing half. The in-pod probe this replaces
# binds port 0 and *holds* the socket, so a kernel-allocated port can never be
# handed to anyone else -- it is immune by construction. Server-side
# assignment is not: under hostNetwork the competing allocator is the node's
# entire outbound connection load, drawing from that same pool, and it can
# take an assigned port between admission and the pod's bind.
#
# The default is right where both of those hold, but neither is guaranteed:
# the ephemeral floor is a per-node sysctl, and a cluster measured during
# this work sets it to 10240, putting the whole default range inside the
# kernel's pool.
#
# So the range is configurable **per Kubernetes context**, because that is
# what it varies with -- one API server serves many contexts, and two of the
# three clusters measured need different values. It reads through the same
# per-context chain as `namespace` and `remote_identity`, workspace override
# included; there is nothing special about it.
#
# Changing it is not free. `ports_from_pod` uses the range to tell our block
# apart from a user's pod_config ports, so a context whose range moves reads
# every existing pod's block as "not ours" and hands out new ones while the
# pods keep listening on the old. Widening is not exempt either:
# `apply_to_pod_spec` refuses a user's pod_config port inside the range, so
# widening turns a port that launched yesterday into a launch-time error.
# Change it with that context's clusters drained.
_RANGE_KEY = 'host_network_port_range'
_DEFAULT_RANGE = (20000, 29999)


def resolve_range(context: Optional[str]) -> Tuple[int, int]:
    """The reserved range for one context, from config or the default.

    Malformed and too-narrow values are refused here rather than surfacing
    later as a failure about the block, which would point at the wrong
    thing: a range one port short of a block fails every launch, and the
    error would name the block rather than the setting.
    """
    raw = skypilot_config.get_effective_workspace_region_config(
        cloud='kubernetes',
        keys=(_RANGE_KEY,),
        region=context,
        default_value=None)
    if not raw:
        return _DEFAULT_RANGE
    try:
        start_s, _, end_s = str(raw).partition('-')
        start, end = int(start_s), int(end_s)
    except ValueError:
        raise ValueError(f'kubernetes.{_RANGE_KEY}={raw!r} is not of the form '
                         '"<start>-<end>", e.g. "8000-9999".') from None
    if not 1 <= start < end <= 65535:
        raise ValueError(f'kubernetes.{_RANGE_KEY}={raw!r} must satisfy '
                         '1 <= start < end <= 65535.')
    width = end - start + 1
    need = len(host_network_probe.HEAD_PORT_NAMES)
    if width < need:
        raise ValueError(
            f'kubernetes.{_RANGE_KEY}={raw!r} spans {width} ports; a pod '
            f'needs a contiguous block of {need}.')
    return start, end


# The default, and the value the tripwire pins. Every function below takes
# `context` as a required argument rather than defaulting to this: the range
# decides which hostPorts read back as ours, so a caller that resolves the
# default while its counterpart resolved a context's range hands out fresh
# ports while the pods keep listening on the old ones. Required means mypy
# says so; a default means a test has to happen to cover that pair.
PORT_RANGE_START, PORT_RANGE_END = _DEFAULT_RANGE

# One contiguous block per pod, sized for the head; a worker not using three of
# them costs nothing and keeps a single pod spec for every pod in the cluster.
#
# Contiguous rather than N independent ports because it collides *less*: two
# blocks overlap only if their starts are within BLOCK_SIZE-1 (~0.17% over this
# range), where N independent ports collide if any single one does (~0.81%).
BLOCK_SIZE = len(host_network_probe.HEAD_PORT_NAMES)

# The client's SSH proxy command finds the pod's sshd port by this name.
SSHD_PORT_NAME = 'ssh'

# Test-only: pin the block start so two clusters are handed the same one.
# The collision this design exists to arbitrate has a ~0.17% chance of
# occurring naturally over the range, so an e2e case that waits for it tests
# nothing. Unset in every normal run.
_PINNED_START_ENV = 'SKYPILOT_HOST_NETWORK_PORT_START'


def allocate_block(context: Optional[str]) -> Dict[str, int]:
    """Assign a fresh contiguous block, keyed by port name."""
    pinned = os.environ.get(_PINNED_START_ENV)
    if pinned:
        start = int(pinned)
    else:
        lo, hi = resolve_range(context)
        start = random.randint(lo, hi - BLOCK_SIZE + 1)
    return {
        name: start + offset
        for offset, name in enumerate(host_network_probe.HEAD_PORT_NAMES)
    }


def ports_from_pod(pod: Any,
                   context: Optional[str]) -> Optional[Dict[str, int]]:
    """The block a live pod declares, or None if it declares none.

    Reads the **ray-node** container specifically. A user's ``pod_config`` can
    add containers, and a sidecar declaring its own hostPort would otherwise
    be folded into the block -- a lower one would become its start, and every
    port would reconstruct to something nothing is listening on. For a head
    that number is handed to every worker in the launch.

    Three states, kept distinct:

    * no ports at all -> ``None``. A pod created before this change declares
      none (the template rendered ``ports:`` only for non-hostNetwork pods),
      so the caller falls back and then allocates.
    * exactly one contiguous in-range block -> that block.
    * anything else -> raise. Treating a partial read as "legacy" would
      allocate a fresh block for a pod that is running and listening on the
      old one. That is not hypothetical: ``BLOCK_SIZE`` is
      ``len(HEAD_PORT_NAMES)``, so adding a tenth port name would make every
      existing pod's nine ports look partial, cluster-wide, on upgrade.
    """
    lo, hi = resolve_range(context)
    spec = getattr(pod, 'spec', None)
    declared: List[int] = []
    for container in (getattr(spec, 'containers', None) or []):
        if getattr(container, 'name',
                   None) != k8s_constants.RAY_NODE_CONTAINER_NAME:
            continue
        for port in (getattr(container, 'ports', None) or []):
            host_port = getattr(port, 'host_port', None)
            # In-range only. The container also carries the user's pod_config
            # ports, which apply_to_pod_spec keeps; it refuses any of theirs
            # inside the reserved range, so everything in range here is ours.
            # Without this the user's port joins the block and the run stops
            # being contiguous, and every read of the cluster raises.
            if (host_port is not None and lo <= int(host_port) <= hi):
                declared.append(int(host_port))
    if not declared:
        return None
    start = min(declared)
    expected = list(range(start, start + BLOCK_SIZE))
    if sorted(declared) != expected or start < lo or expected[-1] > hi:
        pod_name = getattr(getattr(pod, 'metadata', None), 'name', '<unknown>')
        raise RuntimeError(
            f'Pod {pod_name!r} declares host ports {sorted(declared)}, which '
            f'is not one contiguous block of {BLOCK_SIZE} within '
            f'{lo}-{hi}. Refusing to guess: '
            'allocating a new block would point the cluster at ports nothing '
            'is listening on.')
    return {
        name: start + offset
        for offset, name in enumerate(host_network_probe.HEAD_PORT_NAMES)
    }


def resolve_block(
    pod: Any,
    configmap_ports: Optional[Dict[str, int]],
    context: Optional[str],
) -> Dict[str, int]:
    """The block for one pod, resolved in the only order that is correct.

    Written once because there are two callers -- ``_create_pods`` assigning
    ports and ``get_cluster_info`` reading them back -- and they must not
    disagree. A pod created before this change declares no ports, so for one
    release the ConfigMap the old probe published is still the answer for it;
    disagreeing there means a new worker is told a GCS port the head is not
    listening on, and it never joins.

    Order:
      1. the live pod's declared hostPorts -- it is bound and depended on
      2. else, *only if that pod exists*, the ConfigMap the pre-change probe
         published. A ConfigMap with no pod behind it is not compatibility,
         it is garbage: it is owned by the head pod, so one outliving its
         head is a stale object whose GC has not caught up, and its ports may
         already be taken on the new pod's node. The in-pod probe this
         replaces guarded the same case by owner UID.
      3. else a fresh block

    ``pod`` may be None (no such pod yet).
    """
    if pod is not None:
        declared = ports_from_pod(pod, context)
        if declared is not None:
            return declared
        if configmap_ports:
            return dict(configmap_ports)
    return allocate_block(context)


def apply_to_pod_spec(pod_spec: Dict[str, Any], ports: Dict[str, int],
                      head_gcs_port: int, context: Optional[str]) -> None:
    """Declare ``ports`` on the pod spec and export them to the bootstrap.

    Declaring hostPort is what makes the assignment safe: the scheduler's
    NodePorts predicate will not co-schedule two pods wanting the same host
    port. Under hostNetwork the API server defaults hostPort to containerPort
    anyway; we write it explicitly so the spec does not depend on that.

    ``head_gcs_port`` is the *head's* GCS port, which every pod needs in order
    to reach it -- a worker's own gcs slot goes unused. The server has to pass
    it because head and workers are created concurrently, so a worker cannot
    read it off a head pod that may not exist yet.
    """
    # The ray-node container specifically, matching ports_from_pod: a user's
    # pod_config can add containers, and writing into the wrong one would put
    # the ports where nothing reads them.
    containers = pod_spec.setdefault('spec', {}).setdefault('containers', [{}])
    container = next(
        (c for c in containers
         if c.get('name') == k8s_constants.RAY_NODE_CONTAINER_NAME),
        containers[0])
    # Only the sshd port is named: the client's SSH proxy command selects it
    # by name (`ports[?(@.name=="ssh")]`) because it is built during auth
    # setup, before the pod exists and before a port has been assigned, so it
    # cannot be handed the number. The rest need no name and a K8s port name
    # is capped at 15 characters, which several of these would exceed.
    sshd_port = ports.get('sshd')
    # Keep the container's existing ports. `combine_pod_config_fields`
    # appends a user's `pod_config` ports here, and under hostNetwork the
    # template renders none of its own, so before this change they were this
    # container's *only* ports -- replacing the list deleted them.
    #
    # Nothing of ours is ever already present: this runs on a deepcopy that is
    # never written back, and `node_config` is restored from the rendered
    # template, which declares no ports under hostNetwork. So an entry inside
    # the reserved range is the user's, and it is refused rather than dropped
    # -- under hostNetwork it would contend with the assigned block for real,
    # and a silent drop would leave them no way to find out why.
    #
    # Scoped to this container on purpose, though contention is node-wide: a
    # sidecar taking a reserved port is not refused here, but it degrades
    # loudly on its own -- duplicate hostPort in one pod is rejected by the
    # API server, and across pods the scheduler's predicate catches it.
    lo, hi = resolve_range(context)
    kept = []
    for entry in (container.get('ports') or []):
        if not isinstance(entry, dict):
            continue
        declared_port = entry.get('hostPort', entry.get('containerPort'))
        if (isinstance(declared_port, int) and lo <= declared_port <= hi):
            raise ValueError(
                f'Container port {declared_port} falls inside '
                f'{lo}-{hi}, which SkyPilot reserves '
                'for the host ports it assigns to hostNetwork pods. Pick a '
                'port outside that range in pod_config.')
        if entry.get('name') == SSHD_PORT_NAME:
            raise ValueError(
                f'A port named {SSHD_PORT_NAME!r} is already declared on '
                f'container {container.get("name")!r}. SkyPilot selects the '
                'pod\'s sshd port by that name, so it cannot be reused.')
        kept.append(entry)
    container['ports'] = kept + [{
        'containerPort': port,
        'hostPort': port,
        'protocol': 'TCP',
        **({
            'name': SSHD_PORT_NAME
        } if port == sshd_port else {}),
    } for port in sorted(ports.values())]

    exported = dict(ports)
    exported['gcs'] = head_gcs_port
    env = container.setdefault('env', [])
    by_name = {
        entry.get('name'): entry for entry in env if isinstance(entry, dict)
    }
    for name, port in exported.items():
        var = host_network_probe.env_var_for_port(name)
        if var in by_name:
            by_name[var]['value'] = str(port)
        else:
            env.append({'name': var, 'value': str(port)})
