"""Unit tests for the SkyPilot API server plugins."""

import sys
import types

from fastapi import FastAPI
import yaml

from sky.server import plugins


def test_load_plugins_registers_and_installs(monkeypatch, tmp_path):
    module_name = 'sky_test_dummy_plugin'
    installed = {}

    class DummyPlugin(plugins.BasePlugin):

        def __init__(self, value=None):
            self.value = value

        def install(self, extension_context):
            installed['ctx'] = extension_context

    DummyPlugin.__module__ = module_name
    module = types.ModuleType(module_name)
    module.DummyPlugin = DummyPlugin
    monkeypatch.setitem(sys.modules, module_name, module)

    config = {
        'plugins': [{
            'class': f'{module_name}.DummyPlugin',
            'parameters': {
                'value': 42,
            },
        }],
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))
    monkeypatch.setattr(plugins, '_PLUGINS', {})

    app = FastAPI()
    ctx = plugins.ExtensionContext(app=app)

    plugins.load_plugins(ctx)
    loaded_plugins = plugins.get_plugins()

    assert len(loaded_plugins) == 1
    plugin = loaded_plugins[0]
    assert isinstance(plugin, DummyPlugin)
    assert plugin.value == 42
    assert installed['ctx'] is ctx
