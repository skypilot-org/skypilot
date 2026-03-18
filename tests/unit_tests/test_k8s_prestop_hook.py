"""Tests for K8s preStop preemption hook (PreemptionConfig and schema)."""
import jsonschema
import pytest

from sky.resources import PreemptionConfig
from sky.utils import schemas


class TestPreemptionConfigFromYaml:
    """Tests for PreemptionConfig.from_yaml_config()."""

    def test_none_returns_none(self):
        assert PreemptionConfig.from_yaml_config(None) is None

    def test_non_dict_returns_none(self):
        assert PreemptionConfig.from_yaml_config('string') is None
        assert PreemptionConfig.from_yaml_config(42) is None

    def test_dict_without_hook_returns_none(self):
        assert PreemptionConfig.from_yaml_config({}) is None
        assert PreemptionConfig.from_yaml_config({'hook_timeout': 60}) is None

    def test_basic_hook(self):
        config = PreemptionConfig.from_yaml_config({'hook': 'echo "preempted"'})
        assert config is not None
        assert config.hook == 'echo "preempted"'
        assert config.hook_timeout == 300  # default

    def test_hook_with_timeout(self):
        config = PreemptionConfig.from_yaml_config({
            'hook': 'save.sh',
            'hook_timeout': 600,
        })
        assert config is not None
        assert config.hook == 'save.sh'
        assert config.hook_timeout == 600

    def test_multiline_hook(self):
        hook_script = 'echo "saving"\ncp checkpoint.pt s3://bucket/\n'
        config = PreemptionConfig.from_yaml_config({'hook': hook_script})
        assert config is not None
        assert config.hook == hook_script


class TestPreemptionConfigEffectiveGracePeriod:
    """Tests for PreemptionConfig.effective_grace_period."""

    def test_with_hook_uses_hook_timeout(self):
        config = PreemptionConfig(hook='echo hi', hook_timeout=600)
        assert config.effective_grace_period == 600

    def test_with_hook_default_timeout(self):
        config = PreemptionConfig(hook='echo hi')
        assert config.effective_grace_period == 300

    def test_without_hook_returns_k8s_default(self):
        config = PreemptionConfig(hook=None)
        assert config.effective_grace_period == 30


class TestPreemptionConfigToYaml:
    """Tests for PreemptionConfig.to_yaml_config() round-trip."""

    def test_no_hook_returns_none(self):
        config = PreemptionConfig(hook=None)
        assert config.to_yaml_config() is None

    def test_hook_with_default_timeout(self):
        config = PreemptionConfig(hook='echo hi', hook_timeout=300)
        yaml_config = config.to_yaml_config()
        assert yaml_config == {'hook': 'echo hi'}
        # hook_timeout omitted when default

    def test_hook_with_custom_timeout(self):
        config = PreemptionConfig(hook='echo hi', hook_timeout=600)
        yaml_config = config.to_yaml_config()
        assert yaml_config == {'hook': 'echo hi', 'hook_timeout': 600}

    def test_round_trip(self):
        original = {'hook': 'save.sh', 'hook_timeout': 120}
        config = PreemptionConfig.from_yaml_config(original)
        assert config is not None
        yaml_config = config.to_yaml_config()
        assert yaml_config == original

    def test_round_trip_default_timeout(self):
        original = {'hook': 'save.sh'}
        config = PreemptionConfig.from_yaml_config(original)
        assert config is not None
        yaml_config = config.to_yaml_config()
        assert yaml_config == original


class TestPreemptionSchema:
    """Tests for preemption section in the resources YAML schema."""

    @staticmethod
    def _get_resources_schema():
        return schemas.get_resources_schema()

    def test_valid_preemption_config(self):
        config = {
            'preemption': {
                'hook': 'echo "saving checkpoint"',
                'hook_timeout': 600,
            }
        }
        jsonschema.validate(config, self._get_resources_schema())

    def test_valid_preemption_hook_only(self):
        config = {'preemption': {'hook': 'echo hi',}}
        jsonschema.validate(config, self._get_resources_schema())

    def test_valid_empty_preemption(self):
        config = {'preemption': {}}
        jsonschema.validate(config, self._get_resources_schema())

    def test_invalid_negative_hook_timeout(self):
        config = {
            'preemption': {
                'hook': 'echo hi',
                'hook_timeout': -1,
            }
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, self._get_resources_schema())

    def test_invalid_zero_hook_timeout(self):
        config = {
            'preemption': {
                'hook': 'echo hi',
                'hook_timeout': 0,
            }
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, self._get_resources_schema())

    def test_invalid_additional_property(self):
        config = {
            'preemption': {
                'hook': 'echo hi',
                'unknown_field': 'value',
            }
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, self._get_resources_schema())

    def test_invalid_hook_type(self):
        config = {'preemption': {'hook': 123,}}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, self._get_resources_schema())

    def test_no_preemption_section_is_valid(self):
        config = {}
        jsonschema.validate(config, self._get_resources_schema())
