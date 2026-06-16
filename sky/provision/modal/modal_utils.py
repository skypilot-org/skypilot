"""Modal provisioning utilities."""

import shlex
from typing import Any, Dict, Optional, Tuple

from sky.adaptors import modal as modal_adaptor

APP_NAME = 'skypilot-modal'
SSH_PORT = 22
SSH_USER = 'root'

_TUNNEL_TIMEOUT_SECONDS = 180


def get_app(create_if_missing: bool):
    return modal_adaptor.modal.App.lookup(APP_NAME,
                                          create_if_missing=create_if_missing)


def get_image():
    return (modal_adaptor.modal.Image.debian_slim(
        python_version='3.12').apt_install('openssh-server', 'sudo', 'rsync',
                                           'curl', 'procps', 'patch', 'lsof'))


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


def sandbox_status(sandbox) -> Optional[int]:
    return sandbox.poll()
