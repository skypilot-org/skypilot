"""Immutable user configurations (EXPERIMENTAL).

On module import, we parse the config file pointed to by an env var
('SKYPILOT_CONFIG').

User example usage:

    # Use a config.
    SKYPILOT_CONFIG=config1.yaml sky launch ...

    # This `sky launch` will use a different config.
    SKYPILOT_CONFIG=config2.yaml sky launch ...

    # No configs will be used.
    SKYPILOT_CONFIG=nonexist-file sky launch ...
    sky launch ...

Module API:

To check if the config is successfully loaded and non-empty:

  >> sky_config.exists()

To read a nested-key config:

  >> sky_config.get_nested(('auth', 'some_auth_config'), default_value)

To pop a nested-key config:

  >> config_dict = sky_config.pop_nested(('auth', 'some_key'))

The pop operation returns a deep-copy dict, and is safe in that any key not
found will not raise an error.

Consider the following config contents:

    a:
        nested: 1
    b: 2

then:

    # Assuming SKYPILOT_CONFIG=~/.sky/config.yaml and it can be loaded:
    sky_config.exists()  # ==> True

    sky_config.get_nested(('a', 'nested'), None)    # ==> 1
    sky_config.get_nested(('a', 'nonexist'), None)  # ==> None
    sky_config.get_nested(('a',), None)             # ==> {'nested': 1}

    # If no env var is set, any set path doesn't exist, or it can't be loaded:
    sky_config.exists()  # ==> False
    sky_config.get_nested(('a', 'nested'), None)    # ==> None
    sky_config.get_nested(('a', 'nonexist'), None)  # ==> None
    sky_config.get_nested(('a',), None)             # ==> None
"""
import copy
import functools
import os
from typing import Any, Dict, Optional, Tuple

import yaml

from sky import sky_logging
from sky.utils import common_utils

# An env var holding the path to the local config file.
SKYPILOT_CONFIG_ENV_VAR = 'SKYPILOT_CONFIG'

# (Used for spot controller) Remote cluster path where the local config file
# will be synced to.
REMOTE_CONFIG_PATH = '~/.sky/config.yaml'  # Do not expanduser.

logger = sky_logging.init_logger(__name__)


@functools.lru_cache(maxsize=1)  # Read once and cache.
def _get_config() -> Optional[Dict[str, Any]]:
    """Reads the config pointed to by the SKYPILOT_CONFIG_ENV_VAR env var.

    Returns None if the env var is not set, or if it points to a non-existent
    file or a file that fails to be raed.
    """
    config_path = os.environ.get(SKYPILOT_CONFIG_ENV_VAR)
    config = None
    if config_path is None:
        return None
    config_path = os.path.expanduser(config_path)
    if os.path.exists(config_path):
        try:
            config = common_utils.read_yaml(config_path)
        except yaml.YAMLError as e:
            logger.error(f'Error in loading config file ({config_path}):', e)
    else:
        logger.error(f'Config file ({config_path}) doesn\'t exist. No config '
                     'values will be used.')
    return config


# Load on module import.
_get_config()


def exists() -> bool:
    """Returns if the user configurations exist and are non-empty."""
    return _get_config() is not None


def get_nested(keys: Tuple[str], default_value: Any) -> Any:
    """Gets a nested key.

    If any key is not found, or any intermediate key does not point to a dict
    value, returns 'default_value'.
    """
    config = _get_config()
    if config is None:
        return default_value

    curr = config
    for key in keys:
        if isinstance(curr, dict) and key in curr:
            curr = curr[key]
        else:
            return default_value
    # FIXME: remove this logging:
    logger.info(f'Found user config: {".".join(keys)} -> {curr}')
    return curr


def pop_nested(keys: Tuple[str]) -> Dict[str, Any]:
    """Pops a nested key and returns a deep-copy dictionary.

    Like get_nested(), if any key is not found, this will not raise an error.
    """
    config = _get_config()
    if config is None:
        return {}
    curr = copy.deepcopy(config)
    to_return = curr
    prev = None
    for i, key in enumerate(keys):
        if key in curr:
            prev = curr
            curr = curr[key]
            if i == len(keys) - 1:
                prev.pop(key)
                # FIXME: remove this logging:
                logger.info(f'Popped {keys}. Returning conf: {to_return}')
        else:
            # If any key not found, simply return.
            return to_return
    return to_return
