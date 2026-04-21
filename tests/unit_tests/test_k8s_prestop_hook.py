"""Tests for K8s preStop hook rendered from the standalone termination_hook.

These tests pin two invariants:

1. ``termination_hook`` is a standalone ``Resources`` field; the
   Kubernetes template renders it into a ``preStop`` lifecycle hook.
2. The legacy ``autostop.hook`` / ``autostop.hook_timeout`` YAML keys
   are deprecated but still accepted — they route into
   ``Resources.termination_hook`` with a warning. ``AutostopConfig``
   itself no longer carries the hook.
"""
import pickle

import jsonschema
import pytest

from sky.resources import AutostopConfig
from sky.resources import Resources
from sky.utils import schemas


class TestAutostopConfigShape:
    """AutostopConfig no longer carries hook / hook_timeout fields."""

    def test_no_hook_attributes_on_dataclass(self):
        """Revert-check: reintroducing the fields fails this test."""
        cfg = AutostopConfig(enabled=True)
        assert not hasattr(cfg, 'hook')
        assert not hasattr(cfg, 'hook_timeout')

    def test_from_yaml_config_ignores_legacy_hook_keys(self):
        """from_yaml_config silently drops legacy keys.

        The hook fields are instead routed out via
        Resources._set_autostop_config. AutostopConfig does not see them.
        """
        cfg = AutostopConfig.from_yaml_config({
            'idle_minutes': 10,
            'hook': 'echo "saving"',
            'hook_timeout': 60,
        })
        assert cfg is not None
        assert cfg.idle_minutes == 10
        assert not hasattr(cfg, 'hook')
        assert not hasattr(cfg, 'hook_timeout')

    def test_to_yaml_config_does_not_emit_hook(self):
        """Round-trip: no hook / hook_timeout in emitted YAML."""
        cfg = AutostopConfig.from_yaml_config({'idle_minutes': 5})
        assert cfg is not None
        yaml_config = cfg.to_yaml_config()
        assert 'hook' not in yaml_config
        assert 'hook_timeout' not in yaml_config


class TestAutostopHookSchemaBackCompat:
    """Legacy autostop.hook / hook_timeout keys still validate.

    We keep them in the schema for YAML back-compat. Resources emits a
    deprecation warning at construction time.
    """

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

    def test_preemption_key_rejected(self):
        """The old preemption key should still be rejected."""
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
        r = Resources(infra='kubernetes',
                      termination_hook={
                          'command': 'save.sh',
                          'timeout': 90,
                      })
        assert r.termination_hook == {'command': 'save.sh', 'timeout': 90}
        # autostop_config carries no hook-related state.
        assert r.autostop_config is None

    def test_termination_hook_without_timeout(self):
        r = Resources(infra='kubernetes',
                      termination_hook={'command': 'save.sh'})
        assert r.termination_hook == {'command': 'save.sh'}
        assert r.autostop_config is None

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

    def test_copy_preserves_termination_hook(self):
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


class TestAutostopHookDeprecation:
    """Legacy autostop.hook input is routed into termination_hook.

    Revert-check for the migration PR: if the routing logic in
    ``_set_autostop_config`` is reverted, these tests fail.
    """

    @staticmethod
    def _capture_warnings(records):
        import logging

        class _Handler(logging.Handler):

            def emit(self, record):  # pragma: no cover - trivial
                records.append(record)

        return _Handler(level=logging.WARNING)

    def test_autostop_hook_routed_to_termination_hook(self):
        import logging

        # sky.* loggers have propagate=False, so attach a handler
        # directly to sky.resources to capture the deprecation warning.
        records: list = []
        handler = self._capture_warnings(records)
        sky_res_logger = logging.getLogger('sky.resources')
        sky_res_logger.addHandler(handler)
        try:
            r = Resources(infra='kubernetes',
                          autostop={
                              'idle_minutes': 5,
                              'hook': 'legacy.sh',
                              'hook_timeout': 30,
                          })
        finally:
            sky_res_logger.removeHandler(handler)
        # Hook lands on Resources.termination_hook, not on
        # AutostopConfig (which no longer has those fields).
        assert r.termination_hook == {
            'command': 'legacy.sh',
            'timeout': 30,
        }
        assert r.autostop_config is not None
        assert r.autostop_config.idle_minutes == 5
        assert not hasattr(r.autostop_config, 'hook')
        # Deprecation warning was emitted.
        assert any('autostop.hook is deprecated' in record.getMessage()
                   for record in records)

    def test_autostop_hook_only_still_routes(self):
        r = Resources(infra='kubernetes', autostop={'hook': 'alone.sh'})
        assert r.termination_hook == {'command': 'alone.sh'}

    def test_explicit_termination_hook_wins_over_legacy(self):
        """If both are present, explicit termination_hook takes precedence."""
        r = Resources(infra='kubernetes',
                      autostop={
                          'idle_minutes': 5,
                          'hook': 'legacy.sh',
                      },
                      termination_hook={'command': 'new.sh'})
        assert r.termination_hook == {'command': 'new.sh'}

    def test_to_yaml_emits_termination_hook_not_autostop_hook(self):
        """Round-trip of legacy YAML produces the new form."""
        r = Resources(infra='kubernetes',
                      autostop={
                          'idle_minutes': 5,
                          'hook': 'legacy.sh',
                          'hook_timeout': 30,
                      })
        yc = r.to_yaml_config()
        # New form: termination_hook.
        assert yc['termination_hook'] == {
            'command': 'legacy.sh',
            'timeout': 30,
        }
        # Old form: no hook keys under autostop.
        assert 'hook' not in yc.get('autostop', {})
        assert 'hook_timeout' not in yc.get('autostop', {})

    def test_plain_autostop_without_hook_unchanged(self):
        """autostop timing fields without hook should not trigger warning."""
        r = Resources(infra='kubernetes',
                      autostop={
                          'idle_minutes': 5,
                          'down': True,
                      })
        assert r.termination_hook is None
        assert r.autostop_config.idle_minutes == 5
        assert r.autostop_config.down is True


