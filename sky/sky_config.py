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

# Path to the local config file.
CONFIG_PATH = os.path.expanduser('~/.sky/config.yaml')
# Remote cluster path where the local config file will be synced to.
REMOTE_CONFIG_PATH = '~/.sky/config.yaml'  # Do not expanduser.

logger = sky_logging.init_logger(__name__)

# Load on import.

# The loaded config.
_dict = None
if os.path.exists(CONFIG_PATH):
    try:
        _dict = common_utils.read_yaml(CONFIG_PATH)
    except yaml.YAMLError as e:
        logger.error(f'Error in loading config file ({CONFIG_PATH}):', e)


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
    """Pops a nested key and returns a deep-copy dictionary.

    Like get_nested(), if any key is not found, this will not raise an error.
    """
    _check_loaded_or_die()
    global _dict
    curr = copy.deepcopy(_dict)
    to_return = curr
    prev = None
    for key in keys:
        if key in curr:
            prev = curr
            curr = curr[key]
        else:
            # If any key not found, simply return.
            return to_return
    prev.pop(key)
    # FIXME: remove this logging:
    logger.info(f'Popped {keys}. Returning conf: {to_return}')
    return to_return
