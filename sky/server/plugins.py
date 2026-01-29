"""Load plugins for the SkyPilot API server."""
import abc
import dataclasses
import importlib
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI

from sky import sky_logging
from sky.skylet import constants as skylet_constants
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import yaml_utils

logger = sky_logging.init_logger(__name__)

# Default paths for plugins configuration
_DEFAULT_PLUGINS_CONFIG_PATH = '~/.sky/plugins.yaml'
# Default path for remote plugins configuration
_DEFAULT_REMOTE_PLUGINS_CONFIG_PATH = '~/.sky/remote_plugins.yaml'
# Remote path for plugins config on the cluster
REMOTE_PLUGINS_CONFIG_PATH = '~/.sky/plugins.yaml'

_PLUGINS_CONFIG_ENV_VAR = (
    f'{skylet_constants.SKYPILOT_SERVER_ENV_VAR_PREFIX}PLUGINS_CONFIG')
_REMOTE_PLUGINS_CONFIG_ENV_VAR = (
    f'{skylet_constants.SKYPILOT_SERVER_ENV_VAR_PREFIX}REMOTE_PLUGINS_CONFIG')


class ExtensionContext:
    """Context provided to plugins during installation.

    Attributes:
        app: The FastAPI application instance.
        rbac_rules: List of RBAC rules registered by the plugin.
        Example:
        [
            ('user', RBACRule(path='/plugins/api/xx/*', method='POST')),
            ('user', RBACRule(path='/plugins/api/xx/*', method='DELETE'))
        ]
    """

    def __init__(self, app: Optional[FastAPI] = None):
        self.app = app
        self.rbac_rules: List[Tuple[str, RBACRule]] = []

    def register_rbac_rule(self,
                           path: str,
                           method: str,
                           description: Optional[str] = None,
                           role: str = 'user') -> None:
        """Register an RBAC rule for this plugin.

        This method allows plugins to declare which endpoints should be
        restricted to admin users during the install phase.

        Args:
            path: The path pattern to restrict (supports wildcards with
                  keyMatch2).
                  Example: '/plugins/api/credentials/*'
            method: The HTTP method to restrict. Example: 'POST', 'DELETE'
            description: Optional description of what this rule protects.
            role: The role to add this rule to (default: 'user').
                  Rules added to 'user' role block regular users but allow
                  admins.

        Example:
            def install(self, ctx: ExtensionContext):
                # Only admin can upload credentials
                ctx.register_rbac_rule(
                    path='/plugins/api/credentials/*',
                    method='POST',
                    description='Only admin can upload credentials'
                )
        """
        rule = RBACRule(path=path, method=method, description=description)
        self.rbac_rules.append((role, rule))
        logger.debug(f'Registered RBAC rule for {role}: {method} {path}'
                     f'{f" - {description}" if description else ""}')


@dataclasses.dataclass
class RBACRule:
    """RBAC rule for a plugin endpoint.

    Attributes:
        path: The path pattern to match (supports wildcards with keyMatch2).
              Example: '/plugins/api/credentials/*'
        method: The HTTP method to restrict. Example: 'POST', 'DELETE'
        description: Optional description of what this rule protects.
    """
    path: str
    method: str
    description: Optional[str] = None


