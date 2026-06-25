"""Modal SDK helpers for SkyPilot provisioner."""
from typing import Any, Dict, Optional

from sky import sky_logging
from sky.adaptors import modal as modal_adaptor

logger = sky_logging.init_logger(__name__)

SANDBOX_SSH_TIMEOUT = 50  # seconds for sandbox.tunnels() to become available
SANDBOX_TIMEOUT_SECONDS = 86400  # 24h — NEVER the 300s default


def _build_setup_cmd(public_key: str) -> str:
    """Builds the sshd setup + sleep-forever entrypoint command.

    Mirrors sky/provision/runpod/utils.py:launch() setup_cmd pattern.
    The public key is the only interpolated value; it is an SSH public key
    (no shell metacharacters). Never log the key value (T-03-01).
    """
    return (
        'apt-get update -qq && '
        'DEBIAN_FRONTEND=noninteractive apt-get install -y '
        # `sudo` is required: SkyPilot's runtime setup commands invoke `sudo`,
        # but Modal's debian_slim image runs as root without it installed.
        'openssh-server rsync curl patch sudo && '
        'mkdir -p /var/run/sshd /root/.ssh && '
        'chmod 700 /root/.ssh && '
        f'echo "{public_key}" >> /root/.ssh/authorized_keys && '
        'chmod 600 /root/.ssh/authorized_keys && '
        'sed -i "s/#PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config; '
        'sed -i "s/PermitRootLogin prohibit-password/PermitRootLogin yes/" '
        '/etc/ssh/sshd_config; '
        'cd /etc/ssh && ssh-keygen -A -q && '
        '/usr/sbin/sshd -D & '
        'export -p > /etc/profile.d/container_env_var.sh && '
        'sleep infinity')


def _get_image(modal: Any, image_id: Optional[str]) -> Any:
    """Returns the Modal Image for the Sandbox."""
    if image_id is None or image_id == 'default':
        return modal.Image.debian_slim().apt_install('openssh-server', 'rsync',
                                                     'curl', 'patch', 'sudo')
    # User-specified Docker image
    return modal.Image.from_registry(image_id).apt_install(
        'openssh-server', 'rsync', 'curl', 'patch', 'sudo')


def launch(cluster_name: str, node_type: str, instance_type: str, region: str,
           image_id: Optional[str], public_key: str, gpu_str: Optional[str],
           cpu: Optional[float], memory_mib: Optional[int]) -> str:
    """Creates a Modal Sandbox and returns sandbox.object_id as instance_id.

    CRITICAL: timeout=86400 (24h). Modal's default is 300s which kills the
    cluster before SkyPilot finishes bootstrapping Ray.
    """
    modal = modal_adaptor.modal
    name = f'{cluster_name}-{node_type}'
    app_name = f'skypilot-{cluster_name}'

    app = modal.App.lookup(app_name, create_if_missing=True)
    image = _get_image(modal, image_id)
    setup_cmd = _build_setup_cmd(public_key)

    sandbox = modal.Sandbox.create(
        'bash',
        '-c',
        setup_cmd,
        app=app,
        name=name,
        image=image,
        gpu=gpu_str,  # None for CPU-only; e.g. "H100" or "H100:4"
        cpu=cpu,
        memory=memory_mib,
        region=region if region and region != 'us' else None,
        timeout=SANDBOX_TIMEOUT_SECONDS,  # 86400, never the 300s default
        idle_timeout=None,  # keep alive even when sshd is idle
        unencrypted_ports=[22],  # expose SSH as TCP tunnel
        tags={
            'skypilot-cluster': cluster_name,
            'skypilot-node': node_type,
        },
    )

    # Acquire tunnel endpoint — blocks until sshd is reachable (up to 50s).
    try:
        tunnels = sandbox.tunnels(timeout=SANDBOX_SSH_TIMEOUT)
    except modal.exception.SandboxTimeoutError:
        sandbox.terminate()
        raise RuntimeError(
            f'Modal Sandbox {name} did not expose SSH tunnel within '
            f'{SANDBOX_SSH_TIMEOUT}s. Check image has openssh-server.')

    if 22 not in tunnels:
        sandbox.terminate()
        raise RuntimeError(f'Modal Sandbox {name}: port 22 not in tunnels. '
                           f'Available ports: {list(tunnels.keys())}')

    ssh_host, ssh_port = tunnels[22].tcp_socket
    logger.info('Modal Sandbox %s SSH tunnel: %s:%s', name, ssh_host, ssh_port)
    return sandbox.object_id


def list_instances(cluster_name_on_cloud: str,
                   query_tunnels: bool = True) -> Dict[str, Dict[str, Any]]:
    """Returns {sandbox_object_id: {status, name, external_ip, ssh_port}}.

    Filters by cluster tag. When ``query_tunnels`` is True (the default,
    required by ``get_cluster_info`` per D-03) a fresh Sandbox object is
    fetched per sandbox to live-query the SSH tunnel endpoint. Status-only
    callers (e.g. ``query_instances``) pass ``query_tunnels=False`` to skip
    that slow per-sandbox network round-trip; their ``external_ip``/``ssh_port``
    are returned as None.
    """
    modal = modal_adaptor.modal
    result: Dict[str, Dict[str, Any]] = {}
    try:
        sandbox_list = list(
            modal.Sandbox.list(
                tags={'skypilot-cluster': cluster_name_on_cloud}))
    except modal.exception.NotFoundError:
        return {}
    except Exception:  # pylint: disable=broad-except
        return {}

    for sandbox in sandbox_list:
        # Modal Sandbox objects expose no `.name`; the head/worker role is
        # carried in the `skypilot-node` tag set at create time. Reconstruct a
        # `<cluster>-<role>` name so `_get_head_instance_id` (which checks
        # `name.endswith('-head')`) keeps working.
        try:
            tags = sandbox.get_tags()
        except Exception:  # pylint: disable=broad-except
            tags = {}
        node_role = tags.get('skypilot-node', 'head')
        ssh_host, ssh_port = None, None
        if query_tunnels:
            # Force a fresh Sandbox object for tunnel re-query (D-03): a new
            # object resets `_tunnels=None`, so `.tunnels()` triggers a live
            # lookup rather than returning a stale cached endpoint.
            fresh = modal.Sandbox.from_id(sandbox.object_id)
            try:
                tunnels = fresh.tunnels(timeout=10)
            except Exception:  # pylint: disable=broad-except
                tunnels = {}
            if 22 in tunnels:
                ssh_host, ssh_port = tunnels[22].tcp_socket
        result[sandbox.object_id] = {
            'status': 'RUNNING',  # list() only returns live sandboxes
            'name': f'{cluster_name_on_cloud}-{node_role}',
            'external_ip': ssh_host,
            'ssh_port': ssh_port,
        }
    return result


def remove(instance_id: str) -> None:
    """Terminates a Modal Sandbox by object_id."""
    modal = modal_adaptor.modal
    modal.Sandbox.from_id(instance_id).terminate()
