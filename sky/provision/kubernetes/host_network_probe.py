"""Free-port probe and ConfigMap publish/discover for hostNetwork pods.

When a Kubernetes pod is launched with ``hostNetwork: true`` (required for
RoCE / RDMA performance on some accelerator setups), the pod shares the
host's network namespace. Ray's default port set (GCS, dashboard,
object/node manager, dashboard agent, runtime env agent, metrics export,
ray client server) then binds directly on the host. A second SkyPilot
pod scheduled to the same node would collide on those ports.

This module:

* Picks a free port for each Ray role by binding ephemeral sockets on
  ``0.0.0.0`` and reading back what the kernel assigned. Sockets are held
  until the process exits, then released right before ``ray start`` binds
  them — a millisecond race window that is acceptable for v0.
* Writes the chosen ports to a sourceable env file consumed by Ray's
  bootstrap commands in :mod:`sky.provision.instance_setup`.
* For the head pod: publishes the chosen ports to a Kubernetes ConfigMap
  named ``<cluster>-ray-ports`` so worker pods can discover the head's
  GCS port (head IP is already discoverable via the headless Service DNS).
* For worker pods: polls that ConfigMap to learn the head's GCS port,
  then probes its own host for local-bind ports independently.

The script is invoked via ``python -m
sky.provision.kubernetes.host_network_probe`` from the K8s pod
entrypoint. It depends only on the Python standard library so it can run
before the skypilot wheel has finished installing.
"""
import argparse
import json
import os
import socket
import ssl
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

# Maps internal port names (also used as ConfigMap keys) to the env vars
# that ray_head_start_command / ray_worker_start_command read via
# ${VAR:-default} substitution. SKYPILOT_RAY_PORT is the head's GCS port
# — it predates this feature (the worker bootstrap template already uses
# this name to dial the head) so we reuse it instead of inventing a
# parallel SKYPILOT_RAY_GCS_PORT.
_ENV_VAR_FOR_PORT: Dict[str, str] = {
    'gcs': 'SKYPILOT_RAY_PORT',
    'dashboard': 'SKYPILOT_RAY_DASHBOARD_PORT',
    'node_manager': 'SKYPILOT_RAY_NODE_MANAGER_PORT',
    'object_manager': 'SKYPILOT_RAY_OBJECT_MANAGER_PORT',
    'ray_client_server': 'SKYPILOT_RAY_CLIENT_SERVER_PORT',
    'dashboard_agent_listen': 'SKYPILOT_RAY_DASHBOARD_AGENT_LISTEN_PORT',
    'runtime_env_agent': 'SKYPILOT_RAY_RUNTIME_ENV_AGENT_PORT',
    'metrics_export': 'SKYPILOT_RAY_METRICS_EXPORT_PORT',
}

# Ports the head needs.
_HEAD_PORT_NAMES: List[str] = [
    'gcs',
    'dashboard',
    'node_manager',
    'object_manager',
    'ray_client_server',
    'dashboard_agent_listen',
    'runtime_env_agent',
    'metrics_export',
]

# Ports a worker needs. Worker doesn't run GCS/dashboard/ray-client server
# — it only binds local raylet ports.
_WORKER_PORT_NAMES: List[str] = [
    'node_manager',
    'object_manager',
    'dashboard_agent_listen',
    'runtime_env_agent',
    'metrics_export',
]

_SA_TOKEN_PATH = '/var/run/secrets/kubernetes.io/serviceaccount/token'
_SA_CA_PATH = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'

# ConfigMap poll: how long workers wait for the head to publish its ports
# before giving up. Bootstraps that exceed this likely indicate the head
# pod is wedged; fail loudly rather than hang the worker forever.
_CONFIGMAP_POLL_TIMEOUT_S = 600
_CONFIGMAP_POLL_INTERVAL_S = 2

# Worker TCP wait: the ConfigMap is published just before the head's
# probe exits (and ray binds), so the worker can read the GCS port a
# beat before ray is actually accepting connections. Wait until the
# port answers before handing off to `ray start --address`.
_HEAD_GCS_TCP_WAIT_TIMEOUT_S = 600
_HEAD_GCS_TCP_WAIT_INTERVAL_S = 1


