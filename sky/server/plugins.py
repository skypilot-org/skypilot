"""Load plugins for the SkyPilot API server."""
import abc
import dataclasses
import importlib
import os
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI

from sky import sky_logging
from sky.skylet import constants as skylet_constants
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import yaml_utils

logger = sky_logging.init_logger(__name__)

_DEFAULT_PLUGINS_CONFIG_PATH = '~/.sky/plugins.yaml'
_PLUGINS_CONFIG_ENV_VAR = (
    f'{skylet_constants.SKYPILOT_SERVER_ENV_VAR_PREFIX}PLUGINS_CONFIG')


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
    def version(self) -> Optional[str]:
        """Plugin version."""
        return None

    @property
    def commit(self) -> Optional[str]:
        """Plugin git commit hash."""
        return None

    @abc.abstractmethod
    def install(self, extension_context: ExtensionContext):
        """Hook called by API server to let the plugin install itself."""
        raise NotImplementedError

    def shutdown(self):
        """Hook called by API server to let the plugin shutdown."""
        pass


def _config_schema():
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


def get_plugin_rbac_rules() -> Dict[str, List[Dict[str, str]]]:
    """Collect RBAC rules from all loaded plugins.

    Collects rules from the ExtensionContext.

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
    rules_by_role: Dict[str, List[Dict[str, str]]] = {}

    # Collect rules registered via ExtensionContext
    if _EXTENSION_CONTEXT:
        for role, rule in _EXTENSION_CONTEXT.rbac_rules:
            if role not in rules_by_role:
                rules_by_role[role] = []
            rules_by_role[role].append({
                'path': rule.path,
                'method': rule.method,
            })

    return rules_by_role
