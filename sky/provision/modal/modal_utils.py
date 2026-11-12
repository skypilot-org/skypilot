"""Modal provisioning utilities."""

import os
import shlex
import sys
import time
from typing import Any, Callable, Dict, List, Optional
import urllib.parse

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import aws
from sky.adaptors import cloudflare
from sky.adaptors import modal as modal_adaptor
from sky.provision import common
from sky.skylet import constants
from sky.utils import schemas

logger = sky_logging.init_logger(__name__)

SSH_PORT = 22
SSH_USER = 'root'
# Containers run as root.
_HOME_DIR = '/root'

# Prefix for the per-cluster Modal App and Dict. Modal object names are at
# most 64 characters and Modal cluster names at most 57.
_NAME_PREFIX = 'sky-'
_CONTAINER_ENV_PROFILE = '/etc/profile.d/modal_container_env.sh'
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


def _check_bucket_mount_credentials(store_type: str) -> None:
    """Whether the credentials a CloudBucketMount needs can be given to Modal.

    Modal mounts a bucket with credentials from a Modal Secret, which
    SkyPilot fills from the API server's own. Checked when the mount is
    planned, so the launch can fail over to another cloud.

    Raises:
        exceptions.ResourcesUnavailableError: they are missing, or must not
            leave the API server.
    """
    reason = None
    if store_type == 'S3':
        remote_identity = skypilot_config.get_effective_region_config(
            cloud='aws',
            region=None,
            keys=('remote_identity',),
            default_value=schemas.get_default_remote_identity('aws'))
        local_credentials = (
            schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value)
        if remote_identity != local_credentials:
            reason = ('`aws.remote_identity` is not LOCAL_CREDENTIALS, so the '
                      'AWS credentials must not be copied to a cluster.')
        elif not _has_aws_credentials():
            reason = 'no AWS credentials were found.'
    elif store_type == 'GCS':
        if not (os.environ.get(_GCS_HMAC_ACCESS_KEY_ENV_VAR) and
                os.environ.get(_GCS_HMAC_SECRET_KEY_ENV_VAR)):
            reason = ('GCS mounts need HMAC credentials in '
                      f'{_GCS_HMAC_ACCESS_KEY_ENV_VAR} and '
                      f'{_GCS_HMAC_SECRET_KEY_ENV_VAR}, set for the API '
                      'server.')
    if reason is not None:
        raise exceptions.ResourcesUnavailableError(
            f'Cannot mount a {store_type} bucket on Modal: {reason}')


def _has_aws_credentials() -> bool:
    try:
        return aws.session().get_credentials() is not None
    except Exception as e:  # pylint: disable=broad-except
        # botocore raises, rather than returning None, for some setups, e.g.
        # an expired SSO login.
        logger.debug(f'Failed to get AWS credentials: {e}')
        return False


def _get_s3_secret(region: Optional[str]):
    credentials = aws.session().get_credentials().get_frozen_credentials()
    env_dict = {
        'AWS_ACCESS_KEY_ID': credentials.access_key,
        'AWS_SECRET_ACCESS_KEY': credentials.secret_key,
    }
    token = getattr(credentials, 'token', None)
    if token:
        # The Secret is a copy made now, and a mount cannot be changed on a
        # running cluster.
        logger.warning('The AWS credentials are temporary (SSO or an assumed '
                       'role): the S3 mount on this Modal cluster stops '
                       'working when they expire. Use long-lived AWS '
                       'credentials for clusters that outlive them.')
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


def resolve_mount_path(path: str) -> str:
    """Absolute container path for a SkyPilot mount destination.

    Modal needs absolute mount paths. SkyPilot also allows `~/...` and, for
    storage mounts, paths relative to the remote workdir.
    """
    if path.startswith('/'):
        return path
    if path == '~' or path.startswith('~/'):
        return _HOME_DIR + path[1:]
    workdir = constants.SKY_REMOTE_WORKDIR
    if workdir.startswith('~/'):
        workdir = _HOME_DIR + workdir[1:]
    return f'{workdir}/{path}'


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


