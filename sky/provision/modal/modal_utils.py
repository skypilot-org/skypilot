"""Modal provisioning utilities."""

import os
import shlex
import sys
import time
from typing import Any, Callable, Dict, List, Optional
import urllib.parse

from sky.adaptors import aws
from sky.adaptors import cloudflare
from sky.adaptors import modal as modal_adaptor
from sky.provision import common

SSH_PORT = 22
SSH_USER = 'root'
TOKEN_ID_ENV_VAR = 'MODAL_TOKEN_ID'
TOKEN_SECRET_ENV_VAR = 'MODAL_TOKEN_SECRET'

# Prefix for the per-cluster Modal App and Dict. Modal object names are at
# most 64 characters and Modal cluster names at most 60.
_NAME_PREFIX = 'sky-'
_NODE_KEY_PREFIX = 'node-'
_CALL_ID_KEY = 'call_id'
_NUM_NODES_KEY = 'num_nodes'
_PREEMPTED_KEY = 'preempted'
# GPU capacity can take a while to schedule; give up after this long.
_NODES_READY_TIMEOUT_SECONDS = 15 * 60
_POLL_INTERVAL_SECONDS = 1
# The node function is shipped with cloudpickle (`serialized=True`), so the
# container must run the same Python minor version as this process.
_IMAGE_PYTHON_VERSION = f'{sys.version_info.major}.{sys.version_info.minor}'
_IMAGE_SYSTEM_PACKAGES = ('openssh-server', 'sudo', 'rsync', 'curl', 'procps',
                          'patch', 'lsof')
_GCS_HMAC_ACCESS_KEY_ENV_VAR = 'GOOGLE_ACCESS_KEY_ID'
_GCS_HMAC_SECRET_KEY_ENV_VAR = 'GOOGLE_ACCESS_KEY_SECRET'


def get_image(docker_image: Optional[str] = None):
    if docker_image is None:
        image = modal_adaptor.modal.Image.debian_slim(
            python_version=_IMAGE_PYTHON_VERSION)
    else:
        image = modal_adaptor.modal.Image.from_registry(
            docker_image, add_python=_IMAGE_PYTHON_VERSION)
    return image.apt_install(*_IMAGE_SYSTEM_PACKAGES)


def get_modal_env_secret():
    """Return a Modal Secret carrying env-token credentials, if configured."""
    token_id = os.environ.get(TOKEN_ID_ENV_VAR)
    token_secret = os.environ.get(TOKEN_SECRET_ENV_VAR)
    if not token_id or not token_secret:
        return None
    return modal_adaptor.modal.Secret.from_dict({
        TOKEN_ID_ENV_VAR: token_id,
        TOKEN_SECRET_ENV_VAR: token_secret,
    })


def _get_s3_secret(region: Optional[str]):
    credentials = aws.session().get_credentials().get_frozen_credentials()
    env_dict = {
        'AWS_ACCESS_KEY_ID': credentials.access_key,
        'AWS_SECRET_ACCESS_KEY': credentials.secret_key,
    }
    token = getattr(credentials, 'token', None)
    if token:
        env_dict['AWS_SESSION_TOKEN'] = token
    if region:
        env_dict['AWS_REGION'] = region
    return modal_adaptor.modal.Secret.from_dict(env_dict)


def _get_r2_secret():
    credentials = cloudflare.get_r2_credentials(cloudflare.session())
    return modal_adaptor.modal.Secret.from_dict({
        'AWS_ACCESS_KEY_ID': credentials.access_key,
        'AWS_SECRET_ACCESS_KEY': credentials.secret_key,
    })


def _get_gcs_hmac_secret():
    access_key = os.environ.get(_GCS_HMAC_ACCESS_KEY_ENV_VAR)
    secret_key = os.environ.get(_GCS_HMAC_SECRET_KEY_ENV_VAR)
    if not access_key or not secret_key:
        raise RuntimeError(
            'Modal CloudBucketMount for GCS requires HMAC credentials in '
            f'{_GCS_HMAC_ACCESS_KEY_ENV_VAR} and '
            f'{_GCS_HMAC_SECRET_KEY_ENV_VAR}.')
    return modal_adaptor.modal.Secret.from_dict({
        _GCS_HMAC_ACCESS_KEY_ENV_VAR: access_key,
        _GCS_HMAC_SECRET_KEY_ENV_VAR: secret_key,
    })


def _get_cloud_bucket_secret(spec: Dict[str, Any]):
    store_type = spec['StoreType']
    if store_type == 'S3':
        return _get_s3_secret(spec.get('Region'))
    if store_type == 'R2':
        return _get_r2_secret()
    if store_type == 'GCS':
        return _get_gcs_hmac_secret()
    raise RuntimeError(f'Unsupported Modal CloudBucketMount store type: '
                       f'{store_type}')


