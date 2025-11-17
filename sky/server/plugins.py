"""Load plugins for the SkyPilot API server."""
import abc
import dataclasses
import importlib
import os
from typing import Dict, List, Optional

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


@dataclasses.dataclass
class ExtensionContext:
    app: FastAPI


class BasePlugin(abc.ABC):
    """Base class for all SkyPilot server plugins."""

    @property
    def js_extension_path(self) -> Optional[str]:
        """Optional API route to the JavaScript extension to load."""
        return None

    @abc.abstractmethod
    def install(self, extension_context: ExtensionContext):
        """Hook called by API server to let the plugin install itself."""
        raise NotImplementedError


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


def load_plugins(extension_context: ExtensionContext):
    """Load and initialize plugins from the config."""
    config = _load_plugin_config()
    if not config:
        return

    for plugin_config in config.get('plugins', []):
        class_path = plugin_config['class']
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
