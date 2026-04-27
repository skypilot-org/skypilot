"""Unit tests for the SkyPilot API server plugins."""

import importlib
import sys
import types
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
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
    ctx = plugins.ExtensionContext(context=plugins.PluginContext.UVICORN,
                                   app=app)

    plugins.load_plugins(ctx)
    loaded_plugins = plugins.get_plugins()

    assert len(loaded_plugins) == 1
    plugin = loaded_plugins[0]
    assert isinstance(plugin, DummyPlugin)
    assert plugin.value == 42
    assert installed['ctx'] is ctx


def test_load_plugins_filters_by_context(monkeypatch, tmp_path):
    """Plugins are skipped when their load_contexts excludes the current one."""
    module_name = 'sky_test_context_filtered_plugin'
    api_calls = {'count': 0}
    controller_calls = {'count': 0}

    class ApiOnlyPlugin(plugins.BasePlugin):
        load_contexts = frozenset({plugins.PluginContext.UVICORN})

        def install(self, extension_context):
            api_calls['count'] += 1

    class ControllerOnlyPlugin(plugins.BasePlugin):
        load_contexts = frozenset({plugins.PluginContext.CONTROLLER})

        def install(self, extension_context):
            controller_calls['count'] += 1

    ApiOnlyPlugin.__module__ = module_name
    ControllerOnlyPlugin.__module__ = module_name
    module = types.ModuleType(module_name)
    module.ApiOnlyPlugin = ApiOnlyPlugin
    module.ControllerOnlyPlugin = ControllerOnlyPlugin
    monkeypatch.setitem(sys.modules, module_name, module)

    config = {
        'plugins': [
            {
                'class': f'{module_name}.ApiOnlyPlugin'
            },
            {
                'class': f'{module_name}.ControllerOnlyPlugin'
            },
        ],
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    # API_SERVER context: only ApiOnlyPlugin runs.
    monkeypatch.setattr(plugins, '_PLUGINS', {})
    plugins.load_plugins(
        plugins.ExtensionContext(context=plugins.PluginContext.UVICORN))
    assert api_calls['count'] == 1
    assert controller_calls['count'] == 0
    loaded = plugins.get_plugins()
    assert len(loaded) == 1
    assert isinstance(loaded[0], ApiOnlyPlugin)

    # CONTROLLER context: only ControllerOnlyPlugin runs.
    monkeypatch.setattr(plugins, '_PLUGINS', {})
    plugins.load_plugins(
        plugins.ExtensionContext(context=plugins.PluginContext.CONTROLLER))
    assert api_calls['count'] == 1
    assert controller_calls['count'] == 1
    loaded = plugins.get_plugins()
    assert len(loaded) == 1
    assert isinstance(loaded[0], ControllerOnlyPlugin)

    # EXECUTOR context: neither runs (both opted out).
    monkeypatch.setattr(plugins, '_PLUGINS', {})
    plugins.load_plugins(
        plugins.ExtensionContext(context=plugins.PluginContext.EXECUTOR))
    assert api_calls['count'] == 1
    assert controller_calls['count'] == 1
    assert plugins.get_plugins() == []


def test_load_plugins_default_loads_in_all_contexts(monkeypatch, tmp_path):
    """A plugin without load_contexts overridden loads in every context."""
    module_name = 'sky_test_default_contexts_plugin'
    install_count = {'count': 0}

    class DefaultPlugin(plugins.BasePlugin):

        def install(self, extension_context):
            install_count['count'] += 1

    DefaultPlugin.__module__ = module_name
    module = types.ModuleType(module_name)
    module.DefaultPlugin = DefaultPlugin
    monkeypatch.setitem(sys.modules, module_name, module)

    config = {'plugins': [{'class': f'{module_name}.DefaultPlugin'}]}
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    for context in plugins.PluginContext:
        monkeypatch.setattr(plugins, '_PLUGINS', {})
        plugins.load_plugins(plugins.ExtensionContext(context=context))

    assert install_count['count'] == len(plugins.PluginContext)


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
