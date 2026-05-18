"""Free-port probe and ConfigMap publish/discover for hostNetwork pods.

When a K8s pod has ``hostNetwork: true`` it shares the host's network
namespace, so a sibling SkyPilot pod scheduled to the same node would
collide on Ray's default ports. This script binds ephemeral sockets to
pick a free port set for the local Ray daemon, then either publishes
them (head) to a ``<cluster>-ray-ports`` ConfigMap or reads the head's
port from that ConfigMap (worker). Stdlib-only so it can run during the
window when the pod's skypilot install is in flux.

It also assigns each pod a deterministic 127.X.Y.Z loopback IP derived
from its pod name and exports it as ``SKYPILOT_RAY_NODE_IP``. Ray binds
its raylet on that IP, which disambiguates the (NodeID -> endpoint)
lookup when two raylets are otherwise co-located on the same host IP.
Without this, Ray's gRPC client cache can collapse both NodeIDs onto a
single endpoint and route every lease request to the wrong raylet.
"""
import argparse
import functools
import hashlib
import json
import os
import re
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

# ConfigMap key carrying the head pod's loopback IP. Workers read it to
# dial the head's GCS over the shared loopback (same K8s node only).
HEAD_NODE_IP_KEY = 'head_node_ip'

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

# Per-request budget for K8s API calls. Without this, urlopen would
# block forever on a hung API server and freeze the pod bootstrap.
_K8S_API_TIMEOUT_S = 10


@functools.lru_cache(maxsize=None)
def _api_auth_token() -> str:
    with open(_SA_TOKEN_PATH, encoding='utf-8') as f:
        return f.read().strip()


@functools.lru_cache(maxsize=None)
def _api_ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=_SA_CA_PATH)


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


def _write_env_file(ports: Dict[str, int],
                    path: str,
                    extra: Optional[Dict[str, str]] = None) -> None:
    lines = [
        f'export {_ENV_VAR_FOR_PORT[name]}={port}'
        for name, port in ports.items()
    ]
    for key, value in (extra or {}).items():
        lines.append(f'export {key}={value}')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


_POD_NAME_RANK_RE = re.compile(r'^(.*)-(head|worker(\d+))$')


def loopback_ip_for_pod(pod_name: str) -> str:
    """Deterministic 127.X.Y.Z address per pod for ``--node-ip-address``.

    127.0.0.0/8 is wholly routed to ``lo`` by the Linux kernel, so the
    address is bindable with no CAP_NET_ADMIN and reachable from any
    process sharing the netns (i.e. any other pod on the same K8s node
    under hostNetwork).

    Layout: ``127.<H1>.<H2>.<rank>`` where (H1, H2) come from a SHA-256
    of the cluster prefix (pod name minus the ``-head`` / ``-workerN``
    suffix) and ``rank`` is 0 for the head, N for ``-workerN``. Putting
    rank in the last byte makes IPs immediately readable in debug
    output ('head is .0, worker1 is .1'); the cluster-id bytes keep
    two concurrent SkyPilot clusters on the same K8s node from
    colliding. H1 is OR'd with 0x80 to stay out of the conventionally
    special 127.0.x.x range (in particular 127.0.0.1).
    """
    match = _POD_NAME_RANK_RE.match(pod_name)
    if match:
        cluster = match.group(1)
        rank = 0 if match.group(2) == 'head' else int(match.group(3))
    else:
        # Pod-name shape we don't recognize: hash the whole name as a
        # fallback. Better to stay deterministically distinct than to
        # fail provisioning.
        cluster = pod_name
        rank = 0
    digest = hashlib.sha256(cluster.encode('utf-8')).digest()
    return f'127.{digest[0] | 0x80}.{digest[1]}.{rank % 256}'


