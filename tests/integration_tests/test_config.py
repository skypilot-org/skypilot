import copy
import pathlib
import textwrap

import pytest

from sky import skypilot_config
from sky.utils import common_utils

VPC_NAME = 'vpc-12345678'
PROXY_COMMAND = 'ssh -W %h:%p -i ~/.ssh/id_rsa -o StrictHostKeyChecking=no'


def _reload_config() -> None:
    skypilot_config._dict = None
    skypilot_config._try_load_config()


def _check_empty_config() -> None:
    """Check that the config is empty."""
    assert not skypilot_config.loaded()
    assert skypilot_config.get_nested(
        ('aws', 'ssh_proxy_command'), None) is None
    assert skypilot_config.get_nested(('aws', 'ssh_proxy_command'),
                                      'default') == 'default'
    with pytest.raises(RuntimeError):
        skypilot_config.set_nested(('aws', 'ssh_proxy_command'), 'value')


def _create_config_file(config_file_path: pathlib.Path) -> None:
    config_file_path.open('w').write(
        textwrap.dedent(f"""\
            aws:
                vpc_name: {VPC_NAME}
                use_internal_ips: true
                ssh_proxy_command: {PROXY_COMMAND}
            """))


def test_no_config(monkeypatch) -> None:
    """Test that the config is not loaded if the config file does not exist."""
    monkeypatch.setattr(skypilot_config, 'CONFIG_PATH', '/tmp/does_not_exist')
    _reload_config()
    _check_empty_config()


def test_empty_config(monkeypatch, tmp_path) -> None:
    """Test that the config is not loaded if the config file is empty."""
    with open(tmp_path / 'empty.yaml', 'w') as f:
        f.write('')
    monkeypatch.setattr(skypilot_config, 'CONFIG_PATH', tmp_path / 'empty.yaml')
    _reload_config()
    _check_empty_config()


def test_config_get_set_nested(monkeypatch, tmp_path) -> None:
    """Test that set_nested(), get_nested() works."""

    # Load from a config file
    config_path = tmp_path / 'config.yaml'
    _create_config_file(config_path)
    monkeypatch.setattr(skypilot_config, 'CONFIG_PATH', config_path)
    _reload_config()
    # Check that the config is loaded with the expected values
    assert skypilot_config.loaded()
    assert skypilot_config.get_nested(('aws', 'vpc_name'), None) == VPC_NAME
    assert skypilot_config.get_nested(('aws', 'use_internal_ips'), None)
    assert skypilot_config.get_nested(('aws', 'ssh_proxy_command'),
                                      None) == PROXY_COMMAND

    # Check set_nested() will copy the config dict and return a new dict
    new_config = skypilot_config.set_nested(('aws', 'ssh_proxy_command'),
                                            'new_value')
    assert new_config['aws']['ssh_proxy_command'] == 'new_value'
    assert skypilot_config.get_nested(('aws', 'ssh_proxy_command'),
                                      None) == PROXY_COMMAND

    # Check that dumping the config to a file with the new None can be reloaded
    new_config2 = skypilot_config.set_nested(('aws', 'ssh_proxy_command'), None)
    new_config_path = tmp_path / 'new_config.yaml'
    common_utils.dump_yaml(new_config_path, new_config2)
    monkeypatch.setattr(skypilot_config, 'CONFIG_PATH', new_config_path)
    _reload_config()
    assert skypilot_config.get_nested(('aws', 'vpc_name'), None) == VPC_NAME
    assert skypilot_config.get_nested(('aws', 'use_internal_ips'), None)
    assert skypilot_config.get_nested(
        ('aws', 'ssh_proxy_command'), None) is None

    # Check config with only partial keys still works
    new_config3 = copy.copy(new_config2)
    del new_config3['aws']['ssh_proxy_command']
    del new_config3['aws']['use_internal_ips']
    new_config_path = tmp_path / 'new_config3.yaml'
    common_utils.dump_yaml(new_config_path, new_config3)
    monkeypatch.setattr(skypilot_config, 'CONFIG_PATH', new_config_path)
    _reload_config()
    assert skypilot_config.get_nested(('aws', 'vpc_name'), None) == VPC_NAME
    assert skypilot_config.get_nested(('aws', 'use_internal_ips'), None) is None
    assert skypilot_config.get_nested(
        ('aws', 'ssh_proxy_command'), None) is None
    # set_nested() should still work
    new_config4 = skypilot_config.set_nested(('aws', 'ssh_proxy_command'),
                                             'new_value')
    assert new_config4['aws']['ssh_proxy_command'] == 'new_value'
    assert skypilot_config.get_nested(
        ('aws', 'ssh_proxy_command'), None) is None


def test_config_with_env(monkeypatch, tmp_path) -> None:
    """Test that the config is loaded with environment variables."""
    config_path = tmp_path / 'config.yaml'
    _create_config_file(config_path)
    monkeypatch.setenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, config_path)
    monkeypatch.setattr(skypilot_config, 'CONFIG_PATH',
                        tmp_path / 'does_not_exist')
    _reload_config()
    assert skypilot_config.loaded()
    assert skypilot_config.get_nested(('aws', 'vpc_name'), None) == VPC_NAME
    assert skypilot_config.get_nested(('aws', 'use_internal_ips'), None)
    assert skypilot_config.get_nested(('aws', 'ssh_proxy_command'),
                                      None) == PROXY_COMMAND
