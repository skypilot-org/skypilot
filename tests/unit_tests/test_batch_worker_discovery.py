"""Worker discovery budgets for warm pools, cold starts and recovery."""
# Discovery is an internal coordinator operation.
# pylint: disable=protected-access
from unittest import mock

import jsonschema
import pytest
import yaml

from sky import skypilot_config
from sky.batch import coordinator
from sky.utils import config_utils
from sky.utils import schemas

_INITIAL = 'worker_discovery_timeout_seconds'
_RESUME = 'worker_discovery_resume_timeout_seconds'


class FakeClock:
    """Advance discovery's clock without sleeping or contacting a cloud."""

    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture(name='discovery')
def _discovery(monkeypatch):
    config = config_utils.Config()
    monkeypatch.setattr(skypilot_config, '_get_loaded_config', lambda: config)
    clock = FakeClock()
    monkeypatch.setattr(coordinator, 'time', clock)
    # Construct the real coordinator without changing pytest's SIGTERM handler.
    monkeypatch.setattr(coordinator.signal, 'signal', mock.Mock())

    def make_coordinator(is_resume=False):
        return coordinator.BatchCoordinator(dataset_path='gs://bucket/input',
                                            output_path='gs://bucket/output',
                                            batch_size=1,
                                            pool_name='test-pool',
                                            serialized_fn='',
                                            input_format_dict={},
                                            output_formats_dict=[],
                                            job_id=1,
                                            is_resume=is_resume)

    return config, clock, make_coordinator


@pytest.mark.parametrize('is_resume, budget', [(False, 300), (True, 600)])
def test_default_discovery_budgets(discovery, is_resume, budget):
    _, clock, make_coordinator = discovery
    batch = make_coordinator(is_resume)
    with mock.patch.object(batch, '_get_ready_workers', return_value=[]):
        with pytest.raises(RuntimeError, match=f'after waiting {budget}s'):
            batch._discover_workers()
    assert clock.now == budget


@pytest.mark.parametrize('is_resume', [False, True])
def test_cold_worker_can_become_ready_after_default_budget(
        discovery, is_resume):
    config, clock, make_coordinator = discovery
    # Exercise the documented YAML through schema validation and the real
    # config accessor, rather than replacing get_nested with a test stub.
    loaded = yaml.safe_load("""
batch:
  worker_discovery_timeout_seconds: 1800
  worker_discovery_resume_timeout_seconds: 1800
""")
    jsonschema.validate(loaded, schemas.get_config_schema())
    config.update(loaded)
    batch = make_coordinator(is_resume)
    with mock.patch.object(batch,
                           '_get_ready_workers',
                           side_effect=lambda: ['cold-worker']
                           if clock.now >= 900 else []):
        batch._discover_workers()
    assert batch._workers == ['cold-worker']
    assert clock.now == 900


@pytest.mark.parametrize(
    'is_resume, initial_timeout, resume_timeout, expected_timeout',
    [(False, 10, 20, 10), (True, 10, 20, 20)])
def test_configured_timeout_bounds_discovery(discovery, is_resume,
                                             initial_timeout, resume_timeout,
                                             expected_timeout):
    config, clock, make_coordinator = discovery
    # Different values ensure selecting the other timeout fails the test.
    config['batch'] = {_INITIAL: initial_timeout, _RESUME: resume_timeout}
    batch = make_coordinator(is_resume)
    with mock.patch.object(batch, '_get_ready_workers', return_value=[]):
        with pytest.raises(RuntimeError,
                           match=f'after waiting {expected_timeout}s'):
            batch._discover_workers()
    assert clock.now == expected_timeout


@pytest.mark.parametrize('is_resume, other_key, budget',
                         [(False, _RESUME, 300), (True, _INITIAL, 600)])
def test_discovery_overrides_are_independent(discovery, is_resume, other_key,
                                             budget):
    config, clock, make_coordinator = discovery
    config['batch'] = {other_key: 1800}
    batch = make_coordinator(is_resume)
    with mock.patch.object(batch, '_get_ready_workers', return_value=[]):
        with pytest.raises(RuntimeError, match=f'after waiting {budget}s'):
            batch._discover_workers()
    assert clock.now == budget


@pytest.mark.parametrize('is_resume', [False, True])
def test_warm_worker_does_not_wait(discovery, is_resume):
    config, clock, make_coordinator = discovery
    config['batch'] = {_INITIAL: 1800, _RESUME: 1800}
    batch = make_coordinator(is_resume)
    with mock.patch.object(batch,
                           '_get_ready_workers',
                           return_value=['warm-worker']):
        batch._discover_workers()
    assert batch._workers == ['warm-worker']
    assert clock.now == 0


@pytest.mark.parametrize('key', [_INITIAL, _RESUME])
@pytest.mark.parametrize('value', [0, -1, 1.5, '1800', None, True])
def test_discovery_timeout_schema_rejects_invalid_values(key, value):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({'batch': {
            key: value
        }}, schemas.get_config_schema())


@pytest.mark.parametrize('config', [{}, {
    'batch': {}
}, {
    'batch': {
        _INITIAL: 1
    }
}, {
    'batch': {
        _RESUME: 1800
    }
}])
def test_discovery_timeout_schema_accepts_optional_settings(config):
    jsonschema.validate(config, schemas.get_config_schema())


def test_discovery_timeout_schema_rejects_unknown_settings():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({'batch': {
            'worker_discovery_timeout': 1800
        }}, schemas.get_config_schema())
