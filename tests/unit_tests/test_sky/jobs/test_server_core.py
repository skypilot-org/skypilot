"""Tests for sky.jobs.server.core."""
import types
from unittest import mock

import pytest

from sky import backends
from sky.jobs.server import core as jobs_core
from sky.skylet import constants as skylet_constants


def _forwarded_tail(tail):
    """Call ``jobs_core.tail_logs`` with ``tail`` (mocking out the controller
    restart / backend / runner) and return the ``tail`` value forwarded to
    ``tail_managed_job_logs``."""
    fake_backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
    fake_runner = mock.MagicMock()
    fake_runner.tail_managed_job_logs.return_value = 0
    with mock.patch.object(jobs_core, '_maybe_restart_controller',
                           return_value=mock.MagicMock()), \
         mock.patch.object(jobs_core.backend_utils,
                           'get_backend_from_handle',
                           return_value=fake_backend), \
         mock.patch.object(jobs_core.managed_job_runner,
                           'current',
                           return_value=fake_runner):
        jobs_core.tail_logs(name=None,
                            job_id=1,
                            follow=False,
                            controller=False,
                            refresh=False,
                            tail=tail)
    fake_runner.tail_managed_job_logs.assert_called_once()
    return fake_runner.tail_managed_job_logs.call_args.kwargs['tail']


@pytest.mark.parametrize(
    ('given', 'expected'),
    [
        (0, None),  # dashboard download button's "all lines" sentinel
        (-1, None),  # `sky jobs logs --tail -1`
        (None, None),  # no tail -> all
        (200, 200),  # positive tail forwarded unchanged
        (5000, 5000),
    ])
def test_tail_logs_normalizes_non_positive_tail(given, expected):
    """A non-positive tail (0 / -1) means "all lines" and must be normalized
    to None before reaching the backward-seek tail reader (which asserts
    tail > 0). Otherwise the dashboard download (tail=0) raises
    AssertionError and produces an empty log."""
    assert _forwarded_tail(given) == expected


def _dag_with_local_workdir():
    task = types.SimpleNamespace(file_mounts=None, workdir='/tmp/my-workdir')
    return types.SimpleNamespace(tasks=[task])


def _dag_with_bucket_sources():
    task = types.SimpleNamespace(
        file_mounts={'/remote/data': 's3://my-bucket/data'}, workdir=None)
    return types.SimpleNamespace(tasks=[task])


@pytest.fixture
def _non_durable_server(monkeypatch):
    """Simulates a rolling-update server whose local files are ephemeral."""
    monkeypatch.setenv(skylet_constants.SKYPILOT_ROLLING_UPDATE_ENABLED, 'true')
    monkeypatch.setenv(skylet_constants.SKYPILOT_API_SERVER_STORAGE_ENABLED,
                       'false')
    with mock.patch.object(jobs_core.managed_job_utils,
                           'is_consolidation_mode',
                           return_value=True):
        yield


def _mock_config(require_durable: bool):
    config = {
        ('jobs', 'bucket'): None,
        ('jobs', 'require_durable_file_mounts'): require_durable,
    }

    def _get_nested(keys, default_value, *args, **kwargs):
        del args, kwargs  # Unused.
        return config.get(tuple(keys), default_value)

    return mock.patch.object(jobs_core.skypilot_config,
                             'get_nested',
                             side_effect=_get_nested)


@pytest.mark.usefixtures('_non_durable_server')
def test_local_workdir_warns_by_default():
    with _mock_config(require_durable=False):
        with mock.patch.object(jobs_core.logger, 'warning') as mock_warning:
            jobs_core._check_file_mounts_rolling_update(
                _dag_with_local_workdir())
    assert mock_warning.call_count == 1


@pytest.mark.usefixtures('_non_durable_server')
def test_local_workdir_rejected_when_durability_required():
    with _mock_config(require_durable=True):
        with pytest.raises(ValueError, match='jobs.bucket'):
            jobs_core._check_file_mounts_rolling_update(
                _dag_with_local_workdir())


@pytest.mark.usefixtures('_non_durable_server')
def test_bucket_sources_accepted_when_durability_required():
    with _mock_config(require_durable=True):
        with mock.patch.object(jobs_core.logger, 'warning') as mock_warning:
            jobs_core._check_file_mounts_rolling_update(
                _dag_with_bucket_sources())
    assert mock_warning.call_count == 0


def test_local_workdir_accepted_without_rolling_update(monkeypatch):
    monkeypatch.delenv(skylet_constants.SKYPILOT_ROLLING_UPDATE_ENABLED,
                       raising=False)
    with _mock_config(require_durable=True):
        with mock.patch.object(jobs_core.logger, 'warning') as mock_warning:
            jobs_core._check_file_mounts_rolling_update(
                _dag_with_local_workdir())
    assert mock_warning.call_count == 0


def test_require_durable_file_mounts_is_server_authoritative():
    """A client config override must not weaken the server's setting."""
    assert (('jobs', 'require_durable_file_mounts')
            in skylet_constants.SKIPPED_CLIENT_OVERRIDE_KEYS)
