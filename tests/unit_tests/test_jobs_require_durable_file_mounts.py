"""Tests for ``jobs.require_durable_file_mounts``.

The check runs at managed-job submission time and only fires when a rolling
update could drop the job's local file mounts or workdir: rolling update
enabled, no persistent API server storage, consolidation mode, and no
``jobs.bucket``.
"""
from unittest import mock

import pytest

from sky import exceptions
from sky.jobs.server import core as jobs_core
from sky.skylet import constants as skylet_constants


def _dag(*, file_mounts=None, workdir=None):
    task = mock.MagicMock()
    task.file_mounts = file_mounts
    task.workdir = workdir
    dag = mock.MagicMock()
    dag.tasks = [task]
    return dag


def _config(require_durable_file_mounts):

    def get_nested(keys, default=None, *args, **kwargs):
        del args, kwargs  # Unused.
        if keys == ('jobs', 'require_durable_file_mounts'):
            return require_durable_file_mounts
        return default

    return get_nested


@pytest.fixture
def rolling_update_without_storage(monkeypatch):
    """Rolling update on, persistent storage off, consolidation mode on."""
    monkeypatch.setenv(skylet_constants.SKYPILOT_ROLLING_UPDATE_ENABLED, '1')
    monkeypatch.setenv(skylet_constants.SKYPILOT_API_SERVER_STORAGE_ENABLED,
                       'false')
    with mock.patch.object(jobs_core.managed_job_utils,
                           'is_consolidation_mode',
                           return_value=True):
        yield


@pytest.mark.parametrize('dag', [
    _dag(workdir='/tmp/workdir'),
    _dag(file_mounts={'/remote': '~/local'}),
])
def test_warns_by_default(rolling_update_without_storage, dag):
    with mock.patch.object(jobs_core.skypilot_config,
                           'get_nested',
                           side_effect=_config(False)):
        with mock.patch.object(jobs_core, 'logger') as mock_logger:
            jobs_core._check_file_mounts_rolling_update(dag)
    mock_logger.warning.assert_called_once()
    assert 'Local file mounts or workdir detected' in (
        mock_logger.warning.call_args[0][0])


@pytest.mark.parametrize('dag', [
    _dag(workdir='/tmp/workdir'),
    _dag(file_mounts={'/remote': '~/local'}),
])
def test_rejects_when_durable_file_mounts_required(
        rolling_update_without_storage, dag):
    with mock.patch.object(jobs_core.skypilot_config,
                           'get_nested',
                           side_effect=_config(True)):
        with pytest.raises(exceptions.NotSupportedError,
                           match='require_durable_file_mounts'):
            jobs_core._check_file_mounts_rolling_update(dag)


def test_cloud_file_mounts_are_never_rejected(rolling_update_without_storage):
    dag = _dag(file_mounts={'/remote': 's3://my-bucket/data'})
    with mock.patch.object(jobs_core.skypilot_config,
                           'get_nested',
                           side_effect=_config(True)):
        with mock.patch.object(jobs_core, 'logger') as mock_logger:
            jobs_core._check_file_mounts_rolling_update(dag)
    mock_logger.warning.assert_not_called()


def test_no_rolling_update_is_never_rejected(monkeypatch):
    monkeypatch.delenv(skylet_constants.SKYPILOT_ROLLING_UPDATE_ENABLED,
                       raising=False)
    dag = _dag(workdir='/tmp/workdir')
    with mock.patch.object(jobs_core.skypilot_config,
                           'get_nested',
                           side_effect=_config(True)):
        with mock.patch.object(jobs_core, 'logger') as mock_logger:
            jobs_core._check_file_mounts_rolling_update(dag)
    mock_logger.warning.assert_not_called()