class BasePlugin(abc.ABC):
    """Base class for all SkyPilot server plugins."""

    @property
    def name(self) -> Optional[str]:
        """Plugin name for display purposes."""
        return None

    @property
    def js_extension_path(self) -> Optional[str]:
        """Optional API route to the JavaScript extension to load."""
        return None

    @property
    def requires_early_init(self) -> bool:
        """Whether this plugin needs to initialize before dashboard API calls.

        Set to True if the plugin needs to intercept fetch requests or
        otherwise must be ready before the dashboard makes API calls.
        The dashboard will wait for window.__skyPluginsReady before
        proceeding with API calls when this is True.
        """
        return False

    @property
    def version(self) -> Optional[str]:
        """Plugin version."""
        return None

    @property
    def commit(self) -> Optional[str]:
        """Plugin git commit hash."""
        return None

    @property
    def hidden_from_display(self) -> bool:
        """Whether this plugin should be hidden from version display.

        Set to True to exclude this plugin from appearing in the version
        information tooltip. Defaults to False.
        """
        return False

    @property
    def rbac_rules(self) -> List[Tuple[str, 'RBACRule']]:
        """RBAC rules for this plugin.

        Override this property to declare RBAC rules that should be
        enforced for plugin endpoints. Rules are collected during
        server initialization before plugins are fully installed.

        Returns:
            List of (role, RBACRule) tuples. Rules added to 'user' role
            block regular users but allow admins.

        Example:
            @property
            def rbac_rules(self):
                return [
                    ('user', RBACRule(path='/plugins/api/foo/*',
                      method='POST')),
                    ('user', RBACRule(path='/plugins/api/foo/*',
                      method='DELETE')),
                ]
        """
        return []

    @abc.abstractmethod
    def install(self, extension_context: ExtensionContext):
        """Hook called by API server to let the plugin install itself."""
        raise NotImplementedError

    def shutdown(self):
        """Hook called by API server to let the plugin shutdown."""
        pass


def _config_schema():
    """Schema for plugins.yaml (API server plugins only)."""
    plugin_schema = {
        'type': 'object',
        'required': ['class'],
        'additionalProperties': False,
        'properties': {
            'class': {
                'type': 'string',
            },
            'parameters': {
                'type': 'object',
                'required': [],
                'additionalProperties': True,
            },
        },
    }
    return {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'controller_wheel_path': {
                # Path to a directory containing prebuilt
                # plugin wheel files (.whl).
                # All .whl files in this directory will be uploaded
                # to controllers.
                # The wheels will be uploaded to remote clusters and installed.
                'type': 'string',
            },
            'plugins': {
                'type': 'array',
                'items': plugin_schema,
                'default': [],
            },
        },
    }


def _remote_config_schema():
    """Schema for remote_plugins.yaml (plugins for remote controllers)."""
    plugin_schema = {
        'type': 'object',
        'required': ['class'],
        'additionalProperties': False,
        'properties': {
            'class': {
                'type': 'string',
            },
            'parameters': {
                'type': 'object',
                'required': [],
                'additionalProperties': True,
            },
        },
    }
    return {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'plugins': {
                'type': 'array',
                'items': plugin_schema,
                'default': [],
            },
        },
    }


def _load_plugin_config() -> Optional[config_utils.Config]:
    """Load plugin config."""
    config_path = os.getenv(_PLUGINS_CONFIG_ENV_VAR,
                            _DEFAULT_PLUGINS_CONFIG_PATH)
    config_path = os.path.expanduser(config_path)
    if not os.path.exists(config_path):
        return None
    config = yaml_utils.read_yaml(config_path) or {}
    common_utils.validate_schema(config,
                                 _config_schema(),
                                 err_msg_prefix='Invalid plugins config: ')
    return config_utils.Config.from_dict(config)


def _load_remote_plugin_config() -> Optional[config_utils.Config]:
    """Load remote plugin config from remote_plugins.yaml."""
    config_path = os.getenv(_REMOTE_PLUGINS_CONFIG_ENV_VAR,
                            _DEFAULT_REMOTE_PLUGINS_CONFIG_PATH)
    config_path = os.path.expanduser(config_path)
    if not os.path.exists(config_path):
        return None
    config = yaml_utils.read_yaml(config_path) or {}
    common_utils.validate_schema(
        config,
        _remote_config_schema(),
        err_msg_prefix='Invalid remote plugins config: ')
    return config_utils.Config.from_dict(config)


def get_remote_plugin_packages() -> List[Dict[str, Any]]:
    """Get the list of remote plugin packages with their configurations.

    Returns:
        A list of dictionaries containing plugin configurations from
        remote_plugins.yaml, each with at least 'class' and
        optionally 'parameters'.
    """
    config = _load_remote_plugin_config()
    if not config:
        return []

    plugin_configs = config.get('plugins', [])
    return [dict(p) for p in plugin_configs]


def get_remote_controller_wheel_path() -> Optional[str]:
    """Get the controller wheel path from the plugin config.

    Returns:
        The controller_wheel_path if specified in plugins.yaml,
        None otherwise.
    """
    config = _load_plugin_config()
    if not config:
        return None
    return config.get('controller_wheel_path')


