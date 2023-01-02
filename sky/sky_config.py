"""Immutable user configurations (EXPERIMENTAL).

On module import, we attempt to parse the config located at CONFIG_PATH. Caller
can then use

  >> sky_config.loaded()

to check if the config is successfully loaded.

To read a nested-key config:

  >> sky_config.get_nested(('auth', 'some_auth_config'), default_value)

To pop a nested-key config:

  >> config_dict = sky_config.pop_nested(('auth', 'some_key'))

This operation returns a deep-copy dict, and is safe in that any key not found
will not raise an error.

Example usage:

Consider the following config contents:

    a:
        nested: 1
    b: 2

then:

    # Assuming ~/.sky/config.yaml exists and can be loaded:
    sky_config.loaded()  # ==> True

    sky_config.get_nested(('a', 'nested'), None)    # ==> 1
    sky_config.get_nested(('a', 'nonexist'), None)  # ==> None
    sky_config.get_nested(('a',), None)             # ==> {'nested': 1}

    # If ~/.sky/config.yaml doesn't exist or failed to be loaded:
    sky_config.loaded()  # ==> False
    sky_config.get_nested(('a', 'nested'), None)    # ==> None
    sky_config.get_nested(('a', 'nonexist'), None)  # ==> None
    sky_config.get_nested(('a',), None)             # ==> None
"""
import copy
import os
from typing import Any, Dict, Tuple

import yaml

from sky import sky_logging
from sky.utils import common_utils

# The config path is discovered in this order:
#
# (1) (Used internally) If env var {ENV_VAR_SKYPILOT_CONFIG} exists, use its
#     path;
# (2) If file {CONFIG_PATH} exists, use this file.
#
# If the path discovered by (1) fails to load, we do not attempt to go to step
# 2 in the list.

# (Used internally) An env var holding the path to the local config file. This
# is only used by spot controller tasks to ensure recoveries of the same job
# use the same config file.
ENV_VAR_SKYPILOT_CONFIG = 'SKYPILOT_CONFIG'

# Path to the local config file.
CONFIG_PATH = '~/.sky/config.yaml'

logger = sky_logging.init_logger(__name__)

# The loaded config.
_dict = None


def _try_load_config() -> None:
    global _dict
    config_path_via_env_var = os.environ.get(ENV_VAR_SKYPILOT_CONFIG)
    if config_path_via_env_var is not None:
        config_path = config_path_via_env_var
    else:
        config_path = CONFIG_PATH
    config_path = os.path.expanduser(config_path)
    if os.path.exists(config_path):
        logger.debug(f'Using config path: {config_path}')
        try:
            _dict = common_utils.read_yaml(config_path)
            logger.debug(f'Config loaded: {_dict}')
        except yaml.YAMLError as e:
            logger.error(f'Error in loading config file ({config_path}):', e)


# Load on import.
_try_load_config()


def _check_loaded_or_die():
    """Checks loaded() is true; otherwise raises RuntimeError."""
    global _dict
    if _dict is None:
        raise RuntimeError(
            f'No user configs loaded. Check {CONFIG_PATH} exists and '
            'can be loaded.')


def loaded() -> bool:
    """Returns if the user configurations are loaded."""
    global _dict
    return _dict is not None


def get_nested(keys: Tuple[str], default_value: Any) -> Any:
    """Gets a nested key.

    If any key is not found, or any intermediate key does not point to a dict
    value, returns 'default_value'.
    """
    global _dict
    if _dict is None:
        return default_value
    curr = _dict
    for key in keys:
        if isinstance(curr, dict) and key in curr:
            curr = curr[key]
        else:
            return default_value
    # FIXME: remove this logging:
    logger.info(f'Found user config: {".".join(keys)} -> {curr}')
    return curr


def pop_nested(keys: Tuple[str]) -> Dict[str, Any]:
    """Returns a deep-copied config with the nested key popped.

    Like get_nested(), if any key is not found, this will not raise an error.
    """
    _check_loaded_or_die()
    global _dict
    curr = copy.deepcopy(_dict)
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
