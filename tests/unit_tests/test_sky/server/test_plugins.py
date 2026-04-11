"""Unit tests for the SkyPilot API server plugins."""

import importlib
import sys
import types
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
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


def test_hidden_from_display_property_default():
    """Test that hidden_from_display defaults to False."""
    module_name = 'sky_test_visible_plugin'

    class VisiblePlugin(plugins.BasePlugin):

        def install(self, extension_context):
            pass

    plugin = VisiblePlugin()
    assert plugin.hidden_from_display is False


def test_hidden_from_display_property_override():
    """Test that plugins can override hidden_from_display to True."""
    module_name = 'sky_test_hidden_plugin'

    class HiddenPlugin(plugins.BasePlugin):

        @property
        def hidden_from_display(self) -> bool:
            return True

        def install(self, extension_context):
            pass

    plugin = HiddenPlugin()
    assert plugin.hidden_from_display is True


def test_api_plugins_endpoint_excludes_hidden_plugins(monkeypatch):
    """Test that /api/plugins endpoint excludes plugins with hidden_from_display=True."""
    from sky.server import server

    # Create test plugins
    class VisiblePlugin(plugins.BasePlugin):

        @property
        def name(self) -> str:
            return 'VisiblePlugin'

        @property
        def version(self) -> str:
            return '1.0.0'

        @property
        def commit(self) -> str:
            return 'abc123'

        def install(self, extension_context):
            pass

    class HiddenPlugin(plugins.BasePlugin):

        @property
        def name(self) -> str:
            return 'HiddenPlugin'

        @property
        def version(self) -> str:
            return '2.0.0'

        @property
        def commit(self) -> str:
            return 'def456'

        @property
        def hidden_from_display(self) -> bool:
            return True

        def install(self, extension_context):
            pass

    # Mock get_plugins to return our test plugins
    monkeypatch.setattr(
        plugins, 'get_plugins',
        lambda: [VisiblePlugin(), HiddenPlugin()])

    # Create test client
    client = TestClient(server.app)

    # Make request to /api/plugins
    response = client.get('/api/plugins')

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert 'plugins' in data
    plugin_list = data['plugins']

    # Should only contain the visible plugin
    assert len(plugin_list) == 1
    assert plugin_list[0]['name'] == 'VisiblePlugin'
    assert plugin_list[0]['version'] == '1.0.0'
    assert plugin_list[0]['commit'] == 'abc123'

    # Verify hidden plugin is not in the list
    plugin_names = [p['name'] for p in plugin_list]
    assert 'HiddenPlugin' not in plugin_names


def test_api_plugins_endpoint_includes_visible_plugins(monkeypatch):
    """Test that /api/plugins endpoint includes plugins with hidden_from_display=False."""
    from sky.server import server

    # Create test plugins
    class VisiblePlugin1(plugins.BasePlugin):

        @property
        def name(self) -> str:
            return 'VisiblePlugin1'

        @property
        def version(self) -> str:
            return '1.0.0'

        def install(self, extension_context):
            pass

    class VisiblePlugin2(plugins.BasePlugin):

        @property
        def name(self) -> str:
            return 'VisiblePlugin2'

        @property
        def version(self) -> str:
            return '2.0.0'

        def install(self, extension_context):
            pass

    # Mock get_plugins to return our test plugins
    monkeypatch.setattr(
        plugins, 'get_plugins',
        lambda: [VisiblePlugin1(), VisiblePlugin2()])

    # Create test client
    client = TestClient(server.app)

    # Make request to /api/plugins
    response = client.get('/api/plugins')

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert 'plugins' in data
    plugin_list = data['plugins']

    # Should contain both visible plugins
    assert len(plugin_list) == 2
    plugin_names = [p['name'] for p in plugin_list]
    assert 'VisiblePlugin1' in plugin_names
    assert 'VisiblePlugin2' in plugin_names
