"""Utils for VPN setup on instances."""
import os
from typing import Any, Dict, Optional

import requests

from sky import sky_logging
from sky.provision import common
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


class VPNConfig:
    """Basic interface for VPN configuration.

    This class defines the interface for VPN configurations. Each VPN
    should implement the following methods then it can be integrated
    into SkyPilot out of the box.
    """

    @staticmethod
    def from_yaml_config(config: Dict[str, Any]) -> 'VPNConfig':
        """Create a VPN configuration from a YAML configuration."""
        if len(config) > 1:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('We only support one VPN configuration '
                                 'in a cluster. Please check the YAML '
                                 'configuration.')
        if config.get('tailscale') is not None:
            return TailscaleConfig.from_env_vars()
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Unsupported VPN configuration. Please check '
                             'the YAML configuration.')

    def get_setup_command(self, hostname: str) -> str:
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
    def from_backend_config(config: Dict[str, Any]) -> 'VPNConfig':
        """Create a VPN configuration from the backend configuration."""
        vpn_type = config.pop('type')
        if vpn_type == 'tailscale':
            return TailscaleConfig.from_backend_config(config)
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Unsupported VPN type. Please check the backend '
                             'configuration.')

    def to_backend_config(self) -> Dict[str, Any]:
        """Get the VPN configuration for the backend."""
        return {}


class TailscaleConfig(VPNConfig):
    """Tailscale VPN configuration."""
    _TYPE = 'tailscale'

    # pylint: disable=line-too-long
    _SETUP_COMMAND = (
        'curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/focal.noarmor.gpg | '
        'sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null; '
        'curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/focal.tailscale-keyring.list | '
        'sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null; '
        'sudo apt-get update > /dev/null 2>&1; '
        'sudo apt-get install tailscale -y > /dev/null 2>&1; '
        'sudo tailscale login --auth-key {tailscale_auth_key} --hostname {hostname}'
    )

    def __init__(
        self,
        auth_key: str,
        api_key: str,
        tailnet: str,
    ) -> None:
        self._auth_key = auth_key
        self._api_key = api_key
        self._tailnet = tailnet

    @staticmethod
    def from_env_vars() -> 'TailscaleConfig':
        # Parse Tailscale auth key from environment variable.
        # This is required for all Tailscale operations.
        auth_key = os.environ.get('TS_AUTHKEY')
        api_key = os.environ.get('TS_API_KEY')
        tailnet = os.environ.get('TS_TAILNET')

        missing_env_vars = []
        if auth_key is None:
            missing_env_vars.append('TS_AUTHKEY')
        if api_key is None:
            missing_env_vars.append('TS_API_KEY')
        if tailnet is None:
            missing_env_vars.append('TS_TAILNET')

        if len(missing_env_vars) > 0:
            if len(missing_env_vars) == 1:
                missing_env_vars_str = missing_env_vars[0]
            else:
                missing_env_vars_str = ', '.join(
                    missing_env_vars[:-1]) + ' and ' + missing_env_vars[-1]
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'You should set {missing_env_vars_str} to '
                                 'enable Tailscale VPN. Please go to the '
                                 'Tailscale console, retrieve the required '
                                 'access keys and set them in the environment '
                                 'variables.')

        assert auth_key is not None and api_key is not None and tailnet is not None
        return TailscaleConfig(auth_key, api_key, tailnet)

    def get_setup_command(self, hostname: str) -> str:
        return self._SETUP_COMMAND.format(tailscale_auth_key=self._auth_key,
                                          hostname=hostname)

    def get_setup_env_vars(self) -> Dict[str, str]:
        return {
            'TS_AUTHKEY': self._auth_key,
            'TS_API_KEY': self._api_key,
            'TS_TAILNET': self._tailnet,
        }

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get the authentication headers for the Tailscale API."""
        return {
            'Authorization': f'Bearer {self._api_key}',
        }

    def _get_device_id_from_hostname(self, hostname: str) -> Optional[str]:
        """Get the node ID from the hostname."""
        url_to_query = ('https://api.tailscale.com/api/v2/tailnet/'
                        f'{self._tailnet}/devices')
        resp = requests.get(url_to_query, headers=self._get_auth_headers())
        all_devices_in_network = resp.json().get('devices', [])
        for device_info in all_devices_in_network:
            if device_info.get('hostname') == hostname:
                return device_info.get('id')
        return None

    def get_private_ip(self, hostname: str) -> str:
        """Get the private IP address from the hostname."""
        device_id = self._get_device_id_from_hostname(hostname)
        url_to_query = f'https://api.tailscale.com/api/v2/device/{device_id}'
        resp = requests.get(url_to_query, headers=self._get_auth_headers())
        return resp.json().get('addresses', [])[0]

    def remove_host(self, hostname: str) -> None:
        """Remove a host from the VPN."""
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

    def to_yaml_config(self) -> Dict[str, Any]:
        """Get the VPN configuration in YAML format."""
        return {'tailscale': True}

    @staticmethod
    def from_backend_config(config: Dict[str, Any]) -> 'TailscaleConfig':
        assert config.get('auth_key') is not None and config.get(
            'api_key') is not None and config.get('tailnet') is not None, (
                'Tailscale VPN configuration is missing required '
                'fields. Please check the backend configuration.')

        return TailscaleConfig(**config)

    def to_backend_config(self) -> Dict[str, Any]:
        return {
            'type': self._TYPE,
            'auth_key': self._auth_key,
            'api_key': self._api_key,
            'tailnet': self._tailnet,
        }


def rewrite_cluster_info_by_vpn(
    cluster_info: common.ClusterInfo,
    vpn_config: VPNConfig,
) -> common.ClusterInfo:
    for (instance_id, instance_list) in cluster_info.instances.items():
        # TODO(yi): test if this works on TPU VM.
        for (i, instance) in enumerate(instance_list):
            instance.external_ip = vpn_config.get_private_ip(
                f'skypilot-{instance_id}-{i}')
    return cluster_info
