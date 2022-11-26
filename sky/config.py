"""Load sky configs from file."""
import os
from typing import Any, Dict, List, Union

from sky.utils import common_utils

DEFAULT_CONFIG_PATH = os.path.expanduser(os.path.join('~/.sky', 'config.yaml'))
# The config file under the current directory
USER_CONFIG_PATH = os.path.abspath('sky_config.yaml')


class BaseConfig:
    """Abstract base class for config classes."""

    def load(self, config: Dict[str, Any], key_prefix: str = ''):
        # TODO(zhwu): Add type check.
        for k, v in config.items():
            k = k.lower().replace('-', '_')
            if not hasattr(self, k):
                raise ValueError(f'Invalid config key: {key_prefix}.{k}')
            if isinstance(v, dict):
                attr = getattr(self, k)
                attr.load(v, key_prefix=f'{key_prefix}.{k}')
            else:
                self.__setattr__(k, v)

    def dump(self):
        config = {}
        for k, v in self.__dict__.items():
            k = k.replace('_', '-')
            if isinstance(v, BaseConfig):
                config[k] = v.dump()
            elif not callable(v):
                config[k] = v
        return config


class CatalogConfig(BaseConfig):
    """Config for catalog."""

    class AWSConfig(BaseConfig):
        auto_update: bool = False
        preferred_area: Union[str, List[str]] = 'us'

    class GCPConfig(BaseConfig):
        pass

    class AzureConfig(BaseConfig):
        pass

    aws: AWSConfig = AWSConfig()
    gcp: GCPConfig = GCPConfig()
    azure: AzureConfig = AzureConfig()


class SkyConfig(BaseConfig):
    """Load the config file

    The config file is loaded in the following order:
    1. The config file under the ~/.sky directory
    2. The config file under the current directory

    The config file loaded later will override the config file loaded earlier.
    """

    def __init__(self):
        self.catalog = CatalogConfig()

        config = self._load_config_file(DEFAULT_CONFIG_PATH)
        config.update(self._load_config_file(USER_CONFIG_PATH))
        self.load(config)

    def _load_config_file(self, path: str) -> Dict[str, Any]:
        """Load the config file"""
        if not os.path.exists(path):
            return {}
        return common_utils.read_yaml(path)


sky_config = SkyConfig()
