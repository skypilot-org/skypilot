"""Load plugins for the SkyPilot API server."""
import dataclasses
import importlib.metadata
from typing import Dict, List, Optional

from fastapi import FastAPI

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

@dataclasses.dataclass
class ExtensionPoints:
    app: FastAPI


class Plugin:
    # Name of the plugin
    name: str
    # Path to the JavaScript file to load for the plugin, None
    # if no frontend extension is needed
    js_path: Optional[str] = None

    def __init__(self, name: str, js_path: Optional[str] = None):
        self.name = name
        self.js_path = js_path

_PLUGINS: Dict[str, Plugin] = {}

def load_plugins(ep: ExtensionPoints):
    for plugin in importlib.metadata.entry_points(group='skypilot.plugins'):
        logger.info('Loading plugin: %s', plugin.name)
        try:
            install_fn = plugin.load()
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Failed to import plugin {plugin.name}: {e}')
            continue

        try:
            _PLUGINS[plugin.name] = install_fn(ep)
        except Exception:  # pylint: disable=broad-except
            logger.exception('Plugin %s raised during initialization', ep.name)


def get_plugins() -> List[Plugin]:
    """Return shallow copies of the registered plugins."""
    return list(_PLUGINS.values())
