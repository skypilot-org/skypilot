"""Free-port probe and ConfigMap publish/discover for hostNetwork pods.

When a K8s pod has ``hostNetwork: true`` it shares the host's network
namespace, so a sibling SkyPilot pod scheduled to the same node would
collide on Ray's default ports. This script binds ephemeral sockets to
pick a free port set for the local Ray daemon, then either publishes
them (head) to a ``<cluster>-ray-ports`` ConfigMap or reads the head's
port from that ConfigMap (worker). Stdlib-only so it can run during the
window when the pod's skypilot install is in flux.
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

# SKYPILOT_RAY_PORT is the head's GCS port; the worker template already
# uses that name to dial the head, so we reuse it rather than introduce
# a parallel SKYPILOT_RAY_GCS_PORT.
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
    # hostNetwork, so the pod must bind sshd to a probed port instead.
    'sshd': 'SKYPILOT_SSHD_PORT',
}

_HEAD_PORT_NAMES: List[str] = list(_ENV_VAR_FOR_PORT)

# Workers don't run GCS/dashboard/ray-client-server, but they DO run sshd.
_WORKER_PORT_NAMES: List[str] = [
    'node_manager',
    'object_manager',
    'dashboard_agent_listen',
    'runtime_env_agent',
    'metrics_export',
    'sshd',
]

# Public so the SkyPilot client (sky/provision/kubernetes/instance.py)
# can read the same key when assembling InstanceInfo.ssh_port.
SSHD_KEY_PREFIX = 'sshd_'

_SA_TOKEN_PATH = '/var/run/secrets/kubernetes.io/serviceaccount/token'
_SA_CA_PATH = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'

_CONFIGMAP_POLL_TIMEOUT_S = 600
_CONFIGMAP_POLL_INTERVAL_S = 2

# Bridges the gap between the head publishing its ConfigMap (just before
# ray binds) and ray actually accepting connections.
_HEAD_GCS_TCP_WAIT_TIMEOUT_S = 600
_HEAD_GCS_TCP_WAIT_INTERVAL_S = 1

# Bound on retries when a worker's ConfigMap merge races another worker
# (409 Conflict from stale resourceVersion).
_MERGE_RETRY_LIMIT = 5


def _bind_ephemeral_port() -> Tuple[socket.socket, int]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('0.0.0.0', 0))
    return s, s.getsockname()[1]


def _probe_ports(
        names: List[str]) -> Tuple[List[socket.socket], Dict[str, int]]:
    """Bind one ephemeral socket per name; caller must keep them alive
    until just before ``ray start`` rebinds the ports."""
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


def _configmap_data_for_ports(podname: str, ports: Dict[str,
                                                        int]) -> Dict[str, str]:
    """Translate probe port names into ConfigMap data keys.

    The 'sshd' port is rewritten to ``sshd_<podname>`` because every pod
    (head + each worker) publishes its own sshd port into the same
    ConfigMap. Other keys are head-owned Ray ports and stay flat so the
    worker probe can look up the head's GCS by the bare key 'gcs'.
    """
    out: Dict[str, str] = {}
    for name, port in ports.items():
        key = f'{SSHD_KEY_PREFIX}{podname}' if name == 'sshd' else name
        out[key] = str(port)
    return out


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
            # ownerReference ties the ConfigMap's lifetime to the head
            # pod so it's GC'd on sky down without explicit teardown.
            'ownerReferences': [{
                'apiVersion': 'v1',
                'kind': 'Pod',
                'name': owner_pod_name,
                'uid': owner_pod_uid,
                'controller': False,
                'blockOwnerDeletion': False,
            }],
        },
        'data': _configmap_data_for_ports(owner_pod_name, ports),
    }


def _format_api_error(action: str, name: str, namespace: str, status: int,
                      resp: bytes) -> str:
    body = resp.decode('utf-8', 'replace')
    return (f'Failed to {action} ConfigMap {namespace}/{name}: '
            f'status={status} body={body}')


def _get_configmap(name: str, namespace: str) -> Dict[str, Any]:
    """GET a ConfigMap, returning its parsed body. Raises on non-200."""
    base = f'/api/v1/namespaces/{namespace}/configmaps/{name}'
    status, resp = _k8s_api_request('GET', base)
    if status >= 300:
        raise RuntimeError(
            _format_api_error('GET', name, namespace, status, resp))
    return json.loads(resp)


def _publish_configmap(name: str, namespace: str, ports: Dict[str,
                                                              int]) -> None:
    """Create or update a ConfigMap with the head's chosen ports.

    Idempotent: on 409 we GET, lift the resourceVersion, and PUT — which
    also re-points the ownerReference at the current head pod (relevant
    on head-pod restart, where a stale ConfigMap may still be around).
    """
    # SKYPILOT_POD_NAME / SKYPILOT_POD_UID come from the K8s downward
    # API — $HOSTNAME is the host node's name under hostNetwork.
    owner_pod_name = os.environ['SKYPILOT_POD_NAME']
    owner_pod_uid = os.environ['SKYPILOT_POD_UID']
    body = _build_configmap_body(name, namespace, ports, owner_pod_name,
                                 owner_pod_uid)
    base = f'/api/v1/namespaces/{namespace}/configmaps'
    status, resp = _k8s_api_request('POST', base, body)
    if status == 409:
        existing = _get_configmap(name, namespace)
        body['metadata']['resourceVersion'] = (
            existing['metadata']['resourceVersion'])
        status, resp = _k8s_api_request('PUT', f'{base}/{name}', body)
    if status >= 300:
        raise RuntimeError(
            _format_api_error('publish', name, namespace, status, resp))


def _merge_sshd_port(name: str, namespace: str, podname: str,
                     port: int) -> None:
    """Merge ``sshd_<podname>: <port>`` into an existing ConfigMap.

    Workers call this after the head has published the ConfigMap so the
    SkyPilot client can read every pod's sshd port from one place.
    Retries on 409 (resourceVersion went stale — typically another
    worker won the merge race) up to a small bound.
    """
    base = f'/api/v1/namespaces/{namespace}/configmaps/{name}'
    key = f'{SSHD_KEY_PREFIX}{podname}'
    last_err: Optional[str] = None
    for _ in range(_MERGE_RETRY_LIMIT):
        existing = _get_configmap(name, namespace)
        data = dict(existing.get('data') or {})
        data[key] = str(port)
        existing['data'] = data
        status, resp = _k8s_api_request('PUT', base, existing)
        if status < 300:
            return
        last_err = _format_api_error('PUT', name, namespace, status, resp)
        if status != 409:
            break
    raise RuntimeError(
        f'Failed to merge {key} into ConfigMap {namespace}/{name} after '
        f'{_MERGE_RETRY_LIMIT} attempts: {last_err}')


def _read_configmap_with_retry(name: str, namespace: str) -> Dict[str, str]:
    """Poll a ConfigMap until it exists, returning its ``data`` field.

    Doubles as the "head is up" sync barrier for workers — the same
    role ``nc -z`` plays for non-hostNetwork bootstraps.
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