def get_cloud_bucket_mount_specs(storage_mounts: Optional[Dict[str, Any]],
                                 dryrun: bool = False) -> List[Dict[str, Any]]:
    """Modal CloudBucketMount specs for a task's MOUNT-mode storage mounts.

    They go into the cluster config (`CloudBucketMounts`), since Modal mounts
    buckets when the Function is launched.

    Raises:
        exceptions.ResourcesUnavailableError: Modal cannot mount one of them.
            Not NotSupportedError: this is called while the cluster config
            is written, where it would abort the launch instead of letting
            it fail over to another cloud.
    """
    if not storage_mounts:
        return []
    supported_store_types = {'S3', 'R2', 'GCS'}
    bucket_mounts = []
    for dst, storage_obj in storage_mounts.items():
        mode = getattr(storage_obj.mode, 'value', str(storage_obj.mode))
        if mode == 'COPY':
            continue
        if mode == 'MOUNT_CACHED':
            raise exceptions.ResourcesUnavailableError(
                'Modal CloudBucketMounts support storage mode MOUNT, but not '
                'MOUNT_CACHED.')
        if mode != 'MOUNT':
            continue
        if dryrun:
            continue
        storage_obj.construct()
        if not storage_obj.stores:
            raise exceptions.StorageExternalDeletionError(
                f'The bucket {storage_obj.name!r} could not be mounted on '
                'Modal. Please verify that the bucket exists.')
        # A storage can have several stores: use the first one Modal can
        # mount.
        stores = {
            getattr(store_type, 'value', str(store_type)): store
            for store_type, store in storage_obj.stores.items()
        }
        store_type_value = next(
            (name for name in stores if name in supported_store_types), None)
        if store_type_value is None:
            raise exceptions.ResourcesUnavailableError(
                'Modal CloudBucketMounts currently support S3, R2, and GCS '
                f'storage mounts. Got {", ".join(stores)}.')
        _check_bucket_mount_credentials(store_type_value)
        store = stores[store_type_value]
        if store is None:
            raise exceptions.StorageExternalDeletionError(
                f'The bucket {storage_obj.name!r} could not be mounted on '
                'Modal. Please verify that the bucket exists.')
        bucket = getattr(store, 'bucket', None)
        bucket_name = getattr(bucket, 'name', None) or storage_obj.name
        if bucket_name is None:
            raise exceptions.StorageSpecError(
                'Failed to resolve bucket name for Modal CloudBucketMount.')
        key_prefix = getattr(store, 'bucket_sub_path', None)
        if key_prefix:
            key_prefix = key_prefix.strip('/') + '/'
        endpoint_url = None
        if store_type_value == 'R2':
            endpoint_factory = getattr(getattr(store, 'config', None),
                                       'get_endpoint_url', None)
            if endpoint_factory is not None:
                endpoint_url = endpoint_factory()
        elif store_type_value == 'GCS':
            endpoint_url = 'https://storage.googleapis.com'
        read_only = bool(storage_obj.mount_config and
                         storage_obj.mount_config.read_only)
        bucket_mounts.append({
            'Path': resolve_mount_path(dst),
            'StoreType': store_type_value,
            'BucketName': bucket_name,
            'BucketEndpointUrl': endpoint_url,
            'KeyPrefix': key_prefix,
            'Region': getattr(store, 'region', None),
            'ReadOnly': read_only,
            'ForcePathStyle': False,
        })
    return bucket_mounts


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


def _export_container_env_command(
        profile_path: str = _CONTAINER_ENV_PROFILE) -> str:
    """Shell command that gives login shells the container's environment.

    An SSH session does not inherit it. Two things are lost without it:

    - what the image sets (Docker `ENV`: PATH, LD_LIBRARY_PATH, CUDA
      variables, ...), so setup and run commands would not see the image as
      `docker run` does. RunPod and Kubernetes export it the same way.
    - the identity Modal gives the container (its `MODAL_*` variables). With
      them the modal SDK in a session authenticates as the container, like
      code in any Modal Function. Autodown relies on it: skylet runs in such
      a session and has to cancel the cluster's call. The API server's own
      token is not needed on the cluster for that, and is not uploaded to it
      (`remote_identity` defaults to NO_UPLOAD for Modal).

    Left out: the variables that describe the exporting shell itself, and
    `/pkg/` on PYTHONPATH. That is Modal's client library for the
    container's own Python, and would shadow packages of the SkyPilot
    runtime and of the user's environment, which run other Pythons.
    """
    shell_variables = shlex.quote('^declare -x (PWD|OLDPWD|SHLVL|HOME|TERM|_)=')
    drop_pkg = shlex.quote('/^declare -x PYTHONPATH=/ s#/pkg/?:?##')
    return (f'{{ export -p | grep -v -E {shell_variables} | '
            f'sed -E {drop_pkg} || true; }} > {shlex.quote(profile_path)}')