def get_remote_plugins_config_path() -> Optional[str]:
    """Get the path to the remote plugins config file.

    Returns:
        The expanded path to remote_plugins.yaml if it exists,
        None otherwise.
    """
    config_path = os.getenv(_REMOTE_PLUGINS_CONFIG_ENV_VAR,
                            _DEFAULT_REMOTE_PLUGINS_CONFIG_PATH)
    config_path = os.path.expanduser(config_path)
    if not os.path.exists(config_path):
        return None
    return config_path


_PLUGINS: Dict[str, BasePlugin] = {}
_EXTENSION_CONTEXT: Optional[ExtensionContext] = None


def load_plugins(extension_context: ExtensionContext):
    """Load and initialize plugins from the config."""
    global _EXTENSION_CONTEXT
    _EXTENSION_CONTEXT = extension_context

    config = _load_plugin_config()
    if not config:
        return

    for plugin_config in config.get('plugins', []):
        class_path = plugin_config['class']
        logger.debug(f'Loading plugins: {class_path}')
        module_path, class_name = class_path.rsplit('.', 1)
        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise ImportError(
                f'Failed to import plugin module: {module_path}. '
                'Please check if the module is installed in your Python '
                'environment.') from e
        try:
            plugin_cls = getattr(module, class_name)
        except AttributeError as e:
            raise AttributeError(
                f'Could not find plugin {class_name} class in module '
                f'{module_path}. ') from e
        if not issubclass(plugin_cls, BasePlugin):
            raise TypeError(
                f'Plugin {class_path} must inherit from BasePlugin.')
        parameters = plugin_config.get('parameters') or {}
        plugin = plugin_cls(**parameters)
        plugin.install(extension_context)
        _PLUGINS[class_path] = plugin


def get_plugins() -> List[BasePlugin]:
    """Return shallow copies of the registered plugins."""
    return list(_PLUGINS.values())


_PLUGIN_RBAC_RULES: Dict[str, List[Dict[str, str]]] = {}


def load_plugin_rbac_rules() -> Dict[str, List[Dict[str, str]]]:
    """Load RBAC rules from plugins without calling install().

    This is called in the main process before permission service
    initialization to collect plugin RBAC rules. It instantiates
    plugins and reads their rbac_rules property without calling
    install(), avoiding side effects.

    Returns:
        Dictionary mapping role names to lists of blocklist rules.
    """
    global _PLUGIN_RBAC_RULES

    config = _load_plugin_config()
    if not config:
        return {}

    rules_by_role: Dict[str, List[Dict[str, str]]] = {}

    for plugin_config in config.get('plugins', []):
        class_path = plugin_config['class']
        module_path, class_name = class_path.rsplit('.', 1)
        try:
            module = importlib.import_module(module_path)
            plugin_cls = getattr(module, class_name)
            if not issubclass(plugin_cls, BasePlugin):
                continue
            parameters = plugin_config.get('parameters') or {}
            plugin = plugin_cls(**parameters)

            # Collect rules from the rbac_rules property
            for role, rule in plugin.rbac_rules:
                rules_by_role.setdefault(role, []).append({
                    'path': rule.path,
                    'method': rule.method,
                })
                logger.debug(f'Collected RBAC rule from {class_path}: '
                             f'{role} {rule.method} {rule.path}')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to load RBAC rules from {class_path}: {e}')

    _PLUGIN_RBAC_RULES = rules_by_role
    return rules_by_role


def get_plugin_rbac_rules() -> Dict[str, List[Dict[str, str]]]:
    """Collect RBAC rules from all loaded plugins.

    Returns rules collected by load_plugin_rbac_rules() which runs in the
    main process before permission service initialization.

    Returns:
        Dictionary mapping role names to lists of blocklist rules.
        Example:
        {
            'user': [
                {'path': '/plugins/api/credentials/*', 'method': 'POST'},
                {'path': '/plugins/api/credentials/*', 'method': 'DELETE'}
            ]
        }
    """
    return _PLUGIN_RBAC_RULES
