"""Immutable user configurations (EXPERIMENTAL).

On module import, we attempt to parse the config located at _GLOBAL_CONFIG_PATH
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
import json
import os
import threading
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sky import exceptions
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

# The config is generated as described below:
#
# (*) (Used internally) If env var {ENV_VAR_SKYPILOT_CONFIG} exists, use its
#     path as the config file. Do not use any other config files.
#     This behavior is subject to change and should not be relied on by users.
# Else,
# (1) If env var {ENV_VAR_GLOBAL_CONFIG} exists, use its path as the user
#     config file. Else, use the default path {_GLOBAL_CONFIG_PATH}.
# (2) If env var {ENV_VAR_PROJECT_CONFIG} exists, use its path as the project
#     config file. Else, use the default path {_PROJECT_CONFIG_PATH}.
# (3) Override any config keys in (1) with the ones in (2).
# (4) Validate the final config.
#
# (*) is used internally to implement the behavior of the jobs controller.
#     It is not intended to be used by end users.
# (1) and (2) are used by end users to set non-default user and project config
#     files on clients.

# (Used internally) An env var holding the path to the local config file. This
# is only used by jobs controller tasks to ensure recoveries of the same job
# use the same config file.
ENV_VAR_SKYPILOT_CONFIG = f'{constants.SKYPILOT_ENV_VAR_PREFIX}CONFIG'

# Environment variables for setting non-default server and user
# config files.
ENV_VAR_GLOBAL_CONFIG = f'{constants.SKYPILOT_ENV_VAR_PREFIX}GLOBAL_CONFIG'
# Environment variables for setting non-default project config files.
ENV_VAR_PROJECT_CONFIG = f'{constants.SKYPILOT_ENV_VAR_PREFIX}PROJECT_CONFIG'

# Path to the client config files.
_GLOBAL_CONFIG_PATH = '~/.sky/config.yaml'
_PROJECT_CONFIG_PATH = '.sky.yaml'

# The loaded config.
_dict = config_utils.Config()
_loaded_config_path: Optional[str] = None
_config_overridden: bool = False
_reload_config_lock = threading.Lock()


def get_user_config_path() -> str:
    """Returns the path to the user config file."""
    return _GLOBAL_CONFIG_PATH


def get_user_config() -> config_utils.Config:
    """Returns the user config."""
    # find the user config file
    user_config_path = _get_config_file_path(ENV_VAR_GLOBAL_CONFIG)
    if user_config_path:
        logger.debug('using user config file specified by '
                     f'{ENV_VAR_GLOBAL_CONFIG}: {user_config_path}')
        user_config_path = os.path.expanduser(user_config_path)
        if not os.path.exists(user_config_path):
            with ux_utils.print_exception_no_traceback():
                raise FileNotFoundError(
                    'Config file specified by env var '
                    f'{ENV_VAR_GLOBAL_CONFIG} ({user_config_path!r}) '
                    'does not exist. Please double check the path or unset the '
                    f'env var: unset {ENV_VAR_GLOBAL_CONFIG}')
    else:
        user_config_path = get_user_config_path()
        logger.debug(f'using default user config file: {user_config_path}')
        user_config_path = os.path.expanduser(user_config_path)

    # load the user config file
    if os.path.exists(user_config_path):
        user_config = parse_config_file(user_config_path)
        _validate_config(user_config, user_config_path)
    else:
        user_config = config_utils.Config()
    return user_config


def _get_project_config() -> config_utils.Config:
    # find the project config file
    project_config_path = _get_config_file_path(ENV_VAR_PROJECT_CONFIG)
    if project_config_path:
        logger.debug('using project config file specified by '
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
        logger.debug(
            f'using default project config file: {_PROJECT_CONFIG_PATH}')
        project_config_path = _PROJECT_CONFIG_PATH
        project_config_path = os.path.expanduser(project_config_path)

    # load the project config file
    if os.path.exists(project_config_path):
        project_config = parse_config_file(project_config_path)
        _validate_config(project_config, project_config_path)
    else:
        project_config = config_utils.Config()
    return project_config


def get_server_config() -> config_utils.Config:
    """Returns the server config."""
    # find the server config file
    server_config_path = _get_config_file_path(ENV_VAR_GLOBAL_CONFIG)
    if server_config_path:
        logger.debug('using server config file specified by '
                     f'{ENV_VAR_GLOBAL_CONFIG}: {server_config_path}')
        server_config_path = os.path.expanduser(server_config_path)
        if not os.path.exists(server_config_path):
            with ux_utils.print_exception_no_traceback():
                raise FileNotFoundError(
                    'Config file specified by env var '
                    f'{ENV_VAR_GLOBAL_CONFIG} ({server_config_path!r}) '
                    'does not exist. Please double check the path or unset the '
                    f'env var: unset {ENV_VAR_GLOBAL_CONFIG}')
    else:
        server_config_path = _GLOBAL_CONFIG_PATH
        logger.debug(f'using default server config file: {server_config_path}')
        server_config_path = os.path.expanduser(server_config_path)

    # load the server config file
    if os.path.exists(server_config_path):
        server_config = parse_config_file(server_config_path)
        _validate_config(server_config, server_config_path)
    else:
        server_config = config_utils.Config()
    return server_config


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


def _validate_config(config: Dict[str, Any], config_source: str) -> None:
    """Validates the config."""
    common_utils.validate_schema(
        config,
        schemas.get_config_schema(),
        f'Invalid config YAML from ({config_source}). See: '
        'https://docs.skypilot.co/en/latest/reference/config.html. '  # pylint: disable=line-too-long
        'Error: ',
        skip_none=False)


def overlay_skypilot_config(
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


def safe_reload_config() -> None:
    """Reloads the config, safe to be called concurrently."""
    with _reload_config_lock:
        _reload_config()


def _reload_config() -> None:
    internal_config_path = os.environ.get(ENV_VAR_SKYPILOT_CONFIG)
    if internal_config_path is not None:
        # {ENV_VAR_SKYPILOT_CONFIG} is used internally.
        # When this environment variable is set, the config loading
        # behavior is not defined in the public interface.
        # SkyPilot reserves the right to change the config loading behavior
        # at any time when this environment variable is set.
        _reload_config_from_internal_file(internal_config_path)
        return

    if os.environ.get(constants.ENV_VAR_IS_SKYPILOT_SERVER) is not None:
        _reload_config_as_server()
    else:
        _reload_config_as_client()


def parse_config_file(config_path: str) -> config_utils.Config:
    config = config_utils.Config()
    try:
        config_dict = common_utils.read_yaml(config_path)
        config = config_utils.Config.from_dict(config_dict)
        if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
            logger.debug(f'Config loaded from {config_path}:\n'
                         f'{common_utils.dump_yaml_str(dict(config))}')
    except yaml.YAMLError as e:
        logger.error(f'Error in loading config file ({config_path}):', e)
    if config:
        _validate_config(config, config_path)

    logger.debug(f'Config syntax check passed for path: {config_path}')
    return config


def _parse_dotlist(dotlist: List[str]) -> config_utils.Config:
    """Parse a comma-separated list of key-value pairs into a dictionary.

    Args:
        dotlist: A comma-separated list of key-value pairs.

    Returns:
        A config_utils.Config object with the parsed key-value pairs.
    """
    config: config_utils.Config = config_utils.Config()
    for arg in dotlist:
        try:
            key, value = arg.split('=', 1)
        except ValueError as e:
            raise ValueError(f'Invalid config override: {arg}. '
                             'Please use the format: key=value') from e
        if len(key) == 0 or len(value) == 0:
            raise ValueError(f'Invalid config override: {arg}. '
                             'Please use the format: key=value')
        value = yaml.safe_load(value)
        nested_keys = tuple(key.split('.'))
        config.set_nested(nested_keys, value)
    return config


def _reload_config_from_internal_file(internal_config_path: str) -> None:
    global _dict, _loaded_config_path
    # Reset the global variables, to avoid using stale values.
    _dict = config_utils.Config()
    _loaded_config_path = None

    config_path = os.path.expanduser(internal_config_path)
    if not os.path.exists(config_path):
        with ux_utils.print_exception_no_traceback():
            raise FileNotFoundError(
                'Config file specified by env var '
                f'{ENV_VAR_SKYPILOT_CONFIG} ({config_path!r}) does not '
                'exist. Please double check the path or unset the env var: '
                f'unset {ENV_VAR_SKYPILOT_CONFIG}')
    logger.debug(f'Using config path: {config_path}')
    _dict = parse_config_file(config_path)
    _loaded_config_path = config_path


def _reload_config_as_server() -> None:
    global _dict
    # Reset the global variables, to avoid using stale values.
    _dict = config_utils.Config()

    overrides: List[config_utils.Config] = []
    server_config = get_server_config()
    if server_config:
        overrides.append(server_config)

    # layer the configs on top of each other based on priority
    overlaid_server_config: config_utils.Config = config_utils.Config()
    for override in overrides:
        overlaid_server_config = overlay_skypilot_config(
            original_config=overlaid_server_config, override_configs=override)
    if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
        logger.debug(
            f'server config: \n'
            f'{common_utils.dump_yaml_str(dict(overlaid_server_config))}')
    _dict = overlaid_server_config


def _reload_config_as_client() -> None:
    global _dict
    # Reset the global variables, to avoid using stale values.
    _dict = config_utils.Config()

    overrides: List[config_utils.Config] = []
    user_config = get_user_config()
    if user_config:
        overrides.append(user_config)
    project_config = _get_project_config()
    if project_config:
        overrides.append(project_config)

    # layer the configs on top of each other based on priority
    overlaid_client_config: config_utils.Config = config_utils.Config()
    for override in overrides:
        overlaid_client_config = overlay_skypilot_config(
            original_config=overlaid_client_config, override_configs=override)
    if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
        logger.debug(
            f'client config (before task and CLI overrides): \n'
            f'{common_utils.dump_yaml_str(dict(overlaid_client_config))}')
    _dict = overlaid_client_config


def loaded_config_path() -> Optional[str]:
    """Returns the path to the loaded config file, or
    '<overridden>' if the config is overridden."""
    if _config_overridden:
        return '<overridden>'
    return _loaded_config_path


# Load on import, synchronization is guaranteed by python interpreter.
_reload_config()


def loaded() -> bool:
    """Returns if the user configurations are loaded."""
    return bool(_dict)


@contextlib.contextmanager
def override_skypilot_config(
        override_configs: Optional[Dict[str, Any]]) -> Iterator[None]:
    """Overrides the user configurations."""
    global _dict, _config_overridden
    # TODO(SKY-1215): allow admin user to extend the disallowed keys or specify
    # allowed keys.
    if not override_configs:
        # If no override configs (None or empty dict), do nothing.
        yield
        return
    original_config = _dict
    override_configs = config_utils.Config(override_configs)
    disallowed_diff_keys = []
    for key in constants.SKIPPED_CLIENT_OVERRIDE_KEYS:
        value = override_configs.pop_nested(key, default_value=None)
        if (value is not None and
                value != original_config.get_nested(key, default_value=None)):
            disallowed_diff_keys.append('.'.join(key))
    # Only warn if there is a diff in disallowed override keys, as the client
    # use the same config file when connecting to a local server.
    if disallowed_diff_keys:
        logger.warning(
            f'The following keys ({json.dumps(disallowed_diff_keys)}) have '
            'different values in the client SkyPilot config with the server '
            'and will be ignored. Remove these keys to disable this warning. '
            'If you want to specify it, please modify it on server side or '
            'contact your administrator.')
    config = _dict.get_nested(
        keys=tuple(),
        default_value=None,
        override_configs=dict(override_configs),
        allowed_override_keys=None,
        disallowed_override_keys=constants.SKIPPED_CLIENT_OVERRIDE_KEYS)
    try:
        common_utils.validate_schema(
            config,
            schemas.get_config_schema(),
            'Invalid config. See: '
            'https://docs.skypilot.co/en/latest/reference/config.html. '  # pylint: disable=line-too-long
            'Error: ',
            skip_none=False)
        _config_overridden = True
        _dict = config
        yield
    except exceptions.InvalidSkyPilotConfigError as e:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.InvalidSkyPilotConfigError(
                'Failed to override the SkyPilot config on API '
                'server with your local SkyPilot config:\n'
                '=== SkyPilot config on API server ===\n'
                f'{common_utils.dump_yaml_str(dict(original_config))}\n'
                '=== Your local SkyPilot config ===\n'
                f'{common_utils.dump_yaml_str(dict(override_configs))}\n'
                f'Details: {e}') from e
    finally:
        _dict = original_config
        _config_overridden = False


def _compose_cli_config(cli_config: Optional[List[str]]) -> config_utils.Config:
    """Composes the skypilot CLI config.
    CLI config can either be:
    - A path to a config file
    - A comma-separated list of key-value pairs
    """

    if not cli_config:
        return config_utils.Config()

    config_source = 'CLI'
    try:
        maybe_config_path = os.path.expanduser(cli_config[0])
        if os.path.isfile(maybe_config_path):
            if len(cli_config) != 1:
                raise ValueError(
                    'Cannot use multiple --config flags with a config file.')
            config_source = maybe_config_path
            # cli_config is a path to a config file
            parsed_config = parse_config_file(maybe_config_path)
        else:  # cli_config is a comma-separated list of key-value pairs
            parsed_config = _parse_dotlist(cli_config)
        _validate_config(parsed_config, config_source)
    except ValueError as e:
        raise ValueError(f'Invalid config override: {cli_config}. '
                         f'Check if config file exists or if the dotlist '
                         f'is formatted as: key1=value1,key2=value2') from e
    logger.debug('CLI overrides config syntax check passed.')

    return parsed_config


def apply_cli_config(cli_config: Optional[List[str]]) -> Dict[str, Any]:
    """Applies the CLI provided config.
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
    if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
        logger.debug(f'applying following CLI overrides: \n'
                     f'{common_utils.dump_yaml_str(dict(parsed_config))}')
    _dict = overlay_skypilot_config(original_config=_dict,
                                    override_configs=parsed_config)
    return parsed_config
