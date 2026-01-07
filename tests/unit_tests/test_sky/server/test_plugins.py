"""Unit tests for the SkyPilot API server plugins."""

import importlib
import sys
import types
from unittest import mock

from fastapi import FastAPI
import yaml

from sky.server import plugins
from sky.server import uvicorn as skyuvicorn


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


def test_server_import_loads_plugins(monkeypatch):
    load_mock = mock.MagicMock()
    monkeypatch.setattr(plugins, 'load_plugins', load_mock)

    server_module = importlib.import_module('sky.server.server')
    load_mock.reset_mock()

    importlib.reload(server_module)

    load_mock.assert_called_once()
    ctx = load_mock.call_args.args[0]
    assert isinstance(ctx, plugins.ExtensionContext)
    assert ctx.app is server_module.app


def test_uvicorn_run_loads_plugins_for_multiple_workers(monkeypatch):
    load_mock = mock.MagicMock()
    monkeypatch.setattr(plugins, 'load_plugins', load_mock)

    class DummyServer:

        def __init__(self, config, max_db_connections=None):
            del config, max_db_connections

        def run(self, *args, **kwargs):
            del args, kwargs

    class DummyMultiprocess:

        def __init__(self, config, target, sockets):
            self.config = config
            self.target = target
            self.sockets = sockets
            self.run_called = False

        def run(self):
            self.run_called = True

    class DummyConfig:
        reload = False
        workers = 2
        uds = None

        def bind_socket(self):
            return object()

    monkeypatch.setattr(skyuvicorn, 'Server', DummyServer)
    monkeypatch.setattr(skyuvicorn, 'SlowStartMultiprocess', DummyMultiprocess)

    dummy_config = DummyConfig()
    skyuvicorn.run(dummy_config)

    load_mock.assert_called_once()
    ctx = load_mock.call_args.args[0]
    assert isinstance(ctx, plugins.ExtensionContext)
    assert ctx.app is None
