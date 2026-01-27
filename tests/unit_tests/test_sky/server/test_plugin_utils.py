"""Unit tests for the SkyPilot API server plugin_utils."""

import os
import tempfile

import pytest
import yaml

from sky.server import plugin_utils
from sky.server import plugins


def test_get_remote_plugin_packages(monkeypatch, tmp_path):
    """Test get_remote_plugin_packages returns remote plugin configurations."""
    remote_config = {
        'plugins': [
            {
                'class': 'module1.RemotePlugin1',
            },
            {
                'class': 'module2.RemotePlugin2',
                'parameters': {
                    'param': 'value'
                },
            },
        ]
    }
    remote_config_path = tmp_path / 'remote_plugins.yaml'
    remote_config_path.write_text(yaml.safe_dump(remote_config))
    monkeypatch.setenv(plugins._REMOTE_PLUGINS_CONFIG_ENV_VAR,
                       str(remote_config_path))

    plugin_config = {'controller_wheel_path': 'dist', 'plugins': []}
    plugin_config_path = tmp_path / 'plugins.yaml'
    plugin_config_path.write_text(yaml.safe_dump(plugin_config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(plugin_config_path))

    packages = plugins.get_remote_plugin_packages()
    wheel_path = plugins.get_remote_controller_wheel_path()

    assert len(packages) == 2
    assert packages[0]['class'] == 'module1.RemotePlugin1'
    assert 'upload_to_controller' not in packages[0]
    assert packages[1]['class'] == 'module2.RemotePlugin2'
    assert 'upload_to_controller' not in packages[1]
    assert wheel_path == 'dist'


def test_get_plugin_mounts_and_commands(monkeypatch, tmp_path):
    """Test get_plugin_mounts_and_commands returns consistent mounts and cmds."""
    # Create a directory with wheel files
    wheel_dir = tmp_path / 'wheels'
    wheel_dir.mkdir()
    wheel_file1 = wheel_dir / 'test_plugin-0.0.1-py3-none-any.whl'
    wheel_file1.write_bytes(b'fake wheel content 1')
    wheel_file2 = wheel_dir / 'another_plugin-0.0.2-py3-none-any.whl'
    wheel_file2.write_bytes(b'fake wheel content 2')

    remote_config = {
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
        }]
    }
    remote_config_path = tmp_path / 'remote_plugins.yaml'
    remote_config_path.write_text(yaml.safe_dump(remote_config))
    monkeypatch.setenv(plugins._REMOTE_PLUGINS_CONFIG_ENV_VAR,
                       str(remote_config_path))

    plugin_config = {'controller_wheel_path': str(wheel_dir), 'plugins': []}
    plugin_config_path = tmp_path / 'plugins.yaml'
    plugin_config_path.write_text(yaml.safe_dump(plugin_config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(plugin_config_path))

    file_mounts, commands = plugin_utils.get_plugin_mounts_and_commands()

    # Check file mounts - should have both wheel files
    assert len(file_mounts) == 2
    remote_paths = list(file_mounts.keys())
    assert all('~/.sky/plugins/wheels' in path for path in remote_paths)
    assert any(
        'test_plugin-0.0.1-py3-none-any.whl' in path for path in remote_paths)
    assert any('another_plugin-0.0.2-py3-none-any.whl' in path
               for path in remote_paths)
    assert str(wheel_file1) in file_mounts.values()
    assert str(wheel_file2) in file_mounts.values()

    # Check commands
    assert commands != ''
    assert 'pip install' in commands or 'uv pip install' in commands
    # Should contain both wheel filenames
    assert 'test_plugin-0.0.1-py3-none-any.whl' in commands
    assert 'another_plugin-0.0.2-py3-none-any.whl' in commands
    # Path should use ~ for shell expansion (not quoted)
    assert '~/.sky/plugins/wheels' in commands
    # Should have && between commands
    assert ' && ' in commands


def test_get_plugin_mounts_and_commands_no_wheel_path(monkeypatch, tmp_path):
    """Test get_plugin_mounts_and_commands when controller_wheel_path is missing."""
    remote_config = {
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
        }]
    }
    remote_config_path = tmp_path / 'remote_plugins.yaml'
    remote_config_path.write_text(yaml.safe_dump(remote_config))
    monkeypatch.setenv(plugins._REMOTE_PLUGINS_CONFIG_ENV_VAR,
                       str(remote_config_path))

    # plugins.yaml without controller_wheel_path
    plugin_config = {'plugins': []}
    plugin_config_path = tmp_path / 'plugins.yaml'
    plugin_config_path.write_text(yaml.safe_dump(plugin_config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(plugin_config_path))

    file_mounts, commands = plugin_utils.get_plugin_mounts_and_commands()

    # Should return empty since controller_wheel_path is missing
    assert file_mounts == {}
    assert commands == ''


def test_get_plugin_mounts_and_commands_invalid_wheel_path(
        monkeypatch, tmp_path):
    """Test get_plugin_mounts_and_commands with invalid wheel directory path."""
    remote_config = {
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
        }]
    }
    remote_config_path = tmp_path / 'remote_plugins.yaml'
    remote_config_path.write_text(yaml.safe_dump(remote_config))
    monkeypatch.setenv(plugins._REMOTE_PLUGINS_CONFIG_ENV_VAR,
                       str(remote_config_path))

    plugin_config = {
        'controller_wheel_path': str(tmp_path / 'nonexistent_dir'),
        'plugins': []
    }
    plugin_config_path = tmp_path / 'plugins.yaml'
    plugin_config_path.write_text(yaml.safe_dump(plugin_config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(plugin_config_path))

    file_mounts, commands = plugin_utils.get_plugin_mounts_and_commands()

    # Should return empty since directory doesn't exist
    assert file_mounts == {}
    assert commands == ''


def test_get_plugin_mounts_and_commands_not_directory(monkeypatch, tmp_path):
    """Test get_plugin_mounts_and_commands when path is not a directory."""
    # Create a file instead of a directory
    wheel_file = tmp_path / 'test_plugin.whl'
    wheel_file.write_bytes(b'fake wheel content')

    remote_config = {
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
        }]
    }
    remote_config_path = tmp_path / 'remote_plugins.yaml'
    remote_config_path.write_text(yaml.safe_dump(remote_config))
    monkeypatch.setenv(plugins._REMOTE_PLUGINS_CONFIG_ENV_VAR,
                       str(remote_config_path))

    plugin_config = {'controller_wheel_path': str(wheel_file), 'plugins': []}
    plugin_config_path = tmp_path / 'plugins.yaml'
    plugin_config_path.write_text(yaml.safe_dump(plugin_config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(plugin_config_path))

    file_mounts, commands = plugin_utils.get_plugin_mounts_and_commands()

    # Should return empty since path is not a directory
    assert file_mounts == {}
    assert commands == ''


def test_get_plugin_mounts_and_commands_no_whl_files(monkeypatch, tmp_path):
    """Test get_plugin_mounts_and_commands when directory has no .whl files."""
    # Create a directory with no .whl files
    wheel_dir = tmp_path / 'wheels'
    wheel_dir.mkdir()
    # Create a non-wheel file
    (wheel_dir / 'test_plugin.txt').write_text('not a wheel')

    remote_config = {
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
        }]
    }
    remote_config_path = tmp_path / 'remote_plugins.yaml'
    remote_config_path.write_text(yaml.safe_dump(remote_config))
    monkeypatch.setenv(plugins._REMOTE_PLUGINS_CONFIG_ENV_VAR,
                       str(remote_config_path))

    plugin_config = {'controller_wheel_path': str(wheel_dir), 'plugins': []}
    plugin_config_path = tmp_path / 'plugins.yaml'
    plugin_config_path.write_text(yaml.safe_dump(plugin_config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(plugin_config_path))

    file_mounts, commands = plugin_utils.get_plugin_mounts_and_commands()

    # Should return empty since no .whl files found
    assert file_mounts == {}
    assert commands == ''


def test_get_filtered_plugins_config_path_empty(monkeypatch, tmp_path):
    """Test get_filtered_plugins_config_path with no plugins."""
    remote_config_path = tmp_path / 'remote_plugins.yaml'
    remote_config_path.write_text(yaml.safe_dump({'plugins': []}))
    monkeypatch.setenv(plugins._REMOTE_PLUGINS_CONFIG_ENV_VAR,
                       str(remote_config_path))

    result = plugin_utils.get_filtered_plugins_config_path()

    assert result is None


def test_get_filtered_plugins_config_path_no_remote_config(
        monkeypatch, tmp_path):
    """Test get_filtered_plugins_config_path when remote_plugins.yaml doesn't exist."""
    # Set a path that doesn't exist
    remote_config_path = tmp_path / 'nonexistent_remote_plugins.yaml'
    monkeypatch.setenv(plugins._REMOTE_PLUGINS_CONFIG_ENV_VAR,
                       str(remote_config_path))

    result = plugin_utils.get_filtered_plugins_config_path()

    # No remote_plugins.yaml - should return None
    assert result is None


def test_get_filtered_plugins_config_path_with_remote_config(
        monkeypatch, tmp_path):
    """Test get_filtered_plugins_config_path with remote_plugins.yaml."""
    remote_config = {
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
        }]
    }
    remote_config_path = tmp_path / 'remote_plugins.yaml'
    remote_config_path.write_text(yaml.safe_dump(remote_config))
    monkeypatch.setenv(plugins._REMOTE_PLUGINS_CONFIG_ENV_VAR,
                       str(remote_config_path))

    result = plugin_utils.get_filtered_plugins_config_path()

    assert result is not None
    assert result == str(remote_config_path)
    assert os.path.exists(result)

    # Read and verify the config (should be the original remote_plugins.yaml)
    with open(result) as f:
        config = yaml.safe_load(f)

    assert len(config['plugins']) == 1
    assert config['plugins'][0]['class'] == 'test_plugin.TestPlugin'
    # controller_wheel_path should not be in remote_plugins.yaml anymore
    assert 'controller_wheel_path' not in config
    # Config should not include upload_to_controller (not in schema anymore)
    assert 'upload_to_controller' not in config['plugins'][0]


