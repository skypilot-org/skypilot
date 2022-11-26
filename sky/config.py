import os
from typing import Any, Dict

from sky.utils import common_utils

DEFAULT_CONFIG_PATH = os.path.expanduser(os.path.join('~/.sky', 'config.yaml'))
# The config file under the current directory
USER_CONFIG_PATH = os.path.abspath('sky_config.yaml')

class BaseConfig:
    def load(self, config: Dict[str, Any], key_prefix: str = ''):
        for k, v in config.items():
            k = k.lower().replace('-', '_')
            if not hasattr(self, k):
                raise ValueError(f'Invalid config key: {key_prefix}.{k}')
            if isinstance(v, dict):
                self.__dict__[k].load(v, key_prefix=f'{key_prefix}.{k}')
            else:
                self.__dict__[k] = v

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
    class AWSConfig(BaseConfig):
        auto_update: bool = False
        preferred_area: str = 'us'

    def __init__(self):
        self.preferred_area = 'us'
        self.aws = self.AWSConfig()

class SkyConfig(BaseConfig):
    """Load the config file
    
    The config file is loaded in the following order:
    1. The config file under the ~/.sky directory
    2. The config file under the current directory

    The config file loaded later will override the config file loaded earlier.
    """
    def __init__(self):
        self.catalog = CatalogConfig()

        _config = self._load_config_file(DEFAULT_CONFIG_PATH)
        _config.update(self._load_config_file(USER_CONFIG_PATH))
        self.load(_config)


    def _load_config_file(self, path: str) -> Dict[str, Any]:
        """Load the config file"""
        if not os.path.exists(path):
            return {}
        with open(path, 'r') as f:
            return common_utils.read_yaml(f)

sky_config = SkyConfig()
