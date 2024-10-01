"""Utils for VPN setup on instances."""
import os
from typing import Any, Dict, List, Optional

import requests

from sky import sky_logging
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)

class VPN:
    """Interface for VPN configuration."""

    def get_setup_command(self) -> str:
        """Get the command to setup the VPN on VM instances."""
        raise NotImplementedError

    def get_setup_env_vars(self) -> Dict[str, str]:
        """Get the environment variables to setup instances with the VPN.

        NOTE: This is used for launching another instance with the same VPN
        by SkyPilot, instead of setting up the VPN on that instance. Currently
        it is only used in SkyServe.
        """
        return {}

    def get_private_ip(self, hostname: str) -> str:
        """Get the private IP address from the hostname."""
        raise NotImplementedError

    def remove_host(self, hostname: str) -> None:
        """Remove a host from the VPN."""
        raise NotImplementedError

    def to_yaml_config(self) -> Dict[str, Any]:
        """Get the VPN configuration in YAML format."""
        return {}

    @staticmethod
    def from_backend_config(config: Dict[str, Any]) -> 'VPN':
        """Create a VPN configuration from the backend configuration."""
        raise NotImplementedError

    def to_backend_config(self) -> Dict[str, Any]:
        """Get the VPN configuration for the backend."""
        return {}


