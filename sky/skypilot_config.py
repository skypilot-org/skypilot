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
import contextlib
import copy
import os
import pprint
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple

from omegaconf import OmegaConf

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import schemas
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import yaml
else:
    yaml = adaptors_common.LazyImport('yaml')

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
# If this value is specified, ENV_VAR_GLOBAL_CONFIG and ENV_VAR_PROJECT_CONFIG
# are ignored and ONLY the config file specified by this env var is used.
ENV_VAR_SKYPILOT_CONFIG = f'{constants.SKYPILOT_ENV_VAR_PREFIX}CONFIG'

# (Used by users) Environment variables for setting non-default global and
# project config files on clients.
ENV_VAR_GLOBAL_CONFIG = f'{constants.SKYPILOT_ENV_VAR_PREFIX}GLOBAL_CONFIG'
ENV_VAR_PROJECT_CONFIG = f'{constants.SKYPILOT_ENV_VAR_PREFIX}PROJECT_CONFIG'

# Path to the local config file.
CONFIG_PATH = '~/.sky/config.yaml'

# The loaded config.
_dict = config_utils.Config()
_loaded_config_path: Optional[str] = None


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
    return _dict.get_nested(
        keys,
        default_value,
        override_configs,
        allowed_override_keys=constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK,
        disallowed_override_keys=None)


def set_nested(keys: Tuple[str, ...], value: Any) -> Dict[str, Any]:
    """Returns a deep-copied config with the nested key set to value.

    Like get_nested(), if any key is not found, this will not raise an error.
    """
    copied_dict = copy.deepcopy(_dict)
    copied_dict.set_nested(keys, value)
    return dict(**copied_dict)


def to_dict() -> config_utils.Config:
    """Returns a deep-copied version of the current config."""
    return copy.deepcopy(_dict)


def _get_config_file_path(envvar: str) -> Optional[str]:
    config_path_via_env_var = os.environ.get(envvar)
    if config_path_via_env_var is not None:
        return os.path.expanduser(config_path_via_env_var)
    return None


def _validate_config(config: Dict[str, Any], source: str) -> None:
    """Validates the config."""
    common_utils.validate_schema(
        config,
        schemas.get_config_schema(),
        f'Invalid {source} configuration. See: '
        'https://docs.skypilot.co/en/latest/reference/config.html. '  # pylint: disable=line-too-long
        'Error: ',
        skip_none=False)


def _overlay_skypilot_config(
        original_config: Optional[config_utils.Config],
        override_configs: Optional[config_utils.Config]) -> config_utils.Config:
    """Overlays the override configs on the original configs."""
    if original_config is None:
        original_config = config_utils.Config()
    config = original_config.get_nested(keys=tuple(),
                                        default_value=None,
                                        override_configs=override_configs,
                                        allowed_override_keys=None,
                                        disallowed_override_keys=None)
    return config


def _reload_config() -> None:
    internal_config_path = os.environ.get(ENV_VAR_SKYPILOT_CONFIG)
    if internal_config_path is not None:
        _reload_config_legacy()
    else:
        _reload_config_hierarchical()


def _reload_config_legacy() -> None:
    global _dict, _loaded_config_path
    # Reset the global variables, to avoid using stale values.
    _dict = config_utils.Config()
    _loaded_config_path = None

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
            _dict = config_utils.Config.from_dict(config)
            if config_path_via_env_var is not None:
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