def _k8s_api_request(
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None) -> Tuple[int, bytes]:
    api_host = os.environ['KUBERNETES_SERVICE_HOST']
    api_port = os.environ['KUBERNETES_SERVICE_PORT']
    headers = {
        'Authorization': f'Bearer {_api_auth_token()}',
        'Accept': 'application/json',
    }
    data: bytes = b''
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    url = f'https://{api_host}:{api_port}{path}'
    req = urllib.request.Request(url,
                                 data=data if data else None,
                                 headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(
                req, context=_api_ssl_context(),
                timeout=_K8S_API_TIMEOUT_S) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _configmap_data_for_ports(podname: str, ports: Dict[str, int],
                              extra: Dict[str, str]) -> Dict[str, str]:
    """Translate probe port names into ConfigMap data keys.

    The 'sshd' port is rewritten to ``sshd_<podname>`` because every pod
    (head + each worker) publishes its own sshd port into the same
    ConfigMap. Other keys are head-owned Ray ports and stay flat so the
    worker probe can look up the head's GCS by the bare key 'gcs'.
    Non-port head-owned values (e.g. the head's loopback IP) are passed
    in via ``extra`` and written through as-is.
    """
    out: Dict[str, str] = {}
    for name, port in ports.items():
        key = f'{SSHD_KEY_PREFIX}{podname}' if name == 'sshd' else name
        out[key] = str(port)
    out.update(extra)
    return out


def _build_configmap_body(name: str, namespace: str, ports: Dict[str, int],
                          extra: Dict[str, str], owner_pod_name: str,
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
        'data': _configmap_data_for_ports(owner_pod_name, ports, extra),
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


def _publish_configmap(name: str, namespace: str, ports: Dict[str, int],
                       extra: Dict[str, str]) -> None:
    """Create or update a ConfigMap with the head's chosen ports.

    Idempotent: on 409 we GET, lift the resourceVersion, and PUT — which
    also re-points the ownerReference at the current head pod (relevant
    on head-pod restart, where a stale ConfigMap may still be around).
    """
    # SKYPILOT_POD_NAME / SKYPILOT_POD_UID come from the K8s downward
    # API — $HOSTNAME is the host node's name under hostNetwork.
    owner_pod_name = os.environ['SKYPILOT_POD_NAME']
    owner_pod_uid = os.environ['SKYPILOT_POD_UID']
    body = _build_configmap_body(name, namespace, ports, extra, owner_pod_name,
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
    node_ip = loopback_ip_for_pod(os.environ['SKYPILOT_POD_NAME'])
    # Publish before writing the env file so a failed publish prevents
    # ray start from binding ports the workers will never discover.
    _publish_configmap(configmap_name,
                       configmap_namespace,
                       ports,
                       extra={HEAD_NODE_IP_KEY: node_ip})
    _write_env_file(ports, env_file, extra={'SKYPILOT_RAY_NODE_IP': node_ip})
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
    extra = {'SKYPILOT_RAY_NODE_IP': loopback_ip_for_pod(podname)}
    # Override SKYPILOT_RAY_HEAD_IP to the head's loopback so the worker
    # raylet's --address dials the head over 127.16.X.Y. Requires the
    # worker to be on the same K8s node as the head (the only case where
    # the IP-collision bug bites; cross-node hostNetwork pods have
    # naturally distinct host IPs).
    head_node_ip = head_data.get(HEAD_NODE_IP_KEY)
    if head_node_ip:
        extra['SKYPILOT_RAY_HEAD_IP'] = head_node_ip
    _write_env_file(ports, env_file, extra=extra)
    del held
    # Prefer the loopback IP we just decided to dial; fall back to
    # whatever SKYPILOT_RAY_HEAD_IP was set to before the probe ran.
    head_ip = head_node_ip or os.environ.get('SKYPILOT_RAY_HEAD_IP')
    if head_ip:
        _wait_head_gcs_tcp(head_ip, int(head_gcs))


def main(argv: Optional[List[str]] = None) -> int:
    # No description: __doc__ is stripped by source_utils.minify_python_source
    # before the script is inlined into the pod bootstrap.
    parser = argparse.ArgumentParser()
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
