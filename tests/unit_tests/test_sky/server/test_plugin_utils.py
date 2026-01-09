"""Unit tests for the SkyPilot API server plugin_utils."""

import os
import sys
import tempfile
import types
from unittest import mock

import pytest
import yaml

from sky.server import plugin_utils
from sky.server import plugins


def test_build_plugin_wheels_empty(monkeypatch, tmp_path):
    """Test that build_plugin_wheels returns empty when no plugins configured."""
    # Create empty plugins config
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump({'plugins': []}))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    wheels, combined_hash = plugin_utils.build_plugin_wheels()

    assert wheels == {}
    assert combined_hash == ''


def test_build_plugin_wheels_no_package(monkeypatch, tmp_path):
    """Test that plugins without package field are skipped."""
    # Create plugins config without package field
    config = {
        'plugins': [{
            'class': 'some_module.SomePlugin',
        }]
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    wheels, combined_hash = plugin_utils.build_plugin_wheels()

    assert wheels == {}
    assert combined_hash == ''


def test_build_plugin_wheels_with_package(monkeypatch, tmp_path):
    """Test building wheels for a plugin with package specified."""
    # Create a minimal package structure
    package_dir = tmp_path / 'test_plugin'
    package_dir.mkdir()

    # Create pyproject.toml
    pyproject_content = """
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "test_plugin"
version = "0.0.1"
"""
    (package_dir / 'pyproject.toml').write_text(pyproject_content)

    # Create a minimal Python package
    plugin_module = package_dir / 'test_plugin'
    plugin_module.mkdir()
    (plugin_module / '__init__.py').write_text('# Test plugin')

    # Create plugins config
    config = {
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
            'package': str(package_dir),
        }]
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    # Set a temporary wheel directory
    wheel_dir = tmp_path / 'wheels'
    monkeypatch.setattr(plugin_utils, 'PLUGIN_WHEEL_DIR', wheel_dir)
    monkeypatch.setattr(plugin_utils, '_PLUGIN_WHEEL_LOCK_PATH',
                        wheel_dir.parent / '.plugin_wheels_lock')

    wheels, combined_hash = plugin_utils.build_plugin_wheels()

    assert 'test_plugin' in wheels
    assert wheels['test_plugin'].exists()
    assert wheels['test_plugin'].suffix == '.whl'
    assert combined_hash != ''


def test_get_plugins_config_path_exists(monkeypatch, tmp_path):
    """Test get_plugins_config_path when file exists."""
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump({'plugins': []}))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    result = plugins.get_plugins_config_path()

    assert result == str(config_path)


def test_get_plugins_config_path_not_exists(monkeypatch, tmp_path):
    """Test get_plugins_config_path when file doesn't exist."""
    config_path = tmp_path / 'nonexistent.yaml'
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    result = plugins.get_plugins_config_path()

    assert result is None


def test_get_plugin_packages(monkeypatch, tmp_path):
    """Test get_plugin_packages returns plugin configurations."""
    config = {
        'plugins': [
            {
                'class': 'module1.Plugin1',
                'package': '/path/to/plugin1',
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

    assert len(packages) == 2
    assert packages[0]['class'] == 'module1.Plugin1'
    assert packages[0]['package'] == '/path/to/plugin1'
    assert packages[1]['class'] == 'module2.Plugin2'
    assert 'package' not in packages[1]


def test_get_plugin_mounts_and_commands(monkeypatch, tmp_path):
    """Test get_plugin_mounts_and_commands returns consistent mounts and cmds."""
    # Create a minimal package structure
    package_dir = tmp_path / 'test_plugin'
    package_dir.mkdir()

    pyproject_content = """
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "test_plugin"
version = "0.0.1"
"""
    (package_dir / 'pyproject.toml').write_text(pyproject_content)

    plugin_module = package_dir / 'test_plugin'
    plugin_module.mkdir()
    (plugin_module / '__init__.py').write_text('# Test plugin')

    config = {
        'plugins': [{
            'class': 'test_plugin.TestPlugin',
            'package': str(package_dir),
        }]
    }
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))

    wheel_dir = tmp_path / 'wheels'
    monkeypatch.setattr(plugin_utils, 'PLUGIN_WHEEL_DIR', wheel_dir)
    monkeypatch.setattr(plugin_utils, '_PLUGIN_WHEEL_LOCK_PATH',
                        wheel_dir.parent / '.plugin_wheels_lock')

    file_mounts, commands = plugin_utils.get_plugin_mounts_and_commands()

    # Check file mounts
    assert len(file_mounts) == 1
    remote_path = list(file_mounts.keys())[0]
    assert '~/.sky/plugins/wheels' in remote_path

    # Check commands
    assert commands != ''
    assert 'pip install' in commands
    # Should contain the actual wheel filename with version, not just *.whl
    assert 'test_plugin-0.0.1' in commands
    assert '.whl' in commands
    # Path should use ~ for shell expansion (not quoted)
    assert '~/.sky/plugins/wheels' in commands

    # Verify that the hash in file mounts matches the hash in commands
    # Extract the hash from the remote path (format: ~/.sky/plugins/wheels/<hash>)
    mount_hash = remote_path.split('/')[-1]
    assert mount_hash in commands, (
        f'Hash mismatch: mount hash {mount_hash} not found in commands')
