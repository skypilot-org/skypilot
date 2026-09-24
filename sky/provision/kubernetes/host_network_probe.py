"""Verify the host ports assigned to a hostNetwork pod, before Ray binds them.

When a K8s pod has ``hostNetwork: true`` it shares the host's network
namespace, so a sibling SkyPilot pod scheduled to the same node would collide
on Ray's default ports. The server assigns each pod a block and declares it as
``hostPort`` (see ``host_network_ports``), which makes the scheduler refuse to
co-schedule two pods wanting the same port.

This script is the pod-side half, and it is an **assertion**, not an
allocator: it binds each assigned port to prove it is actually free, then
releases it just before ``ray start`` takes it. It makes no Kubernetes API
call, which is the point -- a workload pod needs no API access for this.

The scheduler only knows about *declared* hostPorts, so a port held by
something that never declared one -- a node daemon, or a SkyPilot pod created
before ports moved into the pod spec -- is invisible to it. That is what this
bind catches, and why it fails loudly rather than re-probing: a silent
re-probe would paper over a mis-chosen port range instead of surfacing it.

Stdlib-only so it can run during the window when the pod's skypilot install is
in flux.
"""
import argparse
import os
import socket
import sys
import time
from typing import Dict, List, Optional

# SKYPILOT_RAY_PORT is the head's GCS port; a worker is handed the *head's*
# value here, because that is what it must dial to join.
_ENV_VAR_FOR_PORT: Dict[str, str] = {
    'gcs': 'SKYPILOT_RAY_PORT',
    'dashboard': 'SKYPILOT_RAY_DASHBOARD_PORT',
    'node_manager': 'SKYPILOT_RAY_NODE_MANAGER_PORT',
    'object_manager': 'SKYPILOT_RAY_OBJECT_MANAGER_PORT',
    'ray_client_server': 'SKYPILOT_RAY_CLIENT_SERVER_PORT',
    'dashboard_agent_listen': 'SKYPILOT_RAY_DASHBOARD_AGENT_LISTEN_PORT',
    'runtime_env_agent': 'SKYPILOT_RAY_RUNTIME_ENV_AGENT_PORT',
    'metrics_export': 'SKYPILOT_RAY_METRICS_EXPORT_PORT',
    # Pod sshd port. host:22 is owned by the K8s node's own sshd under
    # hostNetwork, so the pod must bind sshd to its assigned port instead.
    'sshd': 'SKYPILOT_SSHD_PORT',
}

# Public: the server assigns these (host_network_ports) and this script
# verifies them. One list, so a port cannot be assigned without being checked
# or checked without being assigned.
#
# THE ORDER IS THE ON-CLUSTER FORMAT. A pod's ports are reconstructed
# positionally -- block start plus index -- so reordering silently re-maps
# every running pod's ports while every check still passes: the block is
# still BLOCK_SIZE contiguous ports in range. Append to add one; a longer
# list fails loudly against existing pods, which is what you want.
HEAD_PORT_NAMES: List[str] = list(_ENV_VAR_FOR_PORT)

# A worker runs neither GCS, dashboard nor ray-client-server, so those three
# slots of its block go unused -- every pod is given the head-sized block so
# one pod spec serves every pod in the cluster.
WORKER_PORT_NAMES: List[str] = [
    'node_manager',
    'object_manager',
    'dashboard_agent_listen',
    'runtime_env_agent',
    'metrics_export',
    'sshd',
]

# Deprecation window only. This pod no longer reads or writes the ConfigMap;
# the API server still reads the one a pre-change cluster published, because
# such a cluster's pods declare no ports and their head's block is knowable
# from nowhere else. Delete both with that read, one release on.
SSHD_KEY_PREFIX = 'sshd_'


def ray_ports_configmap_name(cluster_name_on_cloud: str) -> str:
    """Name of the ConfigMap a pre-change cluster's head published to."""
    return f'{cluster_name_on_cloud}-ray-ports'


