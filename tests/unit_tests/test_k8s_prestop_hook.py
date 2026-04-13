"""Tests for K8s preStop hook populated from autostop_config.hook."""
import jsonschema
import pytest

from sky.resources import AutostopConfig
from sky.resources import Resources
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


class TestTerminationHookSchema:
    """Tests for the top-level `termination_hook` key in resources schema."""

    @staticmethod
    def _get_resources_schema():
        return schemas.get_resources_schema()

    def test_valid_termination_hook_with_timeout(self):
        config = {
            'termination_hook': {
                'command': 'save-checkpoint.sh',
                'timeout': 120,
            }
        }
        jsonschema.validate(config, self._get_resources_schema())

    def test_valid_termination_hook_command_only(self):
        config = {'termination_hook': {'command': 'save-checkpoint.sh'}}
        jsonschema.validate(config, self._get_resources_schema())

    def test_termination_hook_missing_command(self):
        config = {'termination_hook': {'timeout': 60}}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, self._get_resources_schema())

    def test_termination_hook_extra_key_rejected(self):
        config = {
            'termination_hook': {
                'command': 'x',
                'not_a_field': 1,
            }
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, self._get_resources_schema())

    def test_termination_hook_zero_timeout_rejected(self):
        config = {
            'termination_hook': {
                'command': 'x',
                'timeout': 0,
            }
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, self._get_resources_schema())


class TestTerminationHookResources:
    """Tests for Resources parsing of termination_hook and backward compat."""

    def test_termination_hook_merged_into_autostop_config(self):
        r = Resources(infra='kubernetes',
                      termination_hook={
                          'command': 'save.sh',
                          'timeout': 90,
                      })
        assert r.autostop_config is not None
        assert r.autostop_config.hook == 'save.sh'
        assert r.autostop_config.hook_timeout == 90

    def test_termination_hook_without_timeout(self):
        r = Resources(infra='kubernetes',
                      termination_hook={'command': 'save.sh'})
        assert r.autostop_config is not None
        assert r.autostop_config.hook == 'save.sh'
        assert r.autostop_config.hook_timeout is None

    def test_termination_hook_conflict_with_autostop_hook(self):
        with pytest.raises(ValueError, match='termination_hook'):
            Resources(infra='kubernetes',
                      autostop={
                          'idle_minutes': 5,
                          'hook': 'a.sh',
                      },
                      termination_hook={'command': 'b.sh'})

    def test_termination_hook_matches_autostop_hook_no_conflict(self):
        # Same hook specified on both should not error.
        r = Resources(infra='kubernetes',
                      autostop={
                          'idle_minutes': 5,
                          'hook': 'a.sh',
                          'hook_timeout': 10,
                      },
                      termination_hook={
                          'command': 'a.sh',
                          'timeout': 10,
                      })
        assert r.autostop_config is not None
        assert r.autostop_config.hook == 'a.sh'
        assert r.autostop_config.hook_timeout == 10

    def test_from_yaml_config_round_trip(self):
        yaml_config = {
            'infra': 'kubernetes',
            'termination_hook': {
                'command': 'save.sh',
                'timeout': 120,
            },
        }
        resources_set = Resources.from_yaml_config(yaml_config)
        r = next(iter(resources_set))
        assert r.autostop_config.hook == 'save.sh'
        assert r.autostop_config.hook_timeout == 120
        round_tripped = r.to_yaml_config()
        assert round_tripped['termination_hook'] == {
            'command': 'save.sh',
            'timeout': 120,
        }

    def test_backward_compat_autostop_hook_still_works(self):
        r = Resources(infra='kubernetes',
                      autostop={
                          'idle_minutes': 5,
                          'hook': 'legacy.sh',
                          'hook_timeout': 30,
                      })
        assert r.autostop_config.hook == 'legacy.sh'
        assert r.autostop_config.hook_timeout == 30
        # to_yaml_config now also emits termination_hook for convenience.
        yc = r.to_yaml_config()
        assert yc['termination_hook'] == {
            'command': 'legacy.sh',
            'timeout': 30,
        }
