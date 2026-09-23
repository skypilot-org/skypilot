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
import random
from typing import Any, Dict, List, Optional

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
PORT_RANGE_START = 20000
PORT_RANGE_END = 29999

# One contiguous block per pod, sized for the head; a worker not using three of
# them costs nothing and keeps a single pod spec for every pod in the cluster.
#
# Contiguous rather than N independent ports because it collides *less*: two
# blocks overlap only if their starts are within BLOCK_SIZE-1 (~0.17% over this
# range), where N independent ports collide if any single one does (~0.81%).
BLOCK_SIZE = len(host_network_probe.HEAD_PORT_NAMES)

_MAX_START = PORT_RANGE_END - BLOCK_SIZE + 1

# The client's SSH proxy command finds the pod's sshd port by this name.
SSHD_PORT_NAME = 'ssh'


def allocate_block() -> Dict[str, int]:
    """Assign a fresh contiguous block, keyed by port name."""
    start = random.randint(PORT_RANGE_START, _MAX_START)
    return {
        name: start + offset
        for offset, name in enumerate(host_network_probe.HEAD_PORT_NAMES)
    }


def ports_from_pod(pod: Any) -> Optional[Dict[str, int]]:
    """The block a live pod declares, or None if it declares no ports.

    A pod created before this change declares none at all -- the template's
    ``ports:`` block was rendered only for non-hostNetwork pods -- so "no
    ports" is a state every caller has to handle, not an error.
    """
    spec = getattr(pod, 'spec', None)
    containers = getattr(spec, 'containers', None) or []
    declared: List[int] = []
    for container in containers:
        for port in (getattr(container, 'ports', None) or []):
            host_port = getattr(port, 'host_port', None)
            if host_port is not None:
                declared.append(int(host_port))
    if len(declared) < BLOCK_SIZE:
        return None
    start = min(declared)
    return {
        name: start + offset
        for offset, name in enumerate(host_network_probe.HEAD_PORT_NAMES)
    }


def resolve_block(
    pod: Any,
    configmap_ports: Optional[Dict[str, int]] = None,
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
      2. else the ConfigMap the pre-change probe published
      3. else a fresh block

    ``pod`` may be None (no such pod yet).
    """
    if pod is not None:
        declared = ports_from_pod(pod)
        if declared is not None:
            return declared
    if configmap_ports:
        return dict(configmap_ports)
    return allocate_block()


def is_unschedulable(pod: Any) -> bool:
    """Whether this pod is Pending *because the scheduler refused it*.

    Deliberately narrower than "Pending": a pod pending on an image pull or a
    GPU is making progress, and deleting it on every relaunch would throw that
    progress away. Only a pod the scheduler could not place is worth
    recreating with a different block.
    """
    status = getattr(pod, 'status', None)
    if getattr(status, 'phase', None) != 'Pending':
        return False
    for condition in (getattr(status, 'conditions', None) or []):
        if (getattr(condition, 'type', None) == 'PodScheduled' and
                getattr(condition, 'status', None) == 'False' and
                getattr(condition, 'reason', None) == 'Unschedulable'):
            return True
    return False


def apply_to_pod_spec(pod_spec: Dict[str, Any], ports: Dict[str, int],
                      head_gcs_port: int) -> None:
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
    containers = pod_spec.setdefault('spec', {}).setdefault('containers', [{}])
    container = containers[0]
    # Only the sshd port is named: the client's SSH proxy command selects it
    # by name (`ports[?(@.name=="ssh")]`) because it is built during auth
    # setup, before the pod exists and before a port has been assigned, so it
    # cannot be handed the number. The rest need no name and a K8s port name
    # is capped at 15 characters, which several of these would exceed.
    sshd_port = ports.get('sshd')
    container['ports'] = [{
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
