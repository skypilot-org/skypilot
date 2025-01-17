"""Immutable user configurations (EXPERIMENTAL).

On module import, we attempt to parse the config located at CONFIG_PATH
(default: ~/.sky/config.yaml). Caller can then use

  >> skypilot_config.loaded()

to check if the config is successfully loaded.

To read a nested-key config:

  >> skypilot_config.get_nested(('auth', 'some_auth_config'), default_value)

The config can be overridden by the configs in task YAMLs. Callers are
responsible to provide the override_configs. If the nested key is part of
OVERRIDEABLE_CONFIG_KEYS, override_configs must be provided (can be empty):

  >> skypilot_config.get_nested(('docker', 'run_options'), default_value
                        override_configs={'docker': {'run_options': 'value'}})

To set a value in the nested-key config:

  >> config_dict = skypilot_config.set_nested(('auth', 'some_key'), value)

This operation returns a deep-copy dict, and is safe in that any key not found
will not raise an error.

Example usage:

Consider the following config contents:

    a:
        nested: 1
    b: 2

then:

    # Assuming ~/.sky/config.yaml exists and can be loaded:
    skypilot_config.loaded()  # ==> True

    skypilot_config.get_nested(('a', 'nested'), None)    # ==> 1
    skypilot_config.get_nested(('a', 'nonexist'), None)  # ==> None
    skypilot_config.get_nested(('a',), None)             # ==> {'nested': 1}

    # If ~/.sky/config.yaml doesn't exist or failed to be loaded:
    skypilot_config.loaded()  # ==> False
    skypilot_config.get_nested(('a', 'nested'), None)    # ==> None
    skypilot_config.get_nested(('a', 'nonexist'), None)  # ==> None
    skypilot_config.get_nested(('a',), None)             # ==> None
"""
import copy
import os
import pprint
from typing import Any, Dict, Iterable, Optional, Tuple

import yaml

from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import schemas
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# The config path is discovered in this order:
#
# (1) (Used internally) If env var {ENV_VAR_SKYPILOT_CONFIG} exists, use its
#     path;
# (2) If file {CONFIG_PATH} exists, use this file.
#
# If the path discovered by (1) fails to load, we do not attempt to go to step
# 2 in the list.

# (Used internally) An env var holding the path to the local config file. This
# is only used by jobs controller tasks to ensure recoveries of the same job
# use the same config file.
ENV_VAR_SKYPILOT_CONFIG = 'SKYPILOT_CONFIG'

# Path to the local config file.
CONFIG_PATH = '~/.sky/config.yaml'


class Config(Dict[str, Any]):
    """SkyPilot config that supports setting/getting values with nested keys."""

    def get_nested(self,
                   keys: Tuple[str, ...],
                   default_value: Any,
                   override_configs: Optional[Dict[str, Any]] = None) -> Any:
        """Gets a nested key.

        If any key is not found, or any intermediate key does not point to a
        dict value, returns 'default_value'.

        Args:
            keys: A tuple of strings representing the nested keys.
            default_value: The default value to return if the key is not found.
            override_configs: A dict of override configs with the same schema as
                the config file, but only containing the keys to override.

        Returns:
            The value of the nested key, or 'default_value' if not found.
        """
        config = copy.deepcopy(self)
        if override_configs is not None:
            config = _recursive_update(config, override_configs)
        return _get_nested(config, keys, default_value)

    def set_nested(self, keys: Tuple[str, ...], value: Any) -> None:
        """In-place sets a nested key to value.

        Like get_nested(), if any key is not found, this will not raise an
        error.
        """
        override = {}
        for i, key in enumerate(reversed(keys)):
            if i == 0:
                override = {key: value}
            else:
                override = {key: override}
        _recursive_update(self, override)

    @classmethod
    def from_dict(cls, config: Optional[Dict[str, Any]]) -> 'Config':
        if config is None:
            return cls()
        return cls(**config)


# The loaded config.
_dict = Config()
_loaded_config_path: Optional[str] = None


def _get_nested(configs: Optional[Dict[str, Any]], keys: Iterable[str],
                default_value: Any) -> Any:
    if configs is None:
        return default_value
    curr = configs
    for key in keys:
        if isinstance(curr, dict) and key in curr:
            curr = curr[key]
        else:
            return default_value
    logger.debug(f'User config: {".".join(keys)} -> {curr}')
    return curr