class TailscaleVPN(VPN):
    """Tailscale VPN configuration."""

    # pylint: disable=line-too-long
    _SETUP_COMMAND = (
        'curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/focal.noarmor.gpg | '
        'sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null; '
        'curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/focal.tailscale-keyring.list | '
        'sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null; '
        'sudo apt-get update > /dev/null 2>&1; '
        'sudo apt-get install tailscale -y > /dev/null 2>&1; '
        'sudo tailscale login --auth-key {tailscale_auth_key}')

    def __init__(
        self,
        auth_key: str,
        api_key: Optional[str] = None,
        network_name: Optional[str] = None,
        enable_api: bool = False,
    ) -> None:
        self._auth_key = auth_key
        self._api_key = api_key
        self._network_name = network_name
        self._enable_api = enable_api
        if not enable_api:
            logger.warning('TAILSCALE_API_KEY or TAILSCALE_NETWORK_NAME is'
                           ' not set. Tailscale API will be disabled. You'
                           ' may need to remove hosts manually.')
        elif not api_key or not network_name:
            raise ValueError('Both TAILSCALE_API_KEY and TAILSCALE_NETWORK_NAME'
                             ' must be set to enable the Tailscale API.')


    @staticmethod
    def from_env_vars() -> 'TailscaleVPN':
        # Parse Tailscale auth key from environment variable.
        # This is required for all Tailscale operations.
        auth_key = os.environ.get('TAILSCALE_AUTH_KEY')
        if not auth_key:
            raise ValueError('TAILSCALE_AUTH_KEY is not set.')

        # Parse Tailscale API key and network name
        api_key = os.environ.get('TAILSCALE_API_KEY')
        network_name = os.environ.get('TAILSCALE_NETWORK_NAME')
        enable_api = True
        if not api_key or not network_name:
            enable_api = False

        return TailscaleVPN(auth_key, api_key, network_name, enable_api)

    def get_setup_command(self) -> str:
        return self._SETUP_COMMAND.format(tailscale_auth_key=self._auth_key)

    def get_setup_env_vars(self) -> Dict[str, str]:
        env_config = {
            'TAILSCALE_AUTH_KEY': self._auth_key,
        }
        if self._enable_api:
            assert self._api_key and self._network_name
            env_config['TAILSCALE_API_KEY'] = self._api_key
            env_config['TAILSCALE_NETWORK_NAME'] = self._network_name
        return env_config

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get the authentication headers for the Tailscale API."""
        return {
            'Authorization': f'Bearer {self._api_key}',
        }

    def _get_device_id_from_hostname(self, hostname: str) -> Optional[str]:
        """Get the node ID from the hostname."""
        url_to_query = ('https://api.tailscale.com/api/v2/tailnet/'
                       f'{self._network_name}/devices')
        resp = requests.get(url_to_query, headers=self._get_auth_headers())
        all_devices_in_network = resp.json().get('devices', [])
        for device_info in all_devices_in_network:
            if device_info.get('hostname') == hostname:
                return device_info.get('id')
        return None

    def get_private_ip(self, hostname: str) -> str:
        """Get the private IP address from the hostname."""
        query_cmd = f'tailscale ip -4 {hostname}'
        rc, stdout, stderr = subprocess_utils.run_with_retries(
            query_cmd,
            max_retry=1000,
            retry_stderrs=['no such host', 'server misbehaving'])
        subprocess_utils.handle_returncode(
            rc,
            query_cmd,
            error_msg=('Failed to query private IP address'
                      f' of hostname {hostname}.'),
            stderr=stdout + stderr)
        return stdout.strip()

    def remove_host(self, hostname: str) -> None:
        """Remove a host from the VPN."""
        if not self._enable_api:
            logger.warning('API is not enabled. Skipping host removal.')
            return

        device_id = self._get_device_id_from_hostname(hostname)
        if not device_id:
            logger.warning(f'Could not find node ID for hostname {hostname}.'
                            ' Skipping host removal.')

        url_to_remove = f'https://api.tailscale.com/api/v2/device/{device_id}'
        resp = requests.delete(url_to_remove, headers=self._get_auth_headers())
        if resp.status_code != 200:
            logger.warning(f'Failed to remove host {hostname} from the VPN.'
                           f' Status code: {resp.status_code}.'
                           f' Response: {resp.text}')

    @staticmethod
    def from_backend_config(config: Dict[str, Any]) -> 'TailscaleVPN':
        if config.get('auth_key') is None:
            raise ValueError('Tailscale auth key is required in the configuration.')
        return TailscaleVPN(**config)


    def to_backend_config(self) -> Dict[str, Any]:
        return {
            'auth_key': self._auth_key,
            'api_key': self._api_key,
            'network_name': self._network_name,
            'enable_api': self._enable_api,
        }


class VPNConfig:
    """VPN configuration."""

    def __init__(
        self,
        vpn_configs: Dict[str, Any],
    ) -> None:
        self._vpn_configs = vpn_configs

    @staticmethod
    def from_yaml_config(config: Dict[str, Any]) -> 'VPNConfig':
        """Create a VPN specification from a YAML configuration."""
        vpn_configs = {}
        for vpn_name, vpn_config in config.items(): # pylint: disable=unused-variable
            if vpn_name == 'tailscale':
                vpn_configs[vpn_name] = TailscaleVPN.from_env_vars()
            else:
                raise ValueError(f'Unknown VPN configuration: {vpn_name}')
        return VPNConfig(vpn_configs)

    @staticmethod
    def from_backend_config(config: Dict[str, Any]) -> 'VPNConfig':
        """Create a VPN configuration from the backend configuration."""
        vpn_configs = {}
        for vpn_name, vpn_config in config.items():
            if vpn_name == 'tailscale':
                vpn_configs[vpn_name] = \
                    TailscaleVPN.from_backend_config(vpn_config)
            else:
                raise ValueError(f'Unknown VPN configuration: {vpn_name}')
        return VPNConfig(vpn_configs)

    def get_setup_commands(self) -> List[str]:
        """Get the command to setup the VPN on VM instances."""
        return [vpn_config.get_setup_command()
                for vpn_config in self._vpn_configs.values()]

    def get_setup_env_vars(self) -> Dict[str, str]:
        """Get the environment variables to setup instances with the VPN."""
        env_vars = {}
        for vpn_config in self._vpn_configs.values():
            env_vars.update(vpn_config.get_setup_env_vars())
        return env_vars

    def get_private_ip(self, hostname: str) -> Dict[str, str]:
        """Get the private IP address from the hostname."""
        private_ips = {}
        for vpn_name, vpn_config in self._vpn_configs.items():
            private_ip = vpn_config.get_private_ip(hostname)
            private_ips[vpn_name] = private_ip
        return private_ips

    def get_private_ip_vpn(self, hostname: str, vpn_name: str) -> str:
        """Get the private IP address from the hostname for a specific VPN."""
        return self._vpn_configs[vpn_name].get_private_ip(hostname)

    def remove_host(self, hostname: str) -> None:
        """Remove a host from the VPN."""
        for vpn_config in self._vpn_configs.values():
            vpn_config.remove_host(hostname)

    def to_yaml_config(self) -> Dict[str, Any]:
        """Get the VPN configuration in YAML format."""
        vpn_config_dict = {}
        for vpn_name, vpn_config in self._vpn_configs.items():
            vpn_yaml = vpn_config.to_yaml_config()
            if vpn_yaml:
                vpn_config_dict[vpn_name] = vpn_yaml
            else:
                vpn_config_dict[vpn_name] = True
        return vpn_config_dict

    def to_backend_config(self) -> Dict[str, Any]:
        """Generate the VPN configuration for the backend."""
        vpn_config_dict = {}
        for vpn_name, vpn_config in self._vpn_configs.items():
            vpn_config_dict[vpn_name] = vpn_config.to_backend_config()
        return vpn_config_dict

    def __getitem__(self, key: str) -> VPN:
        return self._vpn_configs[key]

    def __setitem__(self, key: str, value: VPN) -> None:
        self._vpn_configs[key] = value

