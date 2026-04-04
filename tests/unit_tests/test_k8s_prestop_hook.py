"""Tests for K8s preStop hook populated from autostop_config.hook."""
import jsonschema
import pytest

from sky.resources import AutostopConfig
from sky.utils import schemas


class TestAutostopHookConfig:
    """Tests for AutostopConfig hook fields used by K8s preStop."""

    def test_hook_from_dict(self):
        config = AutostopConfig.from_yaml_config({
            'idle_minutes': 10,
            'hook': 'echo "saving"',
            'hook_timeout': 60,
        })
        assert config is not None
        assert config.hook == 'echo "saving"'
        assert config.hook_timeout == 60
        assert config.idle_minutes == 10

    def test_hook_without_timeout(self):
        config = AutostopConfig.from_yaml_config({
            'hook': 'save.sh',
        })
        assert config is not None
        assert config.hook == 'save.sh'
        assert config.hook_timeout is None  # default

    def test_no_hook(self):
        config = AutostopConfig.from_yaml_config({
            'idle_minutes': 10,
        })
        assert config is not None
        assert config.hook is None
        assert config.hook_timeout is None

    def test_round_trip_with_hook(self):
        original = {
            'idle_minutes': 10,
            'hook': 'save.sh',
            'hook_timeout': 120,
        }
        config = AutostopConfig.from_yaml_config(original)
        assert config is not None
        yaml_config = config.to_yaml_config()
        assert yaml_config['hook'] == 'save.sh'
        assert yaml_config['hook_timeout'] == 120

    def test_round_trip_without_hook(self):
        original = {'idle_minutes': 10}
        config = AutostopConfig.from_yaml_config(original)
        assert config is not None
        yaml_config = config.to_yaml_config()
        assert 'hook' not in yaml_config
        assert 'hook_timeout' not in yaml_config


class TestAutostopHookSchema:
    """Tests for autostop hook in the resources YAML schema."""

    @staticmethod
    def _get_resources_schema():
        return schemas.get_resources_schema()

    def test_valid_autostop_with_hook(self):
        config = {
            'autostop': {
                'idle_minutes': 10,
                'hook': 'echo "saving checkpoint"',
                'hook_timeout': 600,
            }
        }
        jsonschema.validate(config, self._get_resources_schema())

    def test_valid_autostop_hook_only(self):
        config = {'autostop': {'hook': 'echo hi'}}
        jsonschema.validate(config, self._get_resources_schema())

    def test_invalid_negative_hook_timeout(self):
        config = {
            'autostop': {
                'hook': 'echo hi',
                'hook_timeout': -1,
            }
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, self._get_resources_schema())

    def test_invalid_zero_hook_timeout(self):
        config = {
            'autostop': {
                'hook': 'echo hi',
                'hook_timeout': 0,
            }
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, self._get_resources_schema())

    def test_preemption_key_rejected(self):
        """The old preemption key should now be rejected."""
        config = {'preemption': {'hook': 'echo hi',}}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, self._get_resources_schema())
