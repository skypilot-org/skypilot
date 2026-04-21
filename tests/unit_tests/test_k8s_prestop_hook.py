"""Tests for K8s preStop hook rendered from the standalone termination_hook.

These tests pin the de-merged design: ``termination_hook`` lives on
``Resources`` as its own field and does NOT interact with
``AutostopConfig``. ``autostop.hook`` keeps its master behavior (skylet
idle path only — no preStop rendering).
"""
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
    """Tests for Resources.termination_hook as a standalone field."""

    def test_termination_hook_is_standalone_field(self):
        """`termination_hook` must NOT merge into `autostop_config`.

        Revert-check: if the merge is reintroduced, ``autostop_config``
        will be non-None here (constructed with ``enabled=True``) and the
        assertion fails.
        """
        r = Resources(infra='kubernetes',
                      termination_hook={
                          'command': 'save.sh',
                          'timeout': 90,
                      })
        assert r.termination_hook == {'command': 'save.sh', 'timeout': 90}
        # No side effect on autostop_config.
        assert r.autostop_config is None

    def test_termination_hook_without_timeout(self):
        r = Resources(infra='kubernetes',
                      termination_hook={'command': 'save.sh'})
        assert r.termination_hook == {'command': 'save.sh'}
        assert r.autostop_config is None

    def test_autostop_hook_does_not_populate_termination_hook(self):
        """Revert-check for the removed autostop.hook -> preStop bridge.

        On master ``autostop.hook`` is skylet-only. If the bridge (merge
        into AutostopConfig then render preStop) is reintroduced,
        ``r.termination_hook`` will be populated here and the test fails.
        """
        r = Resources(infra='kubernetes',
                      autostop={
                          'idle_minutes': 5,
                          'hook': 'legacy.sh',
                          'hook_timeout': 30,
                      })
        assert r.termination_hook is None
        # Legacy autostop.hook path is unchanged from master.
        assert r.autostop_config is not None
        assert r.autostop_config.hook == 'legacy.sh'
        assert r.autostop_config.hook_timeout == 30

    def test_both_keys_coexist_without_raising(self):
        """Mutual exclusion removed. Both keys may be set together.

        Revert-check: if an exclusion ValueError is added back, this test
        fails.
        """
        r = Resources(infra='kubernetes',
                      autostop={
                          'idle_minutes': 5,
                          'hook': 'legacy.sh',
                          'hook_timeout': 10,
                      },
                      termination_hook={
                          'command': 'new.sh',
                          'timeout': 20,
                      })
        # Independent fields: each reflects its own source.
        assert r.autostop_config is not None
        assert r.autostop_config.hook == 'legacy.sh'
        assert r.autostop_config.hook_timeout == 10
        assert r.termination_hook == {'command': 'new.sh', 'timeout': 20}

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
        assert r.termination_hook == {
            'command': 'save.sh',
            'timeout': 120,
        }
        round_tripped = r.to_yaml_config()
        assert round_tripped['termination_hook'] == {
            'command': 'save.sh',
            'timeout': 120,
        }
        # Standalone field must not leak into the autostop block.
        assert 'autostop' not in round_tripped

    def test_to_yaml_does_not_emit_termination_hook_for_autostop_only(self):
        """`autostop.hook` alone must not emit a top-level termination_hook.

        Revert-check for the removed to_yaml_config double-emit.
        """
        r = Resources(infra='kubernetes',
                      autostop={
                          'idle_minutes': 5,
                          'hook': 'legacy.sh',
                          'hook_timeout': 30,
                      })
        yc = r.to_yaml_config()
        assert 'termination_hook' not in yc
        assert yc['autostop']['hook'] == 'legacy.sh'
        assert yc['autostop']['hook_timeout'] == 30

    def test_autostop_without_hook_plus_termination_hook_coexist(self):
        """`autostop: {idle_minutes}` + `termination_hook` stay independent."""
        r = Resources(infra='kubernetes',
                      autostop={
                          'idle_minutes': 5,
                          'down': True,
                      },
                      termination_hook={
                          'command': 'cleanup.sh',
                          'timeout': 60,
                      })
        assert r.autostop_config is not None
        assert r.autostop_config.enabled is True
        assert r.autostop_config.idle_minutes == 5
        assert r.autostop_config.down is True
        # No merge: autostop_config has no hook, termination_hook is its own.
        assert r.autostop_config.hook is None
        assert r.termination_hook == {'command': 'cleanup.sh', 'timeout': 60}

    def test_copy_preserves_termination_hook(self):
        """`Resources.copy()` must carry termination_hook through."""
        r = Resources(infra='kubernetes',
                      termination_hook={
                          'command': 'save.sh',
                          'timeout': 42,
                      })
        copied = r.copy()
        assert copied.termination_hook == {
            'command': 'save.sh',
            'timeout': 42,
        }
        assert copied.autostop_config is None

    def test_copy_with_termination_hook_override(self):
        """`copy(termination_hook=...)` must replace, not merge."""
        r = Resources(infra='kubernetes',
                      termination_hook={
                          'command': 'save.sh',
                          'timeout': 10,
                      })
        copied = r.copy(termination_hook={
            'command': 'different.sh',
            'timeout': 99,
        })
        assert copied.termination_hook == {
            'command': 'different.sh',
            'timeout': 99,
        }
