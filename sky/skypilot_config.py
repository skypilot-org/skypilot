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
import pathlib
import tempfile
import threading
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

import filelock
import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext import declarative

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import context
from sky.utils import schemas
from sky.utils import ux_utils
from sky.utils import yaml_utils
from sky.utils.db import db_utils
from sky.utils.db import migration_utils
from sky.utils.kubernetes import config_map_utils

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

API_SERVER_CONFIG_KEY = 'api_server_config'

_SQLALCHEMY_ENGINE: Optional[sqlalchemy.engine.Engine] = None
_SQLALCHEMY_ENGINE_LOCK = threading.Lock()

Base = declarative.declarative_base()

config_yaml_table = sqlalchemy.Table(
    'config_yaml',
    Base.metadata,
    sqlalchemy.Column('key', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('value', sqlalchemy.Text),
)


class ConfigContext:

    def __init__(self,
                 config: config_utils.Config = config_utils.Config(),
                 config_path: Optional[str] = None,
                 config_overridden: bool = False):
        self.config = config
        self.config_path = config_path
        self.config_overridden = config_overridden


# The global loaded config.
_active_workspace_context = threading.local()
_global_config_context = ConfigContext()

SKYPILOT_CONFIG_LOCK_PATH = '~/.sky/locks/.skypilot_config.lock'


def get_skypilot_config_lock_path() -> str:
    """Get the path for the SkyPilot config lock file."""
    lock_path = os.path.expanduser(SKYPILOT_CONFIG_LOCK_PATH)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    return lock_path


def _get_config_context() -> ConfigContext:
    """Get config context for current context.

    If no context is available, the global config context is returned.
    """
    ctx = context.get()
    if not ctx:
        return _global_config_context
    if ctx.config_context is None:
        # Config context for current context is not initialized, inherit from
        # the global one.
        ctx.config_context = ConfigContext(
            config=copy.deepcopy(_global_config_context.config),
            config_path=_global_config_context.config_path,
            config_overridden=_global_config_context.config_overridden,
        )
    return ctx.config_context


def _get_loaded_config() -> config_utils.Config:
    return _get_config_context().config


def _set_loaded_config(config: config_utils.Config) -> None:
    _get_config_context().config = config


def _get_loaded_config_path() -> List[Optional[str]]:
    serialized = _get_config_context().config_path
    if not serialized:
        return []
    config_paths = json.loads(serialized)
    if config_paths is None:
        return []
    return config_paths


def _set_loaded_config_path(
        path: Optional[Union[str, List[Optional[str]]]]) -> None:
    if not path:
        _get_config_context().config_path = None
    if isinstance(path, str):
        path = [path]
    _get_config_context().config_path = json.dumps(path)


def _set_loaded_config_path_serialized(path: Optional[str]) -> None:
    _get_config_context().config_path = path


def _is_config_overridden() -> bool:
    return _get_config_context().config_overridden


def _set_config_overridden(config_overridden: bool) -> None:
    _get_config_context().config_overridden = config_overridden


def get_user_config_path() -> str:
    """Returns the path to the user config file."""
    return _GLOBAL_CONFIG_PATH


def _get_config_from_path(path: Optional[str]) -> config_utils.Config:
    if path is None:
        return config_utils.Config()
    return parse_and_validate_config_file(path)


def resolve_user_config_path() -> Optional[str]:
    # find the user config file path, None if not resolved.
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
    if os.path.exists(user_config_path):
        return user_config_path
    return None


def get_user_config() -> config_utils.Config:
    """Returns the user config."""
    return _get_config_from_path(resolve_user_config_path())


def _resolve_project_config_path() -> Optional[str]:
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
    if os.path.exists(project_config_path):
        return project_config_path
    return None


def _resolve_server_config_path() -> Optional[str]:
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
    if os.path.exists(server_config_path):
        return server_config_path
    return None


def get_server_config() -> config_utils.Config:
    """Returns the server config."""
    return _get_config_from_path(_resolve_server_config_path())


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
    return _get_loaded_config().get_nested(
        keys,
        default_value,
        override_configs,
        allowed_override_keys=constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK,
        disallowed_override_keys=None)


def get_effective_region_config(
        cloud: str,
        keys: Tuple[str, ...],
        region: Optional[str] = None,
        default_value: Optional[Any] = None,
        override_configs: Optional[Dict[str, Any]] = None) -> Any:
    """Returns the nested key value by reading from config
    Order to get the property_name value:
    1. if region is specified,
       try to get the value from <cloud>/<region_key>/<region>/keys
    2. if no region or no override,
       try to get it at the cloud level <cloud>/keys
    3. if not found at cloud level,
       return either default_value if specified or None

    Note: This function currently only supports getting region-specific
    config from "kubernetes" cloud. For other clouds, this function behaves
    identically to get_nested().
    """
    return config_utils.get_cloud_config_value_from_dict(
        dict_config=_get_loaded_config(),
        cloud=cloud,
        keys=keys,
        region=region,
        default_value=default_value,
        override_configs=override_configs)


def get_workspace_cloud(cloud: str,
                        workspace: Optional[str] = None) -> config_utils.Config:
    """Returns the workspace config."""
    # TODO(zhwu): Instead of just returning the workspace specific config, we
    # should return the config that already merges the global config, so that
    # the caller does not need to manually merge the global config with
    # the workspace specific config.
    if workspace is None:
        workspace = get_active_workspace()
    clouds = get_nested(keys=(
        'workspaces',
        workspace,
    ), default_value=None)
    if clouds is None:
        return config_utils.Config()
    return clouds.get(cloud.lower(), config_utils.Config())


@contextlib.contextmanager
def local_active_workspace_ctx(workspace: str) -> Iterator[None]:
    """Temporarily set the active workspace IN CURRENT THREAD.

    Note: having this function thread-local is error-prone, as wrapping some
    operations with this will not have the underlying threads to get the
    correct active workspace. However, we cannot make it global either, as
    backend_utils.refresh_cluster_status() will be called in multiple threads,
    and they may have different active workspaces for different threads.

    # TODO(zhwu): make this function global by default and able to be set
    # it to thread-local with an argument.

    Args:
        workspace: The workspace to set as active.

    Raises:
        RuntimeError: If called from a non-main thread.
    """
    original_workspace = get_active_workspace()
    if original_workspace == workspace:
        # No change, do nothing.
        yield
        return
    _active_workspace_context.workspace = workspace
    logger.debug(f'Set context workspace: {workspace}')
    yield
    logger.debug(f'Reset context workspace: {original_workspace}')
    _active_workspace_context.workspace = original_workspace


def get_active_workspace(force_user_workspace: bool = False) -> str:
    context_workspace = getattr(_active_workspace_context, 'workspace', None)
    if not force_user_workspace and context_workspace is not None:
        logger.debug(f'Got context workspace: {context_workspace}')
        return context_workspace
    active_workspace = get_nested(keys=('active_workspace',),
                                  default_value=None)
    if active_workspace is None:
        logger.debug(f'No active workspace found, using default workspace: '
                     f'{constants.SKYPILOT_DEFAULT_WORKSPACE}')
        active_workspace = constants.SKYPILOT_DEFAULT_WORKSPACE
    else:
        logger.debug(f'Got active workspace: {active_workspace}')
    return active_workspace


def set_nested(keys: Tuple[str, ...], value: Any) -> Dict[str, Any]:
    """Returns a deep-copied config with the nested key set to value.

    Like get_nested(), if any key is not found, this will not raise an error.
    """
    copied_dict = copy.deepcopy(_get_loaded_config())
    copied_dict.set_nested(keys, value)
    return dict(**copied_dict)


def to_dict() -> config_utils.Config:
    """Returns a deep-copied version of the current config."""
    return copy.deepcopy(_get_loaded_config())


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
    with filelock.FileLock(get_skypilot_config_lock_path()):
        reload_config()


def reload_config(init_db: bool = False) -> None:
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
        _reload_config_as_server(init_db=init_db)
    else:
        _reload_config_as_client()


def parse_and_validate_config_file(config_path: str) -> config_utils.Config:
    config = config_utils.Config()
    try:
        config_dict = yaml_utils.read_yaml(config_path)
        config = config_utils.Config.from_dict(config_dict)
        # pop the db url from the config, and set it to the env var.
        # this is to avoid db url (considered a sensitive value)
        # being printed with the rest of the config.
        db_url = config.pop_nested(('db',), None)
        if db_url:
            os.environ[constants.ENV_VAR_DB_CONNECTION_URI] = db_url
        if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
            logger.debug(f'Config loaded from {config_path}:\n'
                         f'{yaml_utils.dump_yaml_str(dict(config))}')
    except yaml.YAMLError as e:
        logger.error(f'Error in loading config file ({config_path}):', e)
    if config:
        _validate_config(config, config_path)

    logger.debug(f'Config syntax check passed for path: {config_path}')
    return config


def _parse_dotlist(dotlist: List[str]) -> config_utils.Config:
    """Parse a single key-value pair into a dictionary.

    Args:
        dotlist: A single key-value pair.

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
        value = yaml_utils.safe_load(value)
        nested_keys = tuple(key.split('.'))
        config.set_nested(nested_keys, value)
    return config


def _reload_config_from_internal_file(internal_config_path: str) -> None:
    # Reset the global variables, to avoid using stale values.
    _set_loaded_config(config_utils.Config())
    _set_loaded_config_path(None)

    config_path = os.path.expanduser(internal_config_path)
    if not os.path.exists(config_path):
        with ux_utils.print_exception_no_traceback():
            raise FileNotFoundError(
                'Config file specified by env var '
                f'{ENV_VAR_SKYPILOT_CONFIG} ({config_path!r}) does not '
                'exist. Please double check the path or unset the env var: '
                f'unset {ENV_VAR_SKYPILOT_CONFIG}')
    logger.debug(f'Using config path: {config_path}')
    _set_loaded_config(parse_and_validate_config_file(config_path))
    _set_loaded_config_path(config_path)


def _create_table(engine: sqlalchemy.engine.Engine):
    """Initialize the config database with migrations."""
    migration_utils.safe_alembic_upgrade(
        engine, migration_utils.SKYPILOT_CONFIG_DB_NAME,
        migration_utils.SKYPILOT_CONFIG_VERSION)


def _initialize_and_get_db() -> sqlalchemy.engine.Engine:
    """Initialize and return the config database engine.

    This function should only be called by the API Server during initialization.
    Client-side code should never call this function.
    """
    assert os.environ.get(constants.ENV_VAR_IS_SKYPILOT_SERVER) is not None, (
        'initialize_and_get_db() can only be called by the API Server')

    global _SQLALCHEMY_ENGINE

    if _SQLALCHEMY_ENGINE is not None:
        return _SQLALCHEMY_ENGINE

    with _SQLALCHEMY_ENGINE_LOCK:
        if _SQLALCHEMY_ENGINE is not None:
            return _SQLALCHEMY_ENGINE

        # We only store config in the DB when using Postgres,
        # so no need to pass in db_name here.
        engine = db_utils.get_engine(None)

        # Run migrations if needed
        _create_table(engine)

        _SQLALCHEMY_ENGINE = engine
        return _SQLALCHEMY_ENGINE


def _reload_config_as_server(init_db: bool = False) -> None:
    # Reset the global variables, to avoid using stale values.
    _set_loaded_config(config_utils.Config())
    _set_loaded_config_path(None)

    server_config_path = _resolve_server_config_path()
    server_config = _get_config_from_path(server_config_path)
    # Get the db url from the env var. _get_config_from_path should have moved
    # the db url specified in config file to the env var.
    db_url = os.environ.get(constants.ENV_VAR_DB_CONNECTION_URI)

    if db_url:
        if len(server_config.keys()) > 1:
            raise ValueError(
                'If db config is specified, no other config is allowed')
        logger.debug('retrieving config from database')

        if init_db:
            _initialize_and_get_db()

        def _get_config_yaml_from_db(key: str) -> Optional[config_utils.Config]:
            assert _SQLALCHEMY_ENGINE is not None
            with orm.Session(_SQLALCHEMY_ENGINE) as session:
                row = session.query(config_yaml_table).filter_by(
                    key=key).first()
            if row:
                db_config = config_utils.Config(yaml_utils.safe_load(row.value))
                db_config.pop_nested(('db',), None)
                return db_config
            return None

        db_config = _get_config_yaml_from_db(API_SERVER_CONFIG_KEY)
        if db_config:
            server_config = overlay_skypilot_config(server_config, db_config)
    if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
        logger.debug(f'server config: \n'
                     f'{yaml_utils.dump_yaml_str(dict(server_config))}')
    _set_loaded_config(server_config)
    _set_loaded_config_path(server_config_path)


def _reload_config_as_client() -> None:
    # Reset the global variables, to avoid using stale values.
    _set_loaded_config(config_utils.Config())
    _set_loaded_config_path(None)

    overrides: List[config_utils.Config] = []
    user_config_path = resolve_user_config_path()
    user_config = _get_config_from_path(user_config_path)
    if user_config:
        overrides.append(user_config)
    project_config_path = _resolve_project_config_path()
    project_config = _get_config_from_path(project_config_path)
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
            f'{yaml_utils.dump_yaml_str(dict(overlaid_client_config))}')
    _set_loaded_config(overlaid_client_config)
    _set_loaded_config_path([user_config_path, project_config_path])


def loaded_config_path() -> Optional[str]:
    """Returns the path to the loaded config file, or '<overridden>' if the
    config is overridden."""
    path = [p for p in set(_get_loaded_config_path()) if p is not None]
    if len(path) == 0:
        return '<overridden>' if _is_config_overridden() else None
    if len(path) == 1:
        return path[0]

    header = 'overridden' if _is_config_overridden() else 'merged'
    path_str = ', '.join(p for p in path if p is not None)
    return f'<{header} ({path_str})>'


def loaded_config_path_serialized() -> Optional[str]:
    """Returns the json serialized config path list"""
    return _get_config_context().config_path


# Load on import, synchronization is guaranteed by python interpreter.
reload_config(init_db=True)


def loaded() -> bool:
    """Returns if the user configurations are loaded."""
    return bool(_get_loaded_config())


@contextlib.contextmanager
def override_skypilot_config(
        override_configs: Optional[Dict[str, Any]],
        override_config_path_serialized: Optional[str] = None
) -> Iterator[None]:
    """Overrides the user configurations."""
    # TODO(SKY-1215): allow admin user to extend the disallowed keys or specify
    # allowed keys.
    if not override_configs:
        # If no override configs (None or empty dict), do nothing.
        yield
        return
    original_config = _get_loaded_config()
    original_config_path = loaded_config_path_serialized()
    override_configs = config_utils.Config(override_configs)
    if override_config_path_serialized is None:
        override_config_path = []
    else:
        override_config_path = json.loads(override_config_path_serialized)

    disallowed_diff_keys = []
    for key in constants.SKIPPED_CLIENT_OVERRIDE_KEYS:
        if key == ('db',):
            # since db key is popped out of server config, the key is expected
            # to be different between client and server.
            continue
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
    config = original_config.get_nested(
        keys=tuple(),
        default_value=None,
        override_configs=dict(override_configs),
        allowed_override_keys=None,
        disallowed_override_keys=constants.SKIPPED_CLIENT_OVERRIDE_KEYS)
    workspace = config.get_nested(
        keys=('active_workspace',),
        default_value=constants.SKYPILOT_DEFAULT_WORKSPACE)
    if (workspace != constants.SKYPILOT_DEFAULT_WORKSPACE and workspace
            not in get_nested(keys=('workspaces',), default_value={})):
        raise ValueError(f'Workspace {workspace} does not exist. '
                         'Use `sky check` to see if it is defined on the API '
                         'server and try again.')
    # Initialize the active workspace context to the workspace specified, so
    # that a new request is not affected by the previous request's workspace.
    global _active_workspace_context
    _active_workspace_context = threading.local()

    try:
        common_utils.validate_schema(
            config,
            schemas.get_config_schema(),
            'Invalid config. See: '
            'https://docs.skypilot.co/en/latest/reference/config.html. '  # pylint: disable=line-too-long
            'Error: ',
            skip_none=False)
        _set_config_overridden(True)
        _set_loaded_config(config)
        _set_loaded_config_path(_get_loaded_config_path() +
                                override_config_path)
        yield
    except exceptions.InvalidSkyPilotConfigError as e:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.InvalidSkyPilotConfigError(
                'Failed to override the SkyPilot config on API '
                'server with your local SkyPilot config:\n'
                '=== SkyPilot config on API server ===\n'
                f'{yaml_utils.dump_yaml_str(dict(original_config))}\n'
                '=== Your local SkyPilot config ===\n'
                f'{yaml_utils.dump_yaml_str(dict(override_configs))}\n'
                f'Details: {e}') from e
    finally:
        _set_loaded_config(original_config)
        _set_config_overridden(False)
        _set_loaded_config_path_serialized(original_config_path)


@contextlib.contextmanager
def replace_skypilot_config(new_configs: config_utils.Config) -> Iterator[None]:
    """Replaces the global config with the new configs.

    This function is concurrent safe when it is:
    1. called in different processes;
    2. or called in a same process but with different context, refer to
       sky_utils.context for more details.
    """
    original_config = _get_loaded_config()
    original_config_path = loaded_config_path_serialized()
    original_env_var = os.environ.get(ENV_VAR_SKYPILOT_CONFIG)
    if new_configs != original_config:
        # Modify the global config of current process or context
        _set_loaded_config(new_configs)
        with tempfile.NamedTemporaryFile(delete=False,
                                         mode='w',
                                         prefix='mutated-skypilot-config-',
                                         suffix='.yaml') as temp_file:
            yaml_utils.dump_yaml(temp_file.name, dict(**new_configs))
        # Modify the env var of current process or context so that the
        # new config will be used by spawned sub-processes.
        # Note that this code modifies os.environ directly because it
        # will be hijacked to be context-aware if a context is active.
        os.environ[ENV_VAR_SKYPILOT_CONFIG] = temp_file.name
        _set_loaded_config_path(temp_file.name)
        yield
        # Restore the original config and env var.
        _set_loaded_config(original_config)
        _set_loaded_config_path_serialized(original_config_path)
        if original_env_var:
            os.environ[ENV_VAR_SKYPILOT_CONFIG] = original_env_var
        else:
            os.environ.pop(ENV_VAR_SKYPILOT_CONFIG, None)
    else:
        yield


def _compose_cli_config(cli_config: Optional[List[str]]) -> config_utils.Config:
    """Composes the skypilot CLI config.
    CLI config can either be:
    - A path to a config file
    - A single key-value pair
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
            parsed_config = parse_and_validate_config_file(maybe_config_path)
        else:  # cli_config is a single key-value pair
            parsed_config = _parse_dotlist(cli_config)
        _validate_config(parsed_config, config_source)
    except ValueError as e:
        raise ValueError(f'Invalid config override: {cli_config}. '
                         f'Check if config file exists or if the dotlist '
                         f'is formatted as: key1=value1,key2=value2.\n'
                         f'Details: {e}') from e
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
    parsed_config = _compose_cli_config(cli_config)
    if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
        logger.debug(f'applying following CLI overrides: \n'
                     f'{yaml_utils.dump_yaml_str(dict(parsed_config))}')
    _set_loaded_config(
        overlay_skypilot_config(original_config=_get_loaded_config(),
                                override_configs=parsed_config))
    return parsed_config


def update_api_server_config_no_lock(config: config_utils.Config) -> None:
    """Dumps the new config to a file and syncs to ConfigMap if in Kubernetes.

    Args:
        config: The config to save and sync.
    """

    def is_running_pytest() -> bool:
        return 'PYTEST_CURRENT_TEST' in os.environ

    # Only allow this function to be called by the API Server in production.
    if not is_running_pytest() and os.environ.get(
            constants.ENV_VAR_IS_SKYPILOT_SERVER) is None:
        raise ValueError('This function can only be called by the API Server.')

    global_config_path = _resolve_server_config_path()
    if global_config_path is None:
        # Fallback to ~/.sky/config.yaml, and make sure it exists.
        global_config_path = os.path.expanduser(get_user_config_path())
        pathlib.Path(global_config_path).touch(exist_ok=True)

    db_updated = False
    if os.environ.get(constants.ENV_VAR_IS_SKYPILOT_SERVER) is not None:
        existing_db_url = os.environ.get(constants.ENV_VAR_DB_CONNECTION_URI)
        new_db_url = config.pop_nested(('db',), None)
        if new_db_url and new_db_url != existing_db_url:
            raise ValueError('Cannot change db url while server is running')
        if existing_db_url:

            def _set_config_yaml_to_db(key: str, config: config_utils.Config):
                # reload_config(init_db=True) is called when this module is
                # imported, so the database engine must already be initialized.
                assert _SQLALCHEMY_ENGINE is not None
                config_str = yaml_utils.dump_yaml_str(dict(config))
                with orm.Session(_SQLALCHEMY_ENGINE) as session:
                    if (_SQLALCHEMY_ENGINE.dialect.name ==
                            db_utils.SQLAlchemyDialect.SQLITE.value):
                        insert_func = sqlite.insert
                    elif (_SQLALCHEMY_ENGINE.dialect.name ==
                          db_utils.SQLAlchemyDialect.POSTGRESQL.value):
                        insert_func = postgresql.insert
                    else:
                        raise ValueError('Unsupported database dialect')
                    insert_stmnt = insert_func(config_yaml_table).values(
                        key=key, value=config_str)
                    do_update_stmt = insert_stmnt.on_conflict_do_update(
                        index_elements=[config_yaml_table.c.key],
                        set_={config_yaml_table.c.value: config_str})
                    session.execute(do_update_stmt)
                    session.commit()

            logger.debug('saving api_server config to db')
            _set_config_yaml_to_db(API_SERVER_CONFIG_KEY, config)
            db_updated = True

    if not db_updated:
        # save to the local file (PVC in Kubernetes, local file otherwise)
        yaml_utils.dump_yaml(global_config_path, dict(config))

        if config_map_utils.is_running_in_kubernetes():
            # In Kubernetes, sync the PVC config to ConfigMap for user
            # convenience.
            # PVC file is the source of truth, ConfigMap is just a mirror for
            # easy access.
            config_map_utils.patch_configmap_with_config(
                config, global_config_path)

    reload_config()
