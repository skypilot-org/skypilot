"""Modal provisioning utilities."""

import os
import shlex
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

from sky.adaptors import aws
from sky.adaptors import cloudflare
from sky.adaptors import modal as modal_adaptor
from sky.provision import common

APP_NAME = 'skypilot-modal'
SSH_PORT = 22
SSH_USER = 'root'
TOKEN_ID_ENV_VAR = 'MODAL_TOKEN_ID'
TOKEN_SECRET_ENV_VAR = 'MODAL_TOKEN_SECRET'

_TUNNEL_TIMEOUT_SECONDS = 180
_IMAGE_PYTHON_VERSION = '3.12'
_IMAGE_SYSTEM_PACKAGES = ('openssh-server', 'sudo', 'rsync', 'curl', 'procps',
                          'patch', 'lsof')
_GCS_HMAC_ACCESS_KEY_ENV_VAR = 'GOOGLE_ACCESS_KEY_ID'
_GCS_HMAC_SECRET_KEY_ENV_VAR = 'GOOGLE_ACCESS_KEY_SECRET'


def get_app(create_if_missing: bool):
    return modal_adaptor.modal.App.lookup(APP_NAME,
                                          create_if_missing=create_if_missing)


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
    """Build Modal Volume mount objects for Sandbox.create(volumes=...)."""
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
    """Build Modal CloudBucketMount objects for Sandbox.create(volumes=...)."""
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
    return f"""
set -eux
mkdir -p /run/sshd /root/.ssh
chmod 700 /root/.ssh
printf '%s\\n' {shlex.quote(public_key)} > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
ssh-keygen -A
sed -i 's/^#\\?PermitRootLogin .*/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/^#\\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
exec /usr/sbin/sshd -D -e -p {SSH_PORT}
"""


def get_active_sandboxes_by_name(name: str) -> Dict[str, Any]:
    try:
        sandbox = modal_adaptor.modal.Sandbox.from_name(APP_NAME, name)
    except Exception:  # pylint: disable=broad-except
        return {}
    return {sandbox.object_id: sandbox}


def get_head_sandbox(cluster_name_on_cloud: str):
    sandboxes = get_active_sandboxes_by_name(cluster_name_on_cloud)
    if not sandboxes:
        return None
    return next(iter(sandboxes.values()))


def get_ssh_tunnel(sandbox) -> Tuple[str, int]:
    tunnels = sandbox.tunnels(timeout=_TUNNEL_TIMEOUT_SECONDS)
    tunnel = tunnels[SSH_PORT]
    return tunnel.tcp_socket


def get_port_tunnels(sandbox, ports: List[int]) -> Dict[int, common.Endpoint]:
    if not ports:
        return {}
    tunnels = sandbox.tunnels(timeout=_TUNNEL_TIMEOUT_SECONDS)
    endpoints: Dict[int, common.Endpoint] = {}
    for port in ports:
        tunnel = tunnels.get(port)
        if tunnel is None:
            continue
        url = urllib.parse.urlparse(tunnel.url)
        host = url.hostname
        if host is None:
            continue
        endpoints[port] = common.HTTPSEndpoint(host=host,
                                               port=url.port,
                                               path=url.path.lstrip('/'))
    return endpoints


def sandbox_status(sandbox) -> Optional[int]:
    return sandbox.poll()
