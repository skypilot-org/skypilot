"""Tests for plugin-registered task-overrideable config keys.

Plugins can extend the set of config keys that may be overridden via a
task YAML's ``config`` field with
``skypilot_config.register_task_overrideable_config_key``. These tests
cover the registration hook and the properties the extension relies on:
the key survives ``Resources.copy()``, is honored by
``skypilot_config.get_nested(override_configs=...)``, and unknown keys
pass through on the client for server-side validation.
"""
import pytest

from sky import clouds
from sky import skypilot_config
from sky.resources import Resources
from sky.skylet import constants
from sky.utils import config_utils
from sky.utils import schemas

_TEST_PROP = 'test_task_overrideable_prop'
_TEST_KEY = ('jobs', _TEST_PROP)


@pytest.fixture
def registered_test_key():
    """Register a throwaway jobs property as task-overrideable."""
    schemas.register_jobs_property(_TEST_PROP, {'type': 'boolean'})
    skypilot_config.register_task_overrideable_config_key(_TEST_KEY)
    yield _TEST_KEY
    constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK.remove(_TEST_KEY)
    schemas._extra_jobs_properties.pop(_TEST_PROP)  # pylint: disable=protected-access


def test_register_appends_to_overrideable_keys(registered_test_key):
    assert registered_test_key in constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK
    # Idempotent.
    skypilot_config.register_task_overrideable_config_key(registered_test_key)
    assert constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK.count(
        registered_test_key) == 1


def test_register_rejects_key_missing_from_config_schema():
    with pytest.raises(ValueError, match='not present in the config schema'):
        skypilot_config.register_task_overrideable_config_key(
            ('jobs', 'nonexistent_prop'))
    assert (('jobs', 'nonexistent_prop')
            not in constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK)


def test_registered_key_in_task_config_schema(registered_test_key):
    task_config_schema = schemas.get_task_schema()['properties']['config']
    assert _TEST_PROP in task_config_schema['properties']['jobs']['properties']


def test_registered_key_survives_resources_copy(registered_test_key):
    resources = Resources(
        cloud=clouds.Kubernetes(),
        _cluster_config_overrides={'jobs': {
            _TEST_PROP: True
        }})
    copied = resources.copy()
    assert copied.cluster_config_overrides['jobs'][_TEST_PROP] is True


def test_registered_key_honored_by_get_nested(registered_test_key):
    assert skypilot_config.get_nested(
        _TEST_KEY, False, override_configs={'jobs': {
            _TEST_PROP: True
        }}) is True
    assert skypilot_config.get_nested(_TEST_KEY, False,
                                      override_configs={}) is False


def test_copy_preserves_existing_unknown_overrides():
    """Existing overrides pass through copy() unfiltered.

    On the client the full set of overrideable keys is not known (the
    server may register more via plugins), so copy() must not silently
    drop keys that were accepted at construction time — the server is
    responsible for validating them.
    """
    resources = Resources(
        cloud=clouds.Kubernetes(),
        _cluster_config_overrides={'jobs': {
            'some_server_side_prop': True
        }})
    copied = resources.copy()
    assert copied.cluster_config_overrides['jobs']['some_server_side_prop'] is (
        True)


def test_copy_filters_unknown_incoming_overrides():
    """Incoming overrides (e.g. from --config) are still filtered."""
    resources = Resources(cloud=clouds.Kubernetes())
    copied = resources.copy(
        _cluster_config_overrides={
            'jobs': {
                'some_server_side_prop': True
            },
            'docker': {
                'run_options': ['-v /tmp:/tmp']
            },
        })
    overrides = copied.cluster_config_overrides
    assert 'jobs' not in overrides
    assert overrides['docker']['run_options'] == ['-v /tmp:/tmp']


def test_copy_null_incoming_override_clears_existing():
    """`--config <key>=null` clears an existing task-level override.

    The override must not survive the overlay — otherwise the task value
    would keep masking the CLI/global config null.
    """
    resources = Resources(
        cloud=clouds.Kubernetes(),
        _cluster_config_overrides={'gcp': {
            'vpc_name': 'task-vpc'
        }})
    copied = resources.copy(
        _cluster_config_overrides={'gcp': {
            'vpc_name': None
        }})
    missing = object()
    overrides = config_utils.Config(copied.cluster_config_overrides)
    assert overrides.get_nested(('gcp', 'vpc_name'), missing) is missing


def test_from_yaml_config_revalidates_overrides_when_strict(
        registered_test_key, monkeypatch):
    """Server-side, deserialized overrides are re-validated.

    A task's `config` field crosses the client/server boundary as
    `resources._cluster_config_overrides` (schema: bare object), so
    `Resources.from_yaml_config` must re-validate the contents against
    the task config schema — which is strict on the server.
    """
    monkeypatch.setattr(schemas, '_allow_additional_properties', lambda: False)
    # Well-formed value passes.
    resources = next(
        iter(
            Resources.from_yaml_config(
                {'_cluster_config_overrides': {
                    'jobs': {
                        _TEST_PROP: True
                    }
                }})))
    assert resources.cluster_config_overrides['jobs'][_TEST_PROP] is True
    # Malformed value for a registered boolean is rejected.
    with pytest.raises(ValueError, match='Invalid resources.config override'):
        Resources.from_yaml_config({
            '_cluster_config_overrides': {
                'jobs': {
                    _TEST_PROP: {
                        'wrong': 'shape'
                    }
                }
            }
        })
    # Unregistered keys are rejected under strict validation.
    with pytest.raises(ValueError, match='Invalid resources.config override'):
        Resources.from_yaml_config({
            '_cluster_config_overrides': {
                'jobs': {
                    'unregistered_prop': True
                }
            }
        })


def test_from_yaml_config_passes_unknown_overrides_on_client():
    """Client-side (lenient), unknown override keys still pass through so
    the server can validate them."""
    resources = next(
        iter(
            Resources.from_yaml_config({
                '_cluster_config_overrides': {
                    'jobs': {
                        'some_server_side_prop': True
                    }
                }
            })))
    assert resources.cluster_config_overrides['jobs'][
        'some_server_side_prop'] is True


def test_task_config_schema_lenient_on_client(monkeypatch):
    """Off-server, unknown task config keys pass schema validation so a
    stock client can submit them for server-side validation."""
    monkeypatch.delenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, raising=False)
    task_config_schema = schemas.get_task_schema()['properties']['config']
    assert task_config_schema['additionalProperties'] is True
