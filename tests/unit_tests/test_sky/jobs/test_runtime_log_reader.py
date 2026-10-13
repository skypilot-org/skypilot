"""Managed-job log dispatch before status and cluster-handle branching."""
import types
from unittest import mock

import pytest

from sky import exceptions
from sky.jobs import runtime
from sky.jobs import state
from sky.jobs import utils
from sky.skylet import log_lib


@pytest.fixture(name='job_state')
def _job_state(monkeypatch):
    monkeypatch.setattr(runtime, '_runtimes', [])
    mock_state = mock.MagicMock(wraps=state)
    mock_state.ManagedJobStatus = state.ManagedJobStatus
    mock_state.get_num_tasks.return_value = 2
    mock_state.get_status.return_value = state.ManagedJobStatus.RUNNING
    mock_state.get_all_task_ids_names_statuses_logs.return_value = [
        (0, 'train', state.ManagedJobStatus.RUNNING, None, None),
        (1, 'evaluate', state.ManagedJobStatus.RUNNING, None, None),
    ]
    monkeypatch.setattr(utils, 'managed_job_state', mock_state)
    monkeypatch.setattr(log_lib, 'start_orphan_watchdog', mock.MagicMock())
    monkeypatch.setattr(utils.rich_utils, 'safe_status', mock.MagicMock())
    return mock_state


@pytest.mark.parametrize('task,task_id', [(None, None), (1, 1),
                                          ('evaluate', 1)])
@pytest.mark.parametrize('exit_code', [0, 100])
def test_runtime_reader_precedes_status_and_handle_lookup(
        job_state, monkeypatch, task, task_id, exit_code):
    reader = mock.Mock(return_value=exit_code)
    runtime.register(types.SimpleNamespace(tail_managed_job_logs=reader))
    handle_lookup = mock.Mock(
        side_effect=AssertionError('must not need handle'))
    monkeypatch.setattr(utils.global_user_state, 'get_handle_from_cluster_name',
                        handle_lookup)

    assert utils.stream_logs_by_id(9,
                                   follow=True,
                                   tail=7,
                                   tail_offset=3,
                                   task=task) == ('', exit_code)

    reader.assert_called_once_with(job_id=9,
                                   task_id=task_id,
                                   follow=True,
                                   tail=7,
                                   tail_offset=3)
    job_state.get_status.assert_not_called()
    handle_lookup.assert_not_called()


@pytest.mark.parametrize('missing_job,task', [(True, None), (False, 3),
                                              (False, 'unknown')])
def test_validate_before_runtime_reader(job_state, missing_job, task):
    reader = mock.Mock(return_value=0)
    runtime.register(types.SimpleNamespace(tail_managed_job_logs=reader))
    if missing_job:
        job_state.get_num_tasks.return_value = 0

    _, code = utils.stream_logs_by_id(9, task=task)

    assert code == exceptions.JobExitCode.NOT_FOUND
    reader.assert_not_called()


def test_dispatch_uses_first_non_none_result(job_state):
    first = mock.Mock(return_value=None)
    second = mock.Mock(return_value=0)
    third = mock.Mock(return_value=100)
    runtime.register(types.SimpleNamespace())
    for reader in [first, second, third]:
        runtime.register(types.SimpleNamespace(tail_managed_job_logs=reader))

    assert utils.stream_logs_by_id(9, follow=False) == ('', 0)

    first.assert_called_once()
    second.assert_called_once_with(job_id=9,
                                   task_id=None,
                                   follow=False,
                                   tail=None,
                                   tail_offset=None)
    third.assert_not_called()


def test_none_result_uses_standard_terminal_reader(job_state, monkeypatch,
                                                   tmp_path, capsys):
    task_log = tmp_path / 'task.log'
    task_log.write_text(log_lib.LOG_FILE_START_STREAMING_AT +
                        '\nfirst-line\nsecond-line\nthird-line\n')
    reader = mock.Mock(return_value=None)
    runtime.register(types.SimpleNamespace(tail_managed_job_logs=reader))
    job_state.get_num_tasks.return_value = 1
    job_state.get_status.return_value = state.ManagedJobStatus.SUCCEEDED
    job_state.get_all_task_ids_names_statuses_logs.return_value = [
        (0, 'train', state.ManagedJobStatus.SUCCEEDED, str(task_log), None)
    ]
    monkeypatch.setattr(utils.logs, 'get_log_reader', lambda: None)

    _, code = utils.stream_logs_by_id(9, follow=False, tail=1, tail_offset=1)

    assert code == exceptions.JobExitCode.SUCCEEDED
    output = capsys.readouterr().out
    assert 'second-line' in output
    assert 'first-line' not in output
    assert 'third-line' not in output
    reader.assert_called_once()