def get_modal_volume_mounts(
        volume_specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build Modal Volume mount objects for the node Function."""
    volumes = {}
    for spec in volume_specs:
        volume = modal_adaptor.modal.Volume.from_name(
            spec['VolumeNameOnCloud'],
            environment_name=spec.get('EnvironmentName'),
            create_if_missing=False)
        sub_path = spec.get('SubPath')
        if sub_path:
            volume = volume.with_mount_options(sub_path=sub_path)
        volumes[spec['Path']] = volume
    return volumes


def get_cloud_bucket_mounts(
        bucket_specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build Modal CloudBucketMount objects for the node Function."""
    mounts = {}
    for spec in bucket_specs:
        mounts[spec['Path']] = modal_adaptor.modal.CloudBucketMount(
            bucket_name=spec['BucketName'],
            bucket_endpoint_url=spec.get('BucketEndpointUrl'),
            key_prefix=spec.get('KeyPrefix'),
            secret=_get_cloud_bucket_secret(spec),
            read_only=spec.get('ReadOnly', False),
            force_path_style=spec.get('ForcePathStyle', False))
    return mounts


def get_ssh_start_command(public_key: str) -> str:
    """Shell commands that configure sshd for key-based root login."""
    return f"""
set -eux
mkdir -p /run/sshd /root/.ssh
chmod 700 /root/.ssh
printf '%s\\n' {shlex.quote(public_key)} > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
ssh-keygen -A
sed -i 's/^#\\?PermitRootLogin .*/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/^#\\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
"""


def _make_node_function() -> Callable[..., None]:
    """Return the body that every node of a cluster runs.

    It is defined inside a function so that cloudpickle ships it by value:
    the container does not have SkyPilot installed. It must not reference
    module globals.
    """

    def skypilot_node(ssh_setup: str, ssh_port: int, state_name: str,
                      environment_name: Optional[str], clustered: bool,
                      user_ports: List[int]) -> None:
        # pylint: disable=import-outside-toplevel,redefined-outer-name
        import contextlib
        import subprocess

        import modal
        import modal.experimental

        if clustered:
            info = modal.experimental.get_cluster_info()
            rank = info.rank
            ips = list(info.container_ipv4_ips) or list(info.container_ips)
            internal_ip = ips[rank]
        else:
            rank, internal_ip = 0, '127.0.0.1'
        state = modal.Dict.from_name(state_name,
                                     environment_name=environment_name)
        node_key = f'node-{rank}'
        if state.get(node_key) is not None:
            # Modal reruns a preempted Function on the same input, in a new
            # container that has none of the runtime SkyPilot set up. End
            # the call so SkyPilot sees the cluster as terminated.
            state['preempted'] = True
            raise RuntimeError('Modal restarted this node after a preemption.')
        subprocess.run(['bash', '-c', ssh_setup], check=True)
        sshd = subprocess.Popen(
            ['/usr/sbin/sshd', '-D', '-e', '-p',
             str(ssh_port)])
        with contextlib.ExitStack() as stack:
            ssh_tunnel = stack.enter_context(
                modal.forward(ssh_port, unencrypted=True))
            port_urls = {}
            if rank == 0:
                for port in user_ports:
                    port_urls[port] = stack.enter_context(
                        modal.forward(port)).url
            host, port = ssh_tunnel.tcp_socket
            state[node_key] = {
                'ssh_host': host,
                'ssh_port': port,
                'internal_ip': internal_ip,
                'port_urls': port_urls,
            }
            returncode = sshd.wait()
        raise RuntimeError(f'sshd exited with code {returncode}.')

    return skypilot_node


def state_name(cluster_name_on_cloud: str) -> str:
    """Name of the cluster's Modal App and Dict."""
    return f'{_NAME_PREFIX}{cluster_name_on_cloud}'


def _get_state_dict(cluster_name_on_cloud: str,
                    environment_name: Optional[str],
                    create_if_missing: bool = False):
    return modal_adaptor.modal.Dict.from_name(
        state_name(cluster_name_on_cloud),
        environment_name=environment_name,
        create_if_missing=create_if_missing)


def _is_not_found(exc: Exception) -> bool:
    not_found_error = getattr(getattr(modal_adaptor.modal, 'exception', None),
                              'NotFoundError', None)
    return not_found_error is not None and isinstance(exc, not_found_error)


def delete_state(cluster_name_on_cloud: str,
                 environment_name: Optional[str]) -> None:
    modal_adaptor.modal.Dict.objects.delete(state_name(cluster_name_on_cloud),
                                            allow_missing=True,
                                            environment_name=environment_name)


def get_cluster_state(
        cluster_name_on_cloud: str,
        environment_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return the cluster's saved state, or None if it was never launched.

    The state has `call_id`, `num_nodes`, `preempted` and `nodes`, a dict from
    rank to the node's SSH tunnel, internal IP and port URLs.
    """
    try:
        items = dict(
            _get_state_dict(cluster_name_on_cloud, environment_name).items())
    except Exception as exc:  # pylint: disable=broad-except
        if _is_not_found(exc):
            return None
        raise RuntimeError(f'Failed to query Modal cluster '
                           f'{cluster_name_on_cloud!r}: {exc}') from exc
    call_id = items.get(_CALL_ID_KEY)
    if call_id is None:
        return None
    nodes = {
        int(key[len(_NODE_KEY_PREFIX):]): value
        for key, value in items.items()
        if isinstance(key, str) and key.startswith(_NODE_KEY_PREFIX)
    }
    return {
        'call_id': call_id,
        'num_nodes': items.get(_NUM_NODES_KEY, len(nodes)),
        'preempted': bool(items.get(_PREEMPTED_KEY, False)),
        'nodes': nodes,
    }


def call_is_running(call_id: str) -> bool:
    """Whether the cluster's Function call is still running.

    Raises on API errors instead of guessing, so a transient failure cannot
    make SkyPilot treat a live cluster as terminated.
    """
    call = modal_adaptor.modal.FunctionCall.from_id(call_id)
    graph = call.get_call_graph()
    if not graph:
        # Not scheduled yet.
        return True
    pending = modal_adaptor.modal.call_graph.InputStatus.PENDING
    return any(info.status == pending for info in graph)


def cancel_call(call_id: str) -> None:
    modal_adaptor.modal.FunctionCall.from_id(call_id).cancel(
        terminate_containers=True)


def instance_id(call_id: str, rank: int) -> str:
    return f'{call_id}-{rank}'


def launch_cluster(cluster_name_on_cloud: str, environment_name: Optional[str],
                   num_nodes: int, public_key: str, user_ports: List[int],
                   function_kwargs: Dict[str, Any]) -> str:
    """Start the cluster's Function call and return its ID.

    Each cluster gets its own detached ephemeral App, which Modal stops once
    the call ends.
    """
    modal = modal_adaptor.modal
    name = state_name(cluster_name_on_cloud)
    delete_state(cluster_name_on_cloud, environment_name)
    state = _get_state_dict(cluster_name_on_cloud,
                            environment_name,
                            create_if_missing=True)
    state.hydrate()
    clustered = num_nodes > 1
    body = _make_node_function()
    if clustered:
        body = modal.clustered(size=num_nodes)(body)
    app = modal.App(name)
    function = app.function(
        name='skypilot-node',
        serialized=True,
        # Modal reruns a failed call on fresh containers that SkyPilot did not
        # set up. Let SkyPilot handle recovery instead.
        retries=0,
        **function_kwargs)(body)
    with app.run(detach=True, environment_name=environment_name):
        call = function.spawn(get_ssh_start_command(public_key), SSH_PORT, name,
                              environment_name, clustered, user_ports)
    state[_NUM_NODES_KEY] = num_nodes
    state[_CALL_ID_KEY] = call.object_id
    return call.object_id


def wait_for_nodes(cluster_name_on_cloud: str, environment_name: Optional[str],
                   call_id: str, num_nodes: int) -> Dict[int, Dict[str, Any]]:
    """Wait until every node has published its SSH tunnel."""
    call = modal_adaptor.modal.FunctionCall.from_id(call_id)
    deadline = time.time() + _NODES_READY_TIMEOUT_SECONDS
    while True:
        state = get_cluster_state(cluster_name_on_cloud, environment_name)
        nodes = state['nodes'] if state is not None else {}
        if len(nodes) >= num_nodes:
            return nodes
        if not call_is_running(call_id):
            try:
                call.get(timeout=0)
                reason = 'it returned'
            except Exception as e:  # pylint: disable=broad-except
                reason = f'{type(e).__name__}: {e}'
            raise RuntimeError(f'Modal Function call {call_id} ended before '
                               f'all nodes were ready ({reason}).')
        if time.time() > deadline:
            raise RuntimeError(
                f'Timed out after {_NODES_READY_TIMEOUT_SECONDS}s waiting for '
                f'{num_nodes} Modal node(s); {len(nodes)} ready.')
        time.sleep(_POLL_INTERVAL_SECONDS)


def get_port_endpoints(port_urls: Dict[int, str],
                       ports: List[int]) -> Dict[int, common.Endpoint]:
    endpoints: Dict[int, common.Endpoint] = {}
    for port in ports:
        tunnel_url = port_urls.get(port)
        if tunnel_url is None:
            continue
        url = urllib.parse.urlparse(tunnel_url)
        host = url.hostname
        if host is None:
            continue
        endpoints[port] = common.HTTPSEndpoint(host=host,
                                               port=url.port,
                                               path=url.path.lstrip('/'))
    return endpoints