def _run_head(env_file: str, configmap_name: str,
              configmap_namespace: str) -> None:
    held, ports = _probe_ports(_HEAD_PORT_NAMES)
    # Publish before writing the env file so a failed publish prevents
    # ray start from binding ports the workers will never discover.
    _publish_configmap(configmap_name, configmap_namespace, ports)
    _write_env_file(ports, env_file)
    del held  # release the held sockets just before this process exits


def _run_worker(env_file: str, configmap_name: str,
                configmap_namespace: str) -> None:
    head_data = _read_configmap_with_retry(configmap_name, configmap_namespace)
    head_gcs = head_data.get('gcs')
    if head_gcs is None:
        raise RuntimeError(
            f'ConfigMap {configmap_namespace}/{configmap_name} is missing '
            f'the "gcs" key. Data: {head_data}')
    held, ports = _probe_ports(_WORKER_PORT_NAMES)
    # Publish before releasing the held sockets so a failed merge
    # aborts the bootstrap before sshd binds a port nothing can find.
    podname = os.environ['SKYPILOT_POD_NAME']
    _merge_sshd_port(configmap_name, configmap_namespace, podname,
                     ports['sshd'])
    ports['gcs'] = int(head_gcs)
    _write_env_file(ports, env_file)
    del held
    # SKYPILOT_RAY_HEAD_IP is exported by the K8s template before this
    # script runs; if absent we skip the wait and let ray start retry.
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
