"""SSH Node Pool management core functionality."""
import colorama
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sky import clouds
from sky import sky_logging
from sky.ssh_node_pools import constants
from sky.ssh_node_pools import models
from sky.ssh_node_pools import state
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import rich_utils
from sky.utils import ux_utils
from sky.utils.kubernetes import deploy_ssh_cluster

logger = sky_logging.init_logger(__name__)


@usage_lib.entrypoint
def ssh_up(infra: Optional[str] = None, cleanup: bool = False) -> None:
    """Deploys or tears down a Kubernetes cluster on SSH targets.

    Args:
        infra: Name of the cluster configuration to deploy/cleanup.
            If None, the all configurations are used. This value
            should not be none when deploying.
        cleanup: If True, clean up the cluster instead of deploying.
    """
    assert cleanup or infra is not None

    action = 'Cleaning up' if cleanup else 'Deploying'
    msg = f'{action} SSH Node Pool(s)...'

    with rich_utils.safe_status(ux_utils.spinner_message(msg)):
        success, reason = deploy_ssh_cluster.deploy_cluster(cleanup=cleanup,
                                                            infra=infra)

    if not success:
        with ux_utils.print_exception_no_traceback():
            action = 'cleanup' if cleanup else 'deploy'
            raise RuntimeError(f'Failed to {action} SkyPilot '
                               f'on some Node Pools. {reason}')
    else:
        logger.info('')
        if cleanup:
            logger.info(ux_utils.finishing_message(
                    '🎉 SSH Node Pools cleaned up successfully.'))
        else:
            logger.info(
                ux_utils.finishing_message(
                    f'🎉 SSH Node Pool `{infra}` set up successfully. ',
                    follow_up_message=(
                        f'Run `{colorama.Style.BRIGHT}'
                        f'sky check ssh'
                        f'{colorama.Style.RESET_ALL}` to verify access, '
                        f'`{colorama.Style.BRIGHT}sky launch --infra ssh'
                        f'{colorama.Style.RESET_ALL}` to launch a cluster. ')))


@usage_lib.entrypoint
def ssh_status(context_name: str) -> Tuple[bool, str]:
    """Check the status of an SSH Node Pool context.

    Args:
        context_name: The SSH context name (e.g., 'ssh-my-cluster')

    Returns:
        Tuple[bool, str]: (is_ready, reason)
            - is_ready: True if the SSH Node Pool is ready, False otherwise
            - reason: Explanation of the status
    """
    try:
        is_ready, reason = clouds.SSH.check_single_context(context_name)
        return is_ready, reason
    except Exception as e:  # pylint: disable=broad-except
        return False, ('Failed to check SSH context: '
                       f'{common_utils.format_exception(e)}')


def get_all_clusters() -> List[models.SSHCluster]:
    """Get all SSH Node Pool configurations."""
    return state.get_all_clusters()


def _validate_pool_config(config: Dict[str, Any]) -> None:
    """Validate SSH Node Pool configuration."""
    def _validate_field(data: dict, field: str, expected: type):
        if field not in data:
            raise ValueError(f'Pool configuration must include `{field}`')
        if not isinstance(data[field], expected):
            raise ValueError(f'Pool configuration field {field} must be '
                                f'of type {expected.__name__}, got '
                                f'{type(data[field].__name__)}')

    _validate_field(config, 'hosts', list)
    if not config['hosts']:
        raise ValueError('`hosts` must be a non-empty list')

    # TODO(kyuds): stricter validation (eg: non-empty ip?)
    for host_config in config['hosts']:
        if not isinstance(host_config, dict):
            raise ValueError('Each host configuration must be a dictionary, '
                                f'got {type(host_config).__name__}')
        _validate_field(host_config, 'user', str)
        _validate_field(host_config, 'ip', str)
        _validate_field(host_config, 'password', str)
        _validate_field(host_config, 'identity_file', str)
        _validate_field(host_config, 'use_ssh_config', bool)


def update_pool(pool_config: Dict[str, Any]) -> None:
    """Update a SSH Node Pool configuration."""
    if len(pool_config.keys()) != 1:
        raise ValueError('Pool configuration must have exactly one '
                         'SSH Node Pool configuration.')
    infra, config = next(iter(pool_config.items()))
    _validate_pool_config(config)
    nodes = [models.SSHNode.from_dict(d) for d in config['hosts']]

    updating_cluster = state.get_cluster(infra)
    if updating_cluster is not None:
        # there is a pre-existing ssh cluster
        logger.debug(f'Updating ssh cluster config: {infra}')
        updating_cluster.set_update_nodes(nodes)
    else:
        logger.debug(f'Creating new ssh cluster config: {infra}')
        updating_cluster = models.SSHCluster()
        updating_cluster.name = infra
        updating_cluster.set_head_node_ip(nodes[0].ip)
        updating_cluster.set_update_nodes(nodes)
    updating_cluster.status = models.SSHClusterStatus.PENDING
    state.add_or_update_cluster(updating_cluster)


class SSHKeyManager:
    """Manager for SSH Node Pool Key Files"""

    def __init__(self):
        self.keys_dir = Path(os.path.expanduser(constants.SKYSSH_KEY_DIR))
        self.keys_dir.mkdir(parents=True, exist_ok=True)

    def save_ssh_key(self, key_name: str, key_content: str) -> str:
        """Save SSH private key to ~/.sky/ssh_keys/ directory."""
        # Validate key name
        if not key_name or '/' in key_name or key_name.startswith('.'):
            raise ValueError('Invalid key name')

        key_path = self.keys_dir / key_name
        try:
            with open(key_path, 'w', encoding='utf-8') as f:
                f.write(key_content)
            os.chmod(key_path, 0o600)  # Set secure permissions
            return str(key_path)
        except Exception as e:
            raise RuntimeError(f'Failed to save SSH key: {e}') from e

    def list_ssh_keys(self) -> List[str]:
        """List available SSH key files."""
        if not self.keys_dir.exists():
            return []
        try:
            return [f.name for f in self.keys_dir.iterdir() if f.is_file()]
        except Exception:  # pylint: disable=broad-except
            return []

    def remove_ssh_key_by_path(self, key_path: str):
        """Remove SSH key file by path.
        Will silently proceed if file doesn't exist."""
        Path(key_path).unlink(missing_ok=True)

    def remove_ssh_key_by_name(self, key_name: str):
        """Remove SSH key file by name.
        Will silently proceed if file doesn't exist."""
        key_path = self.keys_dir / key_name
        key_path.unlink(missing_ok=True)


def upload_ssh_key(key_name: str, key_content: str) -> str:
    """Upload SSH private key."""
    manager = SSHKeyManager()
    return manager.save_ssh_key(key_name, key_content)


def list_ssh_keys() -> List[str]:
    """List available SSH keys."""
    manager = SSHKeyManager()
    return manager.list_ssh_keys()
