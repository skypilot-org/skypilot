"""Modal provisioning utilities."""

import shlex
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

from sky.adaptors import modal as modal_adaptor
from sky.provision import common

APP_NAME = 'skypilot-modal'
SSH_PORT = 22
SSH_USER = 'root'

_TUNNEL_TIMEOUT_SECONDS = 180
_IMAGE_PYTHON_VERSION = '3.12'
_IMAGE_SYSTEM_PACKAGES = ('openssh-server', 'sudo', 'rsync', 'curl', 'procps',
                          'patch', 'lsof')


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