_HEAD_GCS_TCP_WAIT_TIMEOUT_S = 600
_HEAD_GCS_TCP_WAIT_INTERVAL_S = 2


def env_var_for_port(name: str) -> str:
    """The env var this port is passed in.

    Public so the server exports the same names the pod reads; one mapping,
    so a renamed var cannot be exported under the old name.
    """
    return _ENV_VAR_FOR_PORT[name]


def _assigned_ports(names: List[str]) -> Dict[str, int]:
    """Read the assigned ports out of the pod env."""
    ports: Dict[str, int] = {}
    missing: List[str] = []
    for name in names:
        raw = os.environ.get(_ENV_VAR_FOR_PORT[name])
        if raw is None:
            missing.append(_ENV_VAR_FOR_PORT[name])
            continue
        ports[name] = int(raw)
    if missing:
        raise RuntimeError(
            'Host ports were not assigned to this pod: '
            f'{", ".join(sorted(missing))} unset. The pod spec should carry '
            'them; this pod was likely created by an older SkyPilot.')
    return ports


def _verify_free(ports: Dict[str, int]) -> List[socket.socket]:
    """Bind every assigned port, proving it is free.

    Returns the held sockets; the caller keeps them alive until just before
    ``ray start`` and sshd rebind the same ports.
    """
    held: List[socket.socket] = []
    for name, port in sorted(ports.items(), key=lambda kv: kv[1]):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('0.0.0.0', port))
        except OSError as e:
            for held_sock in held:
                held_sock.close()
            sock.close()
            raise RuntimeError(
                f'Assigned host port {port} ({name}) is already in use on '
                f'this node: {e}.\n'
                'Launching again assigns a different block and usually '
                'succeeds -- blocks are chosen at random, so a clash is '
                'rarely hit twice.\n'
                'If it keeps failing, something on this node holds a port in '
                'the range SkyPilot reserves for host-networked pods. The '
                'Kubernetes scheduler only accounts for ports a pod '
                '*declares*, so such a holder is invisible to it: a node '
                'daemon, a SkyPilot pod created before host ports moved into '
                'the pod spec, or an overlap with the cluster\'s NodePort '
                'range.') from e
        held.append(sock)
    return held


def _wait_head_gcs_tcp(host: str, port: int) -> None:
    """Block until the head's GCS port answers on TCP."""
    deadline = time.monotonic() + _HEAD_GCS_TCP_WAIT_TIMEOUT_S
    last_err: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError as e:
            last_err = e
            time.sleep(_HEAD_GCS_TCP_WAIT_INTERVAL_S)
    raise TimeoutError(
        f'Head GCS {host}:{port} did not accept connections within '
        f'{_HEAD_GCS_TCP_WAIT_TIMEOUT_S}s (last error: {last_err}).')


def _run_head() -> None:
    held = _verify_free(_assigned_ports(HEAD_PORT_NAMES))
    del held  # release just before ray start takes them


def _run_worker() -> None:
    ports = _assigned_ports(WORKER_PORT_NAMES)
    held = _verify_free(ports)
    # The head's GCS port, assigned by the server: head and workers are
    # created concurrently, so a worker cannot read it off the head pod.
    head_gcs = int(os.environ[_ENV_VAR_FOR_PORT['gcs']])
    del held
    # The pod template points SKYPILOT_RAY_HEAD_IP at the head's headless
    # Service DNS. Same-cluster pods never share a K8s node (per-cluster
    # podAntiAffinity), so that resolves to the head's routable host IP.
    head_ip = os.environ.get('SKYPILOT_RAY_HEAD_IP')
    if head_ip:
        _wait_head_gcs_tcp(head_ip, head_gcs)


def main(argv: Optional[List[str]] = None) -> int:
    # No description: __doc__ is stripped by source_utils.minify_python_source
    # before the script is inlined into the pod bootstrap.
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['head', 'worker'], required=True)
    args = parser.parse_args(argv)
    if args.mode == 'head':
        _run_head()
    else:
        _run_worker()
    return 0


if __name__ == '__main__':
    sys.exit(main())
