"""Immutable user configurations (EXPERIMENTAL).

On module import, we attempt to parse the config located at CONFIG_PATH. Caller
can then use

  >> skypilot_config.loaded()

to check if the config is successfully loaded.

To read a nested-key config:

  >> skypilot_config.get_nested(('auth', 'some_auth_config'), default_value)

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
from typing import Any, Dict, Iterable, Optional

import yaml

from sky import sky_logging
from sky.utils import common_utils
from sky.utils import schemas
from sky.utils import ux_utils

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

logger = sky_logging.init_logger(__name__)

# The loaded config.
_dict = None
_loaded_config_path = None


def get_nested(keys: Iterable[str], default_value: Any) -> Any:
    """Gets a nested key.

    If any key is not found, or any intermediate key does not point to a dict
    value, returns 'default_value'.
    """
    if _dict is None:
        return default_value
    curr = _dict
    for key in keys:
        if isinstance(curr, dict) and key in curr:
            curr = curr[key]
        else:
            return default_value
    logger.debug(f'User config: {".".join(keys)} -> {curr}')
    return curr


def set_nested(keys: Iterable[str], value: Any) -> Dict[str, Any]:
    """Returns a deep-copied config with the nested key set to value.

    Like get_nested(), if any key is not found, this will not raise an error.
    """
    _check_loaded_or_die()
    assert _dict is not None
    curr = copy.deepcopy(_dict)
    to_return = curr
    prev = None
    for i, key in enumerate(keys):
        if key not in curr:
            curr[key] = {}
        prev = curr
        curr = curr[key]
        if i == len(keys) - 1:
            prev_value = prev[key]
            prev[key] = value
            logger.debug(f'Set the value of {keys} to {value} (previous: '
                         f'{prev_value}). Returning conf: {to_return}')
    return to_return


def to_dict() -> Dict[str, Any]:
    """Returns a deep-copied version of the current config."""
    if _dict is not None:
        return copy.deepcopy(_dict)
    return {}


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
        _loaded_config_path = config_path
        try:
            _dict = common_utils.read_yaml(config_path)
            logger.debug(f'Config loaded:\n{pprint.pformat(_dict)}')
        except yaml.YAMLError as e:
            logger.error(f'Error in loading config file ({config_path}):', e)
        if _dict is not None:
            common_utils.validate_schema(
                _dict,
                schemas.get_config_schema(),
                f'Invalid config YAML ({config_path}). See: '
                'https://skypilot.readthedocs.io/en/latest/reference/config.html. '  # pylint: disable=line-too-long
                'Error: ',
                skip_none=False)

        logger.debug('Config syntax check passed.')


def loaded_config_path() -> Optional[str]:
    """Returns the path to the loaded config file."""
    return _loaded_config_path


# Load on import.
_try_load_config()


def _check_loaded_or_die():
    """Checks loaded() is true; otherwise raises RuntimeError."""
    if _dict is None:
        raise RuntimeError(
            f'No user configs loaded. Check {CONFIG_PATH} exists and '
            'can be loaded.')


def loaded() -> bool:
    """Returns if the user configurations are loaded."""
    return _dict is not None
