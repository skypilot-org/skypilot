"""SSH Node Pool management core functionality."""
import os
from pathlib import Path
from typing import Any, Dict, List

import yaml


class SSHNodePoolManager:
    """Manager for SSH Node Pool configurations."""

    def __init__(self):
        self.config_path = Path.home() / '.sky' / 'ssh_node_pools.yaml'
        self.keys_dir = Path.home() / '.sky' / 'ssh_keys'
        self.keys_dir.mkdir(parents=True, exist_ok=True)

    def get_all_pools(self) -> Dict[str, Any]:
        """Read all SSH Node Pool configurations from YAML file."""
        if not self.config_path.exists():
            return {}

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            raise RuntimeError(
                f'Failed to read SSH Node Pool config: {e}') from e

    def save_all_pools(self, pools_config: Dict[str, Any]) -> None:
        """Write SSH Node Pool configurations to YAML file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(pools_config, f, default_flow_style=False)
        except Exception as e:
            raise RuntimeError(
                f'Failed to save SSH Node Pool config: {e}') from e

    def update_pools(self, pools_config: Dict[str, Any]) -> None:
        """Update SSH Node Pool configurations."""
        all_pools = self.get_all_pools()
        all_pools.update(pools_config)
        self.save_all_pools(all_pools)

    def add_or_update_pool(self, pool_name: str,
                           pool_config: Dict[str, Any]) -> None:
        """Add or update a single SSH Node Pool configuration."""
        # Validate pool configuration
        self._validate_pool_config(pool_config)

        all_pools = self.get_all_pools()
        all_pools[pool_name] = pool_config
        self.save_all_pools(all_pools)

    def delete_pool(self, pool_name: str) -> bool:
        """Delete a SSH Node Pool configuration."""
        all_pools = self.get_all_pools()
        if pool_name in all_pools:
            del all_pools[pool_name]
            self.save_all_pools(all_pools)
            return True
        return False

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

    def _validate_pool_config(self, config: Dict[str, Any]) -> None:
        """Validate SSH Node Pool configuration."""
        if 'hosts' not in config:
            raise ValueError('Pool configuration must include `hosts`')

        if not isinstance(config['hosts'], list) or not config['hosts']:
            raise ValueError('`hosts` must be a non-empty list')

        # Validate user field
        if not config.get('user', '').strip():
            raise ValueError('Pool configuration must include `user`')

        # Validate authentication - must have either identity_file or password
        if not config.get('identity_file') and not config.get('password'):
            raise ValueError('Pool configuration must include '
                             'either `identity_file` or `password`')


def get_all_pools() -> Dict[str, Any]:
    """Get all SSH Node Pool configurations."""
    manager = SSHNodePoolManager()
    return manager.get_all_pools()


def update_pools(pools_config: Dict[str, Any]) -> None:
    """Update SSH Node Pool configurations."""
    manager = SSHNodePoolManager()
    manager.update_pools(pools_config)


def delete_pool(pool_name: str) -> bool:
    """Delete a SSH Node Pool configuration."""
    manager = SSHNodePoolManager()
    return manager.delete_pool(pool_name)


def upload_ssh_key(key_name: str, key_content: str) -> str:
    """Upload SSH private key."""
    manager = SSHNodePoolManager()
    return manager.save_ssh_key(key_name, key_content)


def list_ssh_keys() -> List[str]:
    """List available SSH keys."""
    manager = SSHNodePoolManager()
    return manager.list_ssh_keys()