def get_nested(keys: Tuple[str, ...],
               default_value: Any,
               override_configs: Optional[Dict[str, Any]] = None) -> Any:
    """Gets a nested key.

    If any key is not found, or any intermediate key does not point to a dict
    value, returns 'default_value'.

    When 'keys' is within OVERRIDEABLE_CONFIG_KEYS, 'override_configs' must be
    provided (can be empty). Otherwise, 'override_configs' must not be provided.

    Args:
        keys: A tuple of strings representing the nested keys.
        default_value: The default value to return if the key is not found.
        override_configs: A dict of override configs with the same schema as
            the config file, but only containing the keys to override.

    Returns:
        The value of the nested key, or 'default_value' if not found.
    """
    assert not (
        keys in constants.OVERRIDEABLE_CONFIG_KEYS and
        override_configs is None), (
            f'Override configs must be provided when keys {keys} is within '
            'constants.OVERRIDEABLE_CONFIG_KEYS: '
            f'{constants.OVERRIDEABLE_CONFIG_KEYS}')
    assert not (
        keys not in constants.OVERRIDEABLE_CONFIG_KEYS and
        override_configs is not None
    ), (f'Override configs must not be provided when keys {keys} is not within '
        'constants.OVERRIDEABLE_CONFIG_KEYS: '
        f'{constants.OVERRIDEABLE_CONFIG_KEYS}')
    return _dict.get_nested(keys, default_value, override_configs)


def _recursive_update(base_config: Config,
                      override_config: Dict[str, Any]) -> Config:
    """Recursively updates base configuration with override configuration"""
    for key, value in override_config.items():
        if (isinstance(value, dict) and key in base_config and
                isinstance(base_config[key], dict)):
            _recursive_update(base_config[key], value)
        else:
            base_config[key] = value
    return base_config


def set_nested(keys: Tuple[str, ...], value: Any) -> Dict[str, Any]:
    """Returns a deep-copied config with the nested key set to value.

    Like get_nested(), if any key is not found, this will not raise an error.
    """
    copied_dict = copy.deepcopy(_dict)
    copied_dict.set_nested(keys, value)
    return dict(**copied_dict)


def to_dict() -> Config:
    """Returns a deep-copied version of the current config."""
    return copy.deepcopy(_dict)


def _try_load_config() -> None:
    global _dict, _loaded_config_path
    config_path_via_env_var = os.environ.get(ENV_VAR_SKYPILOT_CONFIG)
    if config_path_via_env_var is not None:
        config_path = os.path.expanduser(config_path_via_env_var)
        if not os.path.exists(config_path):
            with ux_utils.print_exception_no_traceback():
                raise FileNotFoundError(
                    'Config file specified by env var '
                    f'{ENV_VAR_SKYPILOT_CONFIG} ({config_path!r}) does not '
                    'exist. Please double check the path or unset the env var: '
                    f'unset {ENV_VAR_SKYPILOT_CONFIG}')
    else:
        config_path = CONFIG_PATH
    config_path = os.path.expanduser(config_path)
    if os.path.exists(config_path):
        logger.debug(f'Using config path: {config_path}')
        try:
            config = common_utils.read_yaml(config_path)
            _dict = Config.from_dict(config)
            _loaded_config_path = config_path
            logger.debug(f'Config loaded:\n{pprint.pformat(_dict)}')
        except yaml.YAMLError as e:
            logger.error(f'Error in loading config file ({config_path}):', e)
        if _dict:
            common_utils.validate_schema(
                _dict,
                schemas.get_config_schema(),
                f'Invalid config YAML ({config_path}). See: '
                'https://docs.skypilot.co/en/latest/reference/config.html. '  # pylint: disable=line-too-long
                'Error: ',
                skip_none=False)

        logger.debug('Config syntax check passed.')


def loaded_config_path() -> Optional[str]:
    """Returns the path to the loaded config file."""
    return _loaded_config_path


# Load on import.
_try_load_config()


def loaded() -> bool:
    """Returns if the user configurations are loaded."""
    return bool(_dict)
