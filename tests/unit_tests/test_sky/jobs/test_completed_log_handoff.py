"""Read persisted task output when a log follower finishes waiting."""
from unittest import mock

import pytest

from sky import exceptions
from sky.jobs import state
from sky.jobs import utils
from sky.skylet import log_lib


@pytest.fixture(name='waiting_job')
def _waiting_job(monkeypatch, tmp_path):
    task_log = tmp_path / 'task.log'
    task_log.write_text(log_lib.LOG_FILE_START_STREAMING_AT +
                        '\nfirst-line\nsecond-line\nthird-line\n')
    mock_state = mock.MagicMock(wraps=state)
    mock_state.ManagedJobStatus = state.ManagedJobStatus
    mock_state.get_num_tasks.return_value = 1
    mock_state.is_batch_job.return_value = False
    mock_state.get_pool_from_job_id.return_value = None
    mock_state.get_task_name.return_value = 'task'
    mock_state.get_failure_reason.return_value = 'program exited'
    monkeypatch.setattr(utils, 'managed_job_state', mock_state)
    monkeypatch.setattr(utils.threading, 'Thread', mock.MagicMock())
    monkeypatch.setattr(utils.rich_utils, 'safe_status', mock.MagicMock())
    monkeypatch.setattr(utils, 'read_provision_status_from_log', lambda *args:
                        (0, None))
    monkeypatch.setattr(utils, '_parked_launch_reason', lambda *args: None)
    monkeypatch.setattr(utils, 'JOB_STATUS_CHECK_GAP_SECONDS', 0)
    monkeypatch.setattr(utils.global_user_state, 'get_handle_from_cluster_name',
                        lambda _: None)
    backend = mock.MagicMock()
    monkeypatch.setattr(utils.backends, 'CloudVmRayBackend', lambda: backend)
    monkeypatch.setattr(utils.logs, 'get_log_reader', lambda: None)
    return mock_state, task_log, backend


@pytest.mark.parametrize(
    'terminal',
    [state.ManagedJobStatus.SUCCEEDED, state.ManagedJobStatus.FAILED])
@pytest.mark.parametrize('task', [None, 0, 'task'])
@pytest.mark.parametrize('tail,tail_offset', [(None, None), (1, 1)])
def test_waiting_follower_reads_completed_file(waiting_job, capsys, terminal,
                                               task, tail, tail_offset):
    mock_state, task_log, backend = waiting_job
    mock_state.get_status.side_effect = [
        state.ManagedJobStatus.RUNNING, terminal, terminal
    ]
    mock_state.get_latest_task_id_status.side_effect = [
        (0, state.ManagedJobStatus.RUNNING), (0, terminal)
    ]
    mock_state.get_all_task_ids_names_statuses_logs.return_value = [
        (0, 'task', terminal, str(task_log), None)
    ]

    message, code = utils.stream_logs_by_id(5,
                                            follow=True,
                                            tail=tail,
                                            tail_offset=tail_offset,
                                            task=task)

    output = capsys.readouterr().out
    assert message == ''
    assert code == exceptions.JobExitCode.from_managed_job_status(terminal)
    assert 'second-line' in output
    assert output.count('second-line') == 1
    if tail is not None:
        assert 'first-line' not in output
        assert 'third-line' not in output
    else:
        assert 'first-line' in output
        assert 'third-line' in output
    backend.tail_logs.assert_not_called()


def test_streamed_output_is_not_replayed(waiting_job, monkeypatch, capsys):
    mock_state, task_log, backend = waiting_job
    terminal = state.ManagedJobStatus.SUCCEEDED
    mock_state.get_status.side_effect = [
        state.ManagedJobStatus.RUNNING, terminal
    ]
    mock_state.get_latest_task_id_status.return_value = (
        0, state.ManagedJobStatus.RUNNING)
    mock_state.get_all_task_ids_names_statuses_logs.return_value = [
        (0, 'task', terminal, str(task_log), None)
    ]
    handle = mock.Mock(spec=utils.backends.CloudVmRayResourceHandle)
    monkeypatch.setattr(utils.global_user_state, 'get_handle_from_cluster_name',
                        lambda _: handle)
    monkeypatch.setattr(utils.managed_job_runtime, 'is_registered',
                        lambda: False)

    def tail_logs(*_args, **_kwargs):
        print('first-line\nsecond-line\nthird-line')
        return exceptions.JobExitCode.SUCCEEDED.value

    backend.tail_logs.side_effect = tail_logs
    backend.get_job_status.return_value = {0: utils.job_lib.JobStatus.SUCCEEDED}

    message, code = utils.stream_logs_by_id(5)

    assert message == ''
    assert code == exceptions.JobExitCode.SUCCEEDED
    assert capsys.readouterr().out.count('second-line') == 1
    backend.tail_logs.assert_called_once()
    mock_state.get_all_task_ids_names_statuses_logs.assert_not_called()