class TestResourcesPickleMigration:
    """v33 -> v34 migration: move legacy AutostopConfig.hook into
    Resources._termination_hook on unpickle."""

    def test_v33_pickle_migrates_hook_to_termination_hook(self):
        """Construct a v33-shaped state, unpickle, assert migration.

        Revert-check: if the version<34 branch in Resources.__setstate__
        is removed, the termination_hook ends up None and this fails.
        """
        # Build a minimal Resources, then hand-craft its pickled state to
        # mimic a v33 on-disk payload (legacy .hook/.hook_timeout on the
        # AutostopConfig, no _termination_hook attribute).
        r = Resources(infra='kubernetes')
        state = r.__getstate__() if hasattr(r, '__getstate__') else dict(
            r.__dict__)
        # Downgrade the state to look like v33.
        legacy_autostop = AutostopConfig(enabled=True, idle_minutes=10)
        # Inject legacy attributes on the AutostopConfig instance.
        legacy_autostop.hook = 'legacy-from-pickle.sh'
        legacy_autostop.hook_timeout = 42
        state['_autostop_config'] = legacy_autostop
        state.pop('_termination_hook', None)
        state['_version'] = 33

        restored = Resources.__new__(Resources)
        restored.__setstate__(state)
        assert restored.termination_hook == {
            'command': 'legacy-from-pickle.sh',
            'timeout': 42,
        }
        # AutostopConfig no longer carries the legacy fields.
        assert not hasattr(restored.autostop_config, 'hook')
        assert not hasattr(restored.autostop_config, 'hook_timeout')


class TestSkyletAutostopConfigPickle:
    """Skylet-side AutostopConfig unpickles old pickles with `hook`."""

    def test_old_skylet_pickle_translates_hook_attrs(self):
        """Revert-check: removing the __setstate__ translation fails."""
        from sky.skylet import autostop_lib

        # Manually assemble the dict an old pickle would produce.
        old_state = {
            'autostop_idle_minutes': 10,
            'boot_time': 1.0,
            'backend': 'test',
            'wait_for': autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
            'down': False,
            'hook': 'old-skylet-hook.sh',
            'hook_timeout': 99,
        }
        cfg = autostop_lib.AutostopConfig.__new__(autostop_lib.AutostopConfig)
        cfg.__setstate__(old_state)

        # New attribute names populated.
        assert cfg.termination_hook == 'old-skylet-hook.sh'
        assert cfg.termination_hook_timeout == 99
        # Old names scrubbed.
        assert not hasattr(cfg, 'hook')
        assert not hasattr(cfg, 'hook_timeout')

    def test_new_skylet_pickle_round_trip(self):
        """Pickle/unpickle a fresh AutostopConfig through pickle module."""
        from sky.skylet import autostop_lib

        cfg = autostop_lib.AutostopConfig(
            autostop_idle_minutes=5,
            boot_time=1.0,
            backend='test',
            wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
            down=False,
            termination_hook='fresh.sh',
            termination_hook_timeout=60,
        )
        blob = pickle.dumps(cfg)
        restored = pickle.loads(blob)
        assert restored.termination_hook == 'fresh.sh'
        assert restored.termination_hook_timeout == 60