def test_get_filtered_plugins_config_path_multiple_plugins(
        monkeypatch, tmp_path):
    """Test get_filtered_plugins_config_path with multiple plugins in remote_plugins.yaml."""
    remote_config = {
        'plugins': [
            {
                'class': 'module1.Plugin1',
            },
            {
                'class': 'module3.Plugin3',
                'parameters': {
                    'key': 'value'
                },
            },
        ]
    }
    remote_config_path = tmp_path / 'remote_plugins.yaml'
    remote_config_path.write_text(yaml.safe_dump(remote_config))
    monkeypatch.setenv(plugins._REMOTE_PLUGINS_CONFIG_ENV_VAR,
                       str(remote_config_path))

    result = plugin_utils.get_filtered_plugins_config_path()

    assert result is not None
    assert result == str(remote_config_path)
    assert os.path.exists(result)

    # Read and verify the config (should be the original remote_plugins.yaml)
    with open(result) as f:
        config = yaml.safe_load(f)

    # Should contain all plugins from remote_plugins.yaml
    assert len(config['plugins']) == 2
    assert config['plugins'][0]['class'] == 'module1.Plugin1'
    assert 'upload_to_controller' not in config['plugins'][0]
    assert config['plugins'][1]['class'] == 'module3.Plugin3'
    assert 'upload_to_controller' not in config['plugins'][1]
    assert config['plugins'][1]['parameters'] == {'key': 'value'}
    # controller_wheel_path should not be in remote_plugins.yaml anymore
    assert 'controller_wheel_path' not in config


def test_get_filtered_plugins_config_path_no_config(monkeypatch, tmp_path):
    """Test get_filtered_plugins_config_path when remote_plugins.yaml doesn't exist."""
    remote_config_path = tmp_path / 'nonexistent.yaml'
    monkeypatch.setenv(plugins._REMOTE_PLUGINS_CONFIG_ENV_VAR,
                       str(remote_config_path))

    result = plugin_utils.get_filtered_plugins_config_path()

    assert result is None