def _bind_ephemeral_port() -> Tuple[socket.socket, int]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('0.0.0.0', 0))
    return s, s.getsockname()[1]


def _probe_ports(
        names: List[str]) -> Tuple[List[socket.socket], Dict[str, int]]:
    """Bind one ephemeral socket per name and return (held_sockets, ports).

    Sockets must be kept alive until just before ``ray start`` runs, then
    closed. The caller does this by exiting the process after writing the
    env file.
    """
    held: List[socket.socket] = []
    ports: Dict[str, int] = {}
    for name in names:
        sock, port = _bind_ephemeral_port()
        held.append(sock)
        ports[name] = port
    return held, ports


def _write_env_file(ports: Dict[str, int], path: str) -> None:
    lines = [
        f'export {_ENV_VAR_FOR_PORT[name]}={port}'
        for name, port in ports.items()
    ]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def _k8s_api_request(
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None) -> Tuple[int, bytes]:
    api_host = os.environ['KUBERNETES_SERVICE_HOST']
    api_port = os.environ['KUBERNETES_SERVICE_PORT']
    with open(_SA_TOKEN_PATH, encoding='utf-8') as f:
        token = f.read().strip()
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json',
    }
    data: bytes = b''
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    url = f'https://{api_host}:{api_port}{path}'
    ctx = ssl.create_default_context(cafile=_SA_CA_PATH)
    req = urllib.request.Request(url,
                                 data=data if data else None,
                                 headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _build_configmap_body(name: str, namespace: str, ports: Dict[str, int],
                          owner_pod_name: str,
                          owner_pod_uid: str) -> Dict[str, Any]:
    """Build the ConfigMap body, including the ownerReference that ties its
    lifetime to the head pod so K8s garbage-collects it on `sky down`."""
    return {
        'apiVersion': 'v1',
        'kind': 'ConfigMap',
        'metadata': {
            'name': name,
            'namespace': namespace,
            'labels': {
                'parent': 'skypilot',
                'skypilot-ray-ports': 'true',
            },
            # When the head pod is deleted (sky down deletes pods), the
            # K8s garbage collector cascades and removes this ConfigMap.
            # controller=false because we're not actually controlling
            # the pod; blockOwnerDeletion=false so deleting the pod
            # doesn't wait on our cleanup.
            'ownerReferences': [{
                'apiVersion': 'v1',
                'kind': 'Pod',
                'name': owner_pod_name,
                'uid': owner_pod_uid,
                'controller': False,
                'blockOwnerDeletion': False,
            }],
        },
        'data': {k: str(v) for k, v in ports.items()},
    }


def _publish_configmap(name: str, namespace: str, ports: Dict[str,
                                                              int]) -> None:
    """Create or update a ConfigMap with the head's chosen ports.

    Idempotent: if the ConfigMap exists (head pod restarted with the same
    UID), PUT replaces its data. If a stale ConfigMap exists from a prior
    head pod (different UID — owner already gone but the GC sweep hasn't
    fired yet), we refresh its ownerReference to the current head pod so
    cleanup catches it.
    """
    # SKYPILOT_POD_NAME / SKYPILOT_POD_UID are injected by the K8s
    # downward API in kubernetes-ray.yml.j2. We can't use $HOSTNAME here
    # because under hostNetwork: true the container inherits the host
    # node's hostname (e.g. gke-*-pool-*), not the pod's name.
    owner_pod_name = os.environ['SKYPILOT_POD_NAME']
    owner_pod_uid = os.environ['SKYPILOT_POD_UID']
    body = _build_configmap_body(name, namespace, ports, owner_pod_name,
                                 owner_pod_uid)
    base = f'/api/v1/namespaces/{namespace}/configmaps'
    status, resp = _k8s_api_request('POST', base, body)
    if status == 409:
        # Already exists; fetch resourceVersion and PUT to update.
        status, resp = _k8s_api_request('GET', f'{base}/{name}')
        if status >= 300:
            raise RuntimeError(
                f'Failed to GET ConfigMap {namespace}/{name} for update: '
                f'status={status} body={resp.decode("utf-8", "replace")}')
        existing = json.loads(resp)
        body['metadata']['resourceVersion'] = (
            existing['metadata']['resourceVersion'])
        status, resp = _k8s_api_request('PUT', f'{base}/{name}', body)
    if status >= 300:
        raise RuntimeError(
            f'Failed to publish ConfigMap {namespace}/{name}: '
            f'status={status} body={resp.decode("utf-8", "replace")}')


def _read_configmap_with_retry(name: str, namespace: str) -> Dict[str, str]:
    """Poll a ConfigMap until it exists, returning its ``data`` field.

    Workers call this before invoking ``ray start --address`` — the
    polling doubles as the "head is up" sync barrier, replacing the
    constant-port-only ``nc -z`` loop that the template uses without
    hostNetwork.
    """
    base = f'/api/v1/namespaces/{namespace}/configmaps/{name}'
    deadline = time.monotonic() + _CONFIGMAP_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        status, resp = _k8s_api_request('GET', base)
        if status == 200:
            body = json.loads(resp)
            return body.get('data', {})
        if status != 404:
            raise RuntimeError(
                f'Unexpected status {status} reading ConfigMap '
                f'{namespace}/{name}: {resp.decode("utf-8", "replace")}')
        time.sleep(_CONFIGMAP_POLL_INTERVAL_S)
    raise TimeoutError(
        f'ConfigMap {namespace}/{name} did not appear within '
        f'{_CONFIGMAP_POLL_TIMEOUT_S}s — is the head pod healthy?')


def _run_head(env_file: str, configmap_name: str,
              configmap_namespace: str) -> None:
    held, ports = _probe_ports(_HEAD_PORT_NAMES)
    _publish_configmap(configmap_name, configmap_namespace, ports)
    # Write env file after ConfigMap publish so that if publish fails,
    # ray start doesn't run with ports that workers will never learn
    # about.
    _write_env_file(ports, env_file)
    # Hand off: held sockets close on process exit.
    del held


def _wait_head_gcs_tcp(host: str, port: int) -> None:
    """Block until the head's GCS port answers on TCP.

    Without this the worker can dial through the configured DNS to the
    head and find that Ray hasn't quite finished binding yet —
    ``ray start --address`` would then fail. The non-hostNetwork path
    uses ``nc -z`` in the K8s template for the same purpose, against a
    constant port.
    """
    deadline = time.monotonic() + _HEAD_GCS_TCP_WAIT_TIMEOUT_S
    last_err: Exception = None  # type: ignore[assignment]
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


def _run_worker(env_file: str, configmap_name: str,
                configmap_namespace: str) -> None:
    head_data = _read_configmap_with_retry(configmap_name, configmap_namespace)
    head_gcs = head_data.get('gcs')
    if head_gcs is None:
        raise RuntimeError(
            f'ConfigMap {configmap_namespace}/{configmap_name} is missing '
            f'the "gcs" key. Data: {head_data}')
    # SKYPILOT_RAY_PORT (the env var for 'gcs') carries the head's GCS
    # port — the address this worker's raylet will connect to. Worker's
    # own local-bind ports are probed below.
    held, ports = _probe_ports(_WORKER_PORT_NAMES)
    ports['gcs'] = int(head_gcs)
    _write_env_file(ports, env_file)
    del held
    # SKYPILOT_RAY_HEAD_IP is exported by the K8s template before this
    # script runs; if absent (e.g. under SSH-fallback re-provisioning)
    # we skip the wait — ray start's own retries take over.
    head_ip = os.environ.get('SKYPILOT_RAY_HEAD_IP')
    if head_ip:
        _wait_head_gcs_tcp(head_ip, int(head_gcs))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--mode', choices=['head', 'worker'], required=True)
    parser.add_argument('--env-file',
                        required=True,
                        help='Path to write `export VAR=value` lines to.')
    parser.add_argument('--configmap-name', required=True)
    parser.add_argument('--configmap-namespace', required=True)
    args = parser.parse_args(argv)
    if args.mode == 'head':
        _run_head(args.env_file, args.configmap_name, args.configmap_namespace)
    else:
        _run_worker(args.env_file, args.configmap_name,
                    args.configmap_namespace)
    return 0


if __name__ == '__main__':
    sys.exit(main())
