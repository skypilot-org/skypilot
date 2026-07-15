"""Tests for managed jobs server-side submission checks."""
import types
from unittest import mock

import pytest

from sky.jobs.server import core
from sky.skylet import constants as skylet_constants


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
    with mock.patch.object(core.managed_job_utils,
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

    return mock.patch.object(core.skypilot_config,
                             'get_nested',
                             side_effect=_get_nested)


@pytest.mark.usefixtures('_non_durable_server')
def test_local_workdir_warns_by_default():
    with _mock_config(require_durable=False):
        with mock.patch.object(core.logger, 'warning') as mock_warning:
            core._check_file_mounts_rolling_update(_dag_with_local_workdir())
    assert mock_warning.call_count == 1


@pytest.mark.usefixtures('_non_durable_server')
def test_local_workdir_rejected_when_durability_required():
    with _mock_config(require_durable=True):
        with pytest.raises(ValueError, match='jobs.bucket'):
            core._check_file_mounts_rolling_update(_dag_with_local_workdir())


@pytest.mark.usefixtures('_non_durable_server')
def test_bucket_sources_accepted_when_durability_required():
    with _mock_config(require_durable=True):
        with mock.patch.object(core.logger, 'warning') as mock_warning:
            core._check_file_mounts_rolling_update(_dag_with_bucket_sources())
    assert mock_warning.call_count == 0


def test_local_workdir_accepted_without_rolling_update(monkeypatch):
    monkeypatch.delenv(skylet_constants.SKYPILOT_ROLLING_UPDATE_ENABLED,
                       raising=False)
    with _mock_config(require_durable=True):
        with mock.patch.object(core.logger, 'warning') as mock_warning:
            core._check_file_mounts_rolling_update(_dag_with_local_workdir())
    assert mock_warning.call_count == 0