def get_ssh_start_command(public_key: str) -> str:
    """Shell commands that configure sshd for key-based root login."""
    return f"""
set -eux
{_export_container_env_command()}
mkdir -p /run/sshd /root/.ssh
chmod 700 /root/.ssh
printf '%s\\n' {shlex.quote(public_key)} > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
ssh-keygen -A
sed -i 's/^#\\?PermitRootLogin .*/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/^#\\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
"""


def _make_node_function() -> Callable[..., str]:
    """Return the body that every node of a cluster runs.

    It is defined inside a function so that cloudpickle ships it by value:
    the container does not have SkyPilot installed. It must not reference
    module globals.

    A node returns rather than raises when it is done (sshd exited, Modal
    reran it after a preemption, or its setup failed), so `call_is_running`
    can tell an ended call from an API error.
    """

    # pylint: disable=redefined-outer-name
    def skypilot_node(ssh_setup: str, ssh_port: int, state_name: str,
                      environment_name: Optional[str], clustered: bool,
                      user_ports: List[int]) -> str:
        try:
            return run_node(ssh_setup, ssh_port, state_name, environment_name,
                            clustered, user_ports)
        except Exception as e:  # pylint: disable=broad-except
            # E.g. sshd could not be set up on a custom image. Returned, not
            # raised: the caller would have to unpickle the exception, and
            # could not tell it from an API error.
            return f'The node failed: {type(e).__name__}: {e}'

    def run_node(ssh_setup: str, ssh_port: int, state_name: str,
                 environment_name: Optional[str], clustered: bool,
                 user_ports: List[int]) -> str:
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
            return 'Modal restarted this node after a preemption.'
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
        return f'sshd exited with code {returncode}.'

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

    Polls the call's output: a running (or not yet scheduled) call has none.
    The call graph is not used, because Modal keeps reporting a finished
    call's input as pending and drops old inputs altogether.

    Anything else `get` raises is one of two things. Modal's own errors and
    transport errors are API errors: they propagate, so a transient failure
    cannot make SkyPilot treat a live cluster as terminated. Any other
    exception is the call's own, which Modal re-raises here: the node does
    not raise (see `_make_node_function`), but its container can fail
    before the node runs, e.g. when the function cannot be unpickled on a
    custom image. The call has ended then.
    """
    modal = modal_adaptor.modal
    ended_errors = (
        # Cancelled, or Modal reported the call as failed.
        modal.exception.RemoteError,
        # Modal lost the call's container.
        modal.exception.InternalFailure,
        # The call failed with something that cannot be unpickled here.
        modal.exception.ExecutionError,
        # Hit the Function timeout.
        modal.exception.FunctionTimeoutError,
        # Ended long enough ago for Modal to have dropped its output or
        # the call itself.
        modal.exception.OutputExpiredError,
        modal.exception.NotFoundError,
    )
    try:
        modal.FunctionCall.from_id(call_id).get(timeout=0)
    except TimeoutError:
        return True
    except ended_errors:
        return False
    except (modal.exception.Error, OSError):
        raise
    except Exception as e:  # pylint: disable=broad-except
        if type(e).__module__.split('.', maxsplit=1)[0] == 'grpclib':
            raise
        logger.debug(f'Modal Function call {call_id} ended with '
                     f'{type(e).__name__}: {e}')
        return False
    return False


def cancel_call(call_id: str) -> None:
    """Cancel a call and its containers; a no-op if it has already ended."""
    try:
        modal_adaptor.modal.FunctionCall.from_id(call_id).cancel(
            terminate_containers=True)
    except Exception as e:  # pylint: disable=broad-except
        # Modal drops a call some time after it ends.
        if not _is_not_found(e):
            raise


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
        # Record the call before anything else can fail: once recorded,
        # query_instances and terminate_instances can find it. Otherwise a
        # running call would be orphaned, billing until its timeout.
        try:
            state[_NUM_NODES_KEY] = num_nodes
            state[_CALL_ID_KEY] = call.object_id
        except BaseException:
            cancel_call(call.object_id)
            raise
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
                reason = f'it returned: {call.get(timeout=0)}'
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
