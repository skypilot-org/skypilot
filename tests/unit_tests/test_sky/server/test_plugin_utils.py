"""Unit tests for the SkyPilot API server plugin_utils."""

import os
import tempfile

import pytest
import yaml

from sky.server import plugin_utils
from sky.server import plugins


def test_get_plugin_packages(monkeypatch, tmp_path):
    """Test get_plugin_packages returns plugin configurations."""
    config = {
        'controller_wheel_path': 'dist/plugins-0.0.1-py3-none-any.whl',
        'plugins': [
            {
                'class': 'module1.Plugin1',
                'upload_to_controller': True,
            },
            {
                'class': 'module2.Plugin2',
                'parameters': {
                    'param': 'value'
                },
            },
        ]
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    packages = plugins.get_plugin_packages()
    wheel_path = plugins.get_controller_wheel_path()

    assert len(packages) == 2
    assert packages[0]['class'] == 'module1.Plugin1'
    assert packages[0]['upload_to_controller'] is True
    assert packages[1]['class'] == 'module2.Plugin2'
    assert 'upload_to_controller' not in packages[1]
    assert wheel_path == 'dist/plugins-0.0.1-py3-none-any.whl'


def test_get_plugin_mounts_and_commands(monkeypatch, tmp_path):
    """Test get_plugin_mounts_and_commands returns consistent mounts and cmds."""
    # Create a prebuilt wheel file
    wheel_file = tmp_path / 'test_plugin-0.0.1-py3-none-any.whl'
    wheel_file.write_bytes(b'fake wheel content')

    config = {
        'controller_wheel_path': str(wheel_file),
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
            'upload_to_controller': True,
        }]
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    file_mounts, commands = plugin_utils.get_plugin_mounts_and_commands()

    # Check file mounts
    assert len(file_mounts) == 1
    remote_path = list(file_mounts.keys())[0]
    assert '~/.sky/plugins/wheels' in remote_path
    assert 'test_plugin-0.0.1-py3-none-any.whl' in remote_path
    assert file_mounts[remote_path] == str(wheel_file)

    # Check commands
    assert commands != ''
    assert 'pip install' in commands or 'uv pip install' in commands
    # Should contain the actual wheel filename
    assert 'test_plugin-0.0.1-py3-none-any.whl' in commands
    # Path should use ~ for shell expansion (not quoted)
    assert '~/.sky/plugins/wheels' in commands


def test_get_plugin_mounts_and_commands_no_wheel_path(monkeypatch, tmp_path):
    """Test get_plugin_mounts_and_commands when controller_wheel_path is missing."""
    config = {
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
            'upload_to_controller': True,
            # Missing controller_wheel_path
        }]
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    file_mounts, commands = plugin_utils.get_plugin_mounts_and_commands()

    # Should return empty since controller_wheel_path is missing
    assert file_mounts == {}
    assert commands == ''


def test_get_plugin_mounts_and_commands_invalid_wheel_path(
        monkeypatch, tmp_path):
    """Test get_plugin_mounts_and_commands with invalid wheel path."""
    config = {
        'controller_wheel_path': str(tmp_path / 'nonexistent.whl'),
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
            'upload_to_controller': True,
        }]
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    file_mounts, commands = plugin_utils.get_plugin_mounts_and_commands()

    # Should return empty since wheel file doesn't exist
    assert file_mounts == {}
    assert commands == ''


def test_get_plugin_mounts_and_commands_not_whl_file(monkeypatch, tmp_path):
    """Test get_plugin_mounts_and_commands with non-.whl file."""
    # Create a file that's not a .whl
    non_wheel_file = tmp_path / 'test_plugin.txt'
    non_wheel_file.write_text('not a wheel')

    config = {
        'controller_wheel_path': str(non_wheel_file),
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
            'upload_to_controller': True,
        }]
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    file_mounts, commands = plugin_utils.get_plugin_mounts_and_commands()

    # Should return empty since file doesn't have .whl extension
    assert file_mounts == {}
    assert commands == ''


def test_get_filtered_plugins_config_path_empty(monkeypatch, tmp_path):
    """Test get_filtered_plugins_config_path with no plugins."""
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump({'plugins': []}))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    result = plugin_utils.get_filtered_plugins_config_path()

    assert result is None


def test_get_filtered_plugins_config_path_no_upload_flag(monkeypatch, tmp_path):
    """Test get_filtered_plugins_config_path with plugins without upload_to_controller."""
    config = {
        'plugins': [
            {
                'class': 'module1.Plugin1',
            },
            {
                'class': 'module2.Plugin2',
                'parameters': {
                    'key': 'value'
                },
            },
        ]
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    result = plugin_utils.get_filtered_plugins_config_path()

    # No plugins with upload_to_controller - should return None
    assert result is None


def test_get_filtered_plugins_config_path_with_upload_flag(
        monkeypatch, tmp_path):
    """Test get_filtered_plugins_config_path with plugins that have upload_to_controller."""
    config = {
        'controller_wheel_path': 'dist/test_plugin-0.0.1-py3-none-any.whl',
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
            'upload_to_controller': True,
        }]
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    result = plugin_utils.get_filtered_plugins_config_path()

    assert result is not None
    assert os.path.exists(result)

    # Read and verify the filtered config
    with open(result) as f:
        filtered_config = yaml.safe_load(f)

    assert len(filtered_config['plugins']) == 1
    assert filtered_config['plugins'][0]['class'] == 'test_plugin.TestPlugin'
    # Filtered config should not include upload_to_controller
    assert 'upload_to_controller' not in filtered_config['plugins'][0]


def test_get_filtered_plugins_config_path_mixed(monkeypatch, tmp_path):
    """Test get_filtered_plugins_config_path with mixed plugins."""
    config = {
        'controller_wheel_path': 'dist/plugins-0.0.1-py3-none-any.whl',
        'plugins': [
            {
                'class': 'module1.Plugin1',
                'upload_to_controller': True,
            },
            {
                # Plugin without upload_to_controller - should NOT be included
                'class': 'module2.Plugin2',
            },
            {
                'class': 'module3.Plugin3',
                'upload_to_controller': True,
                'parameters': {
                    'key': 'value'
                },
            },
        ]
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    result = plugin_utils.get_filtered_plugins_config_path()

    assert result is not None
    assert os.path.exists(result)

    # Read and verify the filtered config
    with open(result) as f:
        filtered_config = yaml.safe_load(f)

    # Should only contain plugins with upload_to_controller=True
    assert len(filtered_config['plugins']) == 2
    assert filtered_config['plugins'][0]['class'] == 'module1.Plugin1'
    assert 'upload_to_controller' not in filtered_config['plugins'][0]
    assert filtered_config['plugins'][1]['class'] == 'module3.Plugin3'
    assert 'upload_to_controller' not in filtered_config['plugins'][1]
    assert filtered_config['plugins'][1]['parameters'] == {'key': 'value'}

    # Verify Plugin2 (without upload_to_controller) is NOT in the filtered config
    for plugin in filtered_config['plugins']:
        assert plugin['class'] != 'module2.Plugin2'


def test_get_filtered_plugins_config_path_no_config(monkeypatch, tmp_path):
    """Test get_filtered_plugins_config_path when no config file exists."""
    config_path = tmp_path / 'nonexistent.yaml'
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    result = plugin_utils.get_filtered_plugins_config_path()

    assert result is None