def _reload_config_hierarchical() -> None:
    global _dict
    # Reset the global variables, to avoid using stale values.
    _dict = config_utils.Config()

    # find the global config file
    global_config_path = _get_config_file_path(ENV_VAR_GLOBAL_CONFIG)
    if global_config_path and os.path.exists(global_config_path):
        logger.info('using global config file specified by '
                    f'{ENV_VAR_GLOBAL_CONFIG}: {global_config_path}')
        global_config_path = os.path.expanduser(global_config_path)
        if not os.path.exists(global_config_path):
            with ux_utils.print_exception_no_traceback():
                raise FileNotFoundError(
                    'Config file specified by env var '
                    f'{ENV_VAR_GLOBAL_CONFIG} ({global_config_path!r}) '
                    'does not exist. Please double check the path or unset the '
                    f'env var: unset {ENV_VAR_GLOBAL_CONFIG}')
    else:
        logger.info(f'using default global config file: {CONFIG_PATH}')
        global_config_path = CONFIG_PATH
        global_config_path = os.path.expanduser(global_config_path)

    overrides = []

    # find the project config file
    project_config_path = _get_config_file_path(ENV_VAR_PROJECT_CONFIG)
    if project_config_path and os.path.exists(project_config_path):
        logger.info('using project config file specified by '
                    f'{ENV_VAR_PROJECT_CONFIG}: {project_config_path}')
        project_config_path = os.path.expanduser(project_config_path)
        if not os.path.exists(project_config_path):
            with ux_utils.print_exception_no_traceback():
                raise FileNotFoundError(
                    'Config file specified by env var '
                    f'{ENV_VAR_PROJECT_CONFIG} ({project_config_path!r}) '
                    'does not exist. Please double check the path or unset the '
                    f'env var: unset {ENV_VAR_PROJECT_CONFIG}')
    else:
        logger.info(f'using default project config file: {project_config_path}')
        project_config_path = os.path.join(os.getcwd(), 'sky.yaml')

    # load the global config file
    if os.path.exists(global_config_path):
        logger.info(f'Using global config path: {global_config_path}')
        global_config = OmegaConf.to_object(OmegaConf.load(global_config_path))
        logger.info('following overrides '
                    'are obtained from global config file:')
        logger.info(global_config)
        _validate_config(global_config, 'global')
        overrides.append(global_config)

    if os.path.exists(project_config_path):
        logger.info(f'Using project config path: {project_config_path}')
        project_config = OmegaConf.to_object(
            OmegaConf.load(project_config_path))
        logger.info('following overrides '
                    'are obtained from project config file:')
        logger.info(project_config)
        _validate_config(project_config, 'project')
        overrides.append(project_config)

    # layer the configs on top of each other based on priority
    overlaid_client_config: config_utils.Config = config_utils.Config()
    for override in reversed(overrides):
        overlaid_client_config = _overlay_skypilot_config(
            original_config=overlaid_client_config, override_configs=override)
    _dict = overlaid_client_config


def loaded_config_path() -> Optional[str]:
    """Returns the path to the loaded config file."""
    return _loaded_config_path


# Load on import.
_reload_config()


def loaded() -> bool:
    """Returns if the user configurations are loaded."""
    return bool(_dict)


@contextlib.contextmanager
def override_skypilot_config(
        override_configs: Optional[Dict[str, Any]]) -> Iterator[None]:
    """Overrides the user configurations."""
    global _dict
    # TODO(SKY-1215): allow admin user to extend the disallowed keys or specify
    # allowed keys.
    if not override_configs:
        # If no override configs (None or empty dict), do nothing.
        yield
        return
    original_config = copy.deepcopy(_dict)
    config = _dict.get_nested(
        keys=tuple(),
        default_value=None,
        override_configs=override_configs,
        allowed_override_keys=None,
        disallowed_override_keys=constants.SKIPPED_CLIENT_OVERRIDE_KEYS)
    try:
        _dict = config
        yield
    finally:
        _dict = original_config


def _compose_cli_config(cli_config: Optional[str],) -> config_utils.Config:
    """Composes the skypilot CLI config.
    CLI config can either be:
    - A path to a config file
    - A comma-separated list of key-value pairs
    """

    if not cli_config:
        return config_utils.Config()

    if os.path.isfile(cli_config):
        # cli_config is a path to a config file
        logger.info(f'Parsing CLI provided config file: {cli_config}')
        parsed_config = OmegaConf.to_object(OmegaConf.load(cli_config))
    else:
        # cli_config is a comma-separated list of key-value pairs
        logger.info(f'Parsing CLI provided config: {cli_config}')
        variables: List[str] = []
        variables = cli_config.split(',')
        parsed_config = OmegaConf.to_object(OmegaConf.from_dotlist(variables))
    logger.info(f'Parsed CLI config: {parsed_config}')

    _validate_config(parsed_config, 'cli')
    logger.debug('Config syntax check passed.')

    return parsed_config


def apply_cli_config(cli_config: Optional[str]) -> None:
    """Applies the skypilot CLI config.

    SAFETY:
    This function directly modifies the global _dict variable.
    This is considered fine in CLI context because the program will exit after
    a single CLI command is executed.

    Args:
        cli_config: A path to a config file or a comma-separated
        list of key-value pairs.
    """
    global _dict
    parsed_config = _compose_cli_config(cli_config)
    logger.info(
        f'applying following overrides from CLI config: {parsed_config}')
    print(f'original _dict: {_dict}')
    _dict = _overlay_skypilot_config(original_config=_dict,
                                     override_configs=parsed_config)
    print(f'new _dict: {_dict}')
