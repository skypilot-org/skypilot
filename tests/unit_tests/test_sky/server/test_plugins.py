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


def test_load_plugin_viewer_allowlist(monkeypatch, tmp_path):
    """load_plugin_viewer_allowlist reads viewer_allowlist from each plugin."""
    module_name = 'sky_test_viewer_allowlist_plugin'

    class AllowlistPlugin(plugins.BasePlugin):

        @property
        def viewer_allowlist(self):
            return [
                plugins.RBACRule(path='/plugins/api/foo/list', method='GET'),
                plugins.RBACRule(path='/plugins/api/foo/status', method='POST'),
            ]

        def install(self, extension_context):
            pass

    AllowlistPlugin.__module__ = module_name
    module = types.ModuleType(module_name)
    module.AllowlistPlugin = AllowlistPlugin
    monkeypatch.setitem(sys.modules, module_name, module)

    config = {
        'plugins': [{
            'class': f'{module_name}.AllowlistPlugin',
        }],
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))
    # Reset the module-level cache so this test does not see leftover
    # state from other tests.
    monkeypatch.setattr(plugins, '_PLUGIN_VIEWER_ALLOWLIST', [])

    result = plugins.load_plugin_viewer_allowlist()

    assert {'path': '/plugins/api/foo/list', 'method': 'GET'} in result
    assert {'path': '/plugins/api/foo/status', 'method': 'POST'} in result
    assert len(result) == 2
    # Cached for the getter.
    assert plugins.get_plugin_viewer_allowlist() == result


def test_load_plugin_viewer_allowlist_default_empty(monkeypatch, tmp_path):
    """Plugins that do not override viewer_allowlist contribute nothing."""
    module_name = 'sky_test_viewer_allowlist_default_plugin'

    class NoOverridePlugin(plugins.BasePlugin):

        def install(self, extension_context):
            pass

    NoOverridePlugin.__module__ = module_name
    module = types.ModuleType(module_name)
    module.NoOverridePlugin = NoOverridePlugin
    monkeypatch.setitem(sys.modules, module_name, module)

    config = {
        'plugins': [{
            'class': f'{module_name}.NoOverridePlugin',
        }],
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))
    monkeypatch.setattr(plugins, '_PLUGIN_VIEWER_ALLOWLIST', [])

    result = plugins.load_plugin_viewer_allowlist()
    assert not result


def test_install_late_runs_after_every_install(monkeypatch, tmp_path):
    """The late pass is what lets a plugin's middleware be innermost.

    Middleware order is install order, so a plugin listed before another
    cannot get inside that other plugin's middleware from `install`. Assert
    the guarantee the late pass exists to provide: every `install` completes
    before any `install_late` starts.
    """
    module_name = 'sky_test_late_plugin'
    calls = []

    class FirstPlugin(plugins.BasePlugin):

        def install(self, extension_context):
            del extension_context
            calls.append('first.install')

        def install_late(self, extension_context):
            del extension_context
            calls.append('first.install_late')

    class SecondPlugin(plugins.BasePlugin):

        def install(self, extension_context):
            del extension_context
            calls.append('second.install')

    FirstPlugin.__module__ = module_name
    SecondPlugin.__module__ = module_name
    module = types.ModuleType(module_name)
    module.FirstPlugin = FirstPlugin
    module.SecondPlugin = SecondPlugin
    monkeypatch.setitem(sys.modules, module_name, module)

    config = {
        'plugins': [
            {
                'class': f'{module_name}.FirstPlugin'
            },
            {
                'class': f'{module_name}.SecondPlugin'
            },
        ],
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))
    monkeypatch.setattr(plugins, '_PLUGINS', {})

    plugins.load_plugins(
        plugins.ExtensionContext(context=plugins.PluginContext.UVICORN,
                                 app=FastAPI()))

    # The plugin declaring install_late is listed *first*, so this ordering
    # only holds because the late pass is a separate pass.
    assert calls == ['first.install', 'second.install', 'first.install_late']


def test_install_late_skips_plugins_from_an_earlier_load(monkeypatch, tmp_path):
    """Only what this call installed gets a late install.

    `_PLUGINS` is module-global and never cleared, and a process can load
    plugins more than once (MAIN, then UVICORN in-process). Without care the
    second load's late pass would reach the first load's instances, including
    ones whose `load_contexts` excludes the context now being loaded.
    """
    module_name = 'sky_test_context_late_plugin'
    late_calls = []

    class MainOnlyPlugin(plugins.BasePlugin):
        load_contexts = frozenset({plugins.PluginContext.MAIN})

        def install(self, extension_context):
            del extension_context

        def install_late(self, extension_context):
            late_calls.append(extension_context.context)

    MainOnlyPlugin.__module__ = module_name
    module = types.ModuleType(module_name)
    module.MainOnlyPlugin = MainOnlyPlugin
    monkeypatch.setitem(sys.modules, module_name, module)

    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(
        yaml.safe_dump(
            {'plugins': [{
                'class': f'{module_name}.MainOnlyPlugin'
            }]}))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))
    monkeypatch.setattr(plugins, '_PLUGINS', {})

    plugins.load_plugins(
        plugins.ExtensionContext(context=plugins.PluginContext.MAIN))
    assert late_calls == [plugins.PluginContext.MAIN]

    # Same process, second load in a context this plugin opted out of. It stays
    # in `_PLUGINS` from the first load, but must not be installed again.
    plugins.load_plugins(
        plugins.ExtensionContext(context=plugins.PluginContext.UVICORN,
                                 app=FastAPI()))
    assert late_calls == [plugins.PluginContext.MAIN]


def test_install_late_defaults_to_noop(monkeypatch, tmp_path):
    """A plugin that does not override install_late still loads."""
    module_name = 'sky_test_no_late_plugin'

    class PlainPlugin(plugins.BasePlugin):

        def install(self, extension_context):
            del extension_context

    PlainPlugin.__module__ = module_name
    module = types.ModuleType(module_name)
    module.PlainPlugin = PlainPlugin
    monkeypatch.setitem(sys.modules, module_name, module)

    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(
        yaml.safe_dump({'plugins': [{
            'class': f'{module_name}.PlainPlugin'
        }]}))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))
    monkeypatch.setattr(plugins, '_PLUGINS', {})

    plugins.load_plugins(
        plugins.ExtensionContext(context=plugins.PluginContext.UVICORN,
                                 app=FastAPI()))

    assert len(plugins.get_plugins()) == 1
