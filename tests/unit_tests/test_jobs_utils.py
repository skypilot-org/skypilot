import os
import tempfile
from unittest import mock

from sky import exceptions
from sky import resources as sky_resources
from sky.backends import cloud_vm_ray_backend
from sky.exceptions import ClusterDoesNotExist
from sky.jobs import state as jobs_state
from sky.jobs import utils
from sky.skylet import job_lib
from sky.skylet import log_lib


def _setup_status_context(mock_safe_status: mock.MagicMock) -> mock.MagicMock:
    context = mock.MagicMock()
    context.__enter__.return_value = context
    context.__exit__.return_value = False
    mock_safe_status.return_value = context
    return context


@mock.patch('sky.core.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_retry_on_value_error(mock_set_internal,
                                                mock_sky_down) -> None:
    # Set up mock to fail twice with ValueError, then succeed
    mock_sky_down.side_effect = [
        ValueError('Mock error 1'),
        ValueError('Mock error 2'),
        None,
    ]

    # Call should succeed after retries
    utils.terminate_cluster('test-cluster')

    # Verify sky.down was called 3 times
    assert mock_sky_down.call_count == 3
    mock_sky_down.assert_has_calls([
        mock.call('test-cluster'),
        mock.call('test-cluster'),
        mock.call('test-cluster'),
    ])

    # Verify usage.set_internal was called before each sky.down
    assert mock_set_internal.call_count == 3


@mock.patch('sky.core.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_handles_nonexistent_cluster(mock_set_internal,
                                                       mock_sky_down) -> None:
    # Set up mock to raise ClusterDoesNotExist
    mock_sky_down.side_effect = ClusterDoesNotExist('test-cluster')

    # Call should succeed silently
    utils.terminate_cluster('test-cluster')

    # Verify sky.down was called once
    assert mock_sky_down.call_count == 1
    mock_sky_down.assert_called_once_with('test-cluster')

    # Verify usage.set_internal was called once
    assert mock_set_internal.call_count == 1


def test_stream_logs_by_id_passes_tail_parameter() -> None:
    job_id = 123
    handle = cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name='cluster-123',
        cluster_name_on_cloud='cluster-123',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=sky_resources.Resources(),
    )

    with mock.patch('sky.jobs.utils.time.sleep', new=lambda *_, **__: None), \
            mock.patch('sky.jobs.utils.rich_utils.safe_status') as mock_safe_status, \
            mock.patch('sky.jobs.utils.backends.CloudVmRayBackend') as mock_backend_cls, \
            mock.patch('sky.jobs.utils.managed_job_state.get_num_tasks',
                       return_value=1), \
            mock.patch('sky.jobs.utils.managed_job_state.get_status',
                       side_effect=[jobs_state.ManagedJobStatus.RUNNING,
                                    jobs_state.ManagedJobStatus.SUCCEEDED]), \
            mock.patch('sky.jobs.utils.managed_job_state.get_latest_task_id_status',
                       return_value=(0, jobs_state.ManagedJobStatus.RUNNING)), \
            mock.patch('sky.jobs.utils.managed_job_state.get_task_name',
                       return_value='task-0'), \
            mock.patch('sky.jobs.utils.managed_job_state.get_pool_from_job_id',
                       return_value=None), \
            mock.patch('sky.jobs.utils.global_user_state.get_handle_from_cluster_name',
                       return_value=handle):
        status_display = _setup_status_context(mock_safe_status)
        status_display.stop = mock.MagicMock()

        backend = mock_backend_cls.return_value
        backend.tail_logs.return_value = exceptions.JobExitCode.SUCCEEDED.value
        backend.get_job_status.return_value = {
            'task': job_lib.JobStatus.SUCCEEDED
        }

        msg, exit_code = utils.stream_logs_by_id(job_id, follow=True, tail=10)

        assert msg == ''
        assert exit_code == exceptions.JobExitCode.SUCCEEDED

        assert backend.tail_logs.call_count >= 1
        call_kwargs = backend.tail_logs.call_args.kwargs
        assert call_kwargs['managed_job_id'] == job_id
        assert call_kwargs['tail'] == 10


def test_stream_logs_by_id_uses_pool_handle() -> None:
    job_id = 42
    pool_cluster = 'pool-cluster'
    pool_job_id = 9001
    handle = cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name=pool_cluster,
        cluster_name_on_cloud=pool_cluster,
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=sky_resources.Resources(),
    )

    with mock.patch('sky.jobs.utils.time.sleep', new=lambda *_, **__: None), \
            mock.patch('sky.jobs.utils.rich_utils.safe_status') as mock_safe_status, \
            mock.patch('sky.jobs.utils.backends.CloudVmRayBackend') as mock_backend_cls, \
            mock.patch('sky.jobs.utils.managed_job_state.get_num_tasks',
                       return_value=1), \
            mock.patch('sky.jobs.utils.managed_job_state.get_status',
                       side_effect=[jobs_state.ManagedJobStatus.RUNNING,
                                    jobs_state.ManagedJobStatus.SUCCEEDED]), \
            mock.patch('sky.jobs.utils.managed_job_state.get_latest_task_id_status',
                       return_value=(0, jobs_state.ManagedJobStatus.RUNNING)), \
            mock.patch('sky.jobs.utils.managed_job_state.get_task_name',
                       return_value='pool-task'), \
            mock.patch('sky.jobs.utils.managed_job_state.get_pool_from_job_id',
                       return_value='my-pool'), \
            mock.patch('sky.jobs.utils.managed_job_state.get_pool_submit_info',
                       return_value=(pool_cluster, pool_job_id)) as mock_submit_info, \
            mock.patch('sky.jobs.utils.global_user_state.get_handle_from_cluster_name',
                       return_value=handle) as mock_get_handle:
        _setup_status_context(mock_safe_status)

        backend = mock_backend_cls.return_value
        backend.tail_logs.return_value = exceptions.JobExitCode.SUCCEEDED.value
        backend.get_job_status.return_value = {
            'task': job_lib.JobStatus.SUCCEEDED
        }

        _, exit_code = utils.stream_logs_by_id(job_id, follow=True)

        assert exit_code == exceptions.JobExitCode.SUCCEEDED
        mock_submit_info.assert_called_once_with(job_id)
        mock_get_handle.assert_called_once_with(pool_cluster)
        assert backend.tail_logs.call_count >= 1
        assert backend.tail_logs.call_args.kwargs['job_id'] == pool_job_id


def test_stream_logs_by_id_no_follow_returns_success_while_running() -> None:
    job_id = 7
    handle = cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name='cluster-7',
        cluster_name_on_cloud='cluster-7',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=sky_resources.Resources(),
    )

    with mock.patch('sky.jobs.utils.time.sleep', new=lambda *_, **__: None), \
            mock.patch('sky.jobs.utils.rich_utils.safe_status') as mock_safe_status, \
            mock.patch('sky.jobs.utils.backends.CloudVmRayBackend') as mock_backend_cls, \
            mock.patch('sky.jobs.utils.managed_job_state.get_num_tasks',
                       return_value=2), \
            mock.patch('sky.jobs.utils.managed_job_state.get_status',
                       side_effect=[jobs_state.ManagedJobStatus.RUNNING,
                                    jobs_state.ManagedJobStatus.RUNNING]), \
            mock.patch('sky.jobs.utils.managed_job_state.get_latest_task_id_status',
                       return_value=(1, jobs_state.ManagedJobStatus.RUNNING)), \
            mock.patch('sky.jobs.utils.managed_job_state.get_task_name',
                       return_value='task-1'), \
            mock.patch('sky.jobs.utils.managed_job_state.get_pool_from_job_id',
                       return_value=None), \
            mock.patch('sky.jobs.utils.global_user_state.get_handle_from_cluster_name',
                       return_value=handle):
        _setup_status_context(mock_safe_status)

        backend = mock_backend_cls.return_value
        backend.tail_logs.return_value = exceptions.JobExitCode.SUCCEEDED.value
        backend.get_job_status.return_value = {
            'task': job_lib.JobStatus.SUCCEEDED
        }

        msg, exit_code = utils.stream_logs_by_id(job_id, follow=False)

        assert msg == ''
        assert exit_code == exceptions.JobExitCode.SUCCEEDED
        assert backend.tail_logs.call_count >= 1
        assert backend.tail_logs.call_args.kwargs['follow'] is False


def test_stream_logs_by_id_returns_failure_message_for_terminal_job() -> None:
    job_id = 88
    failure_reason = 'boom'

    with mock.patch('sky.jobs.utils.time.sleep', new=lambda *_, **__: None), \
            mock.patch('sky.jobs.utils.rich_utils.safe_status') as mock_safe_status, \
            mock.patch('sky.jobs.utils.managed_job_state.get_num_tasks',
                       return_value=0), \
            mock.patch('sky.jobs.utils.managed_job_state.get_status',
                       return_value=jobs_state.ManagedJobStatus.FAILED), \
            mock.patch('sky.jobs.utils.managed_job_state.get_all_task_ids_names_statuses_logs',
                       return_value=[]), \
            mock.patch('sky.jobs.utils.managed_job_state.get_failure_reason',
                       return_value=failure_reason):
        _setup_status_context(mock_safe_status)

        msg, exit_code = utils.stream_logs_by_id(job_id, follow=True)

        assert 'Failure reason' in msg
        assert failure_reason in msg
        assert exit_code == exceptions.JobExitCode.FAILED


def test_stream_logs_by_id_handles_tail_failure_and_returns_job_exit_code(
) -> None:
    job_id = 256
    handle = cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name='cluster-256',
        cluster_name_on_cloud='cluster-256',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=sky_resources.Resources(),
    )

    status_sequence = [
        jobs_state.ManagedJobStatus.RUNNING,  # initial while loop
        jobs_state.ManagedJobStatus.RUNNING,  # post tail_logs failure check
        jobs_state.ManagedJobStatus.FAILED,  # final status to break
        jobs_state.ManagedJobStatus.FAILED,  # subsequent reads in final loop
        jobs_state.ManagedJobStatus.FAILED,
    ]

    with mock.patch('sky.jobs.utils.time.sleep', new=lambda *_, **__: None), \
            mock.patch('sky.jobs.utils.rich_utils.safe_status') as mock_safe_status, \
            mock.patch('sky.jobs.utils.backends.CloudVmRayBackend') as mock_backend_cls, \
            mock.patch('sky.jobs.utils.managed_job_state.get_num_tasks',
                       return_value=1), \
            mock.patch('sky.jobs.utils.managed_job_state.get_status',
                       side_effect=status_sequence), \
            mock.patch('sky.jobs.utils.managed_job_state.get_latest_task_id_status',
                       return_value=(0, jobs_state.ManagedJobStatus.RUNNING)), \
            mock.patch('sky.jobs.utils.managed_job_state.get_task_name',
                       return_value='task-0'), \
            mock.patch('sky.jobs.utils.managed_job_state.get_pool_from_job_id',
                       return_value=None), \
            mock.patch('sky.jobs.utils.global_user_state.get_handle_from_cluster_name',
                       return_value=handle):
        _setup_status_context(mock_safe_status)

        backend = mock_backend_cls.return_value
        backend.tail_logs.return_value = 1  # not a JobExitCode
        backend.get_job_status.return_value = {'task': job_lib.JobStatus.FAILED}

        msg, exit_code = utils.stream_logs_by_id(job_id, follow=True)

        assert msg == ''
        assert exit_code == exceptions.JobExitCode.FAILED
        assert backend.tail_logs.call_count >= 1


def test_stream_logs_by_id_waits_until_job_status_available() -> None:
    job_id = 512

    sleep_mock = mock.MagicMock()

    with mock.patch('sky.jobs.utils.time.sleep', new=sleep_mock), \
            mock.patch('sky.jobs.utils.rich_utils.safe_status') as mock_safe_status, \
            mock.patch('sky.jobs.utils.managed_job_state.get_num_tasks',
                       return_value=0), \
            mock.patch('sky.jobs.utils.managed_job_state.get_status',
                       side_effect=[None, None, jobs_state.ManagedJobStatus.SUCCEEDED]), \
            mock.patch('sky.jobs.utils.managed_job_state.get_all_task_ids_names_statuses_logs',
                       return_value=[]), \
            mock.patch('sky.jobs.utils.managed_job_state.get_failure_reason',
                       return_value=None), \
            mock.patch('sky.jobs.utils.backends.CloudVmRayBackend') as mock_backend_cls:
        status_display = _setup_status_context(mock_safe_status)

        msg, exit_code = utils.stream_logs_by_id(job_id, follow=True)

        assert 'terminal state' in msg
        assert exit_code == exceptions.JobExitCode.SUCCEEDED
        assert sleep_mock.call_count == 2
        mock_backend_cls.assert_not_called()
        assert status_display.update.call_count == 0


def test_stream_logs_by_id_handles_task_retry_spinner() -> None:
    job_id = 999
    handle = cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name='cluster-999',
        cluster_name_on_cloud='cluster-999',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=sky_resources.Resources(),
    )

    status_sequence = [
        jobs_state.ManagedJobStatus.RUNNING,  # initial status for loop
        jobs_state.ManagedJobStatus.
        RUNNING,  # inside is_managed_job_status_updated
        jobs_state.ManagedJobStatus.FAILED,  # exit wait
        jobs_state.ManagedJobStatus.FAILED,
        jobs_state.ManagedJobStatus.FAILED,
        jobs_state.ManagedJobStatus.FAILED,
    ]

    with mock.patch('sky.jobs.utils.time.sleep', new=lambda *_, **__: None), \
            mock.patch('sky.jobs.utils.rich_utils.safe_status') as mock_safe_status, \
            mock.patch('sky.jobs.utils.backends.CloudVmRayBackend') as mock_backend_cls, \
            mock.patch('sky.jobs.utils.managed_job_state.get_num_tasks',
                       return_value=1), \
            mock.patch('sky.jobs.utils.managed_job_state.get_status',
                       side_effect=status_sequence), \
            mock.patch('sky.jobs.utils.managed_job_state.get_latest_task_id_status',
                       return_value=(0, jobs_state.ManagedJobStatus.RUNNING)), \
            mock.patch('sky.jobs.utils.managed_job_state.get_task_name',
                       return_value='task-0'), \
            mock.patch('sky.jobs.utils.managed_job_state.get_task_specs',
                       return_value={'max_restarts_on_errors': 1}), \
            mock.patch('sky.jobs.utils.managed_job_state.get_pool_from_job_id',
                       return_value=None), \
            mock.patch('sky.jobs.utils.global_user_state.get_handle_from_cluster_name',
                       return_value=handle):
        status_display = _setup_status_context(mock_safe_status)

        backend = mock_backend_cls.return_value
        backend.tail_logs.return_value = exceptions.JobExitCode.FAILED.value
        backend.get_job_status.return_value = {'task': job_lib.JobStatus.FAILED}

        msg, exit_code = utils.stream_logs_by_id(job_id, follow=True)

        assert msg == ''
        assert exit_code == exceptions.JobExitCode.FAILED
        status_display.update.assert_any_call(mock.ANY)
        assert any('Waiting for next restart' in str(call.args[0])
                   for call in status_display.update.mock_calls)
        assert status_display.start.called
        assert backend.tail_logs.call_count >= 1


def test_stream_logs_by_id_handles_multiple_tasks_sequentially() -> None:
    job_id = 321
    handle_task0 = cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name='cluster-321-0',
        cluster_name_on_cloud='cluster-321-0',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=sky_resources.Resources(),
    )
    handle_task1 = cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name='cluster-321-1',
        cluster_name_on_cloud='cluster-321-1',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=sky_resources.Resources(),
    )

    status_calls = [
        jobs_state.ManagedJobStatus.RUNNING,
        jobs_state.ManagedJobStatus.RUNNING,
        jobs_state.ManagedJobStatus.RUNNING,
        jobs_state.ManagedJobStatus.RUNNING,
        jobs_state.ManagedJobStatus.RUNNING,
        jobs_state.ManagedJobStatus.SUCCEEDED,
        jobs_state.ManagedJobStatus.SUCCEEDED,
    ]

    sleep_mock = mock.MagicMock(side_effect=lambda *_, **__: None)
    with mock.patch('sky.jobs.utils.time.sleep', new=sleep_mock), \
            mock.patch('sky.jobs.utils.rich_utils.safe_status') as mock_safe_status, \
            mock.patch('sky.jobs.utils.backends.CloudVmRayBackend') as mock_backend_cls, \
            mock.patch('sky.jobs.utils.managed_job_state.get_num_tasks',
                       return_value=2), \
            mock.patch('sky.jobs.utils.managed_job_state.get_status',
                       side_effect=status_calls), \
            mock.patch('sky.jobs.utils.managed_job_state.get_latest_task_id_status',
                       side_effect=[
                           (0, jobs_state.ManagedJobStatus.RUNNING),
                           (0, jobs_state.ManagedJobStatus.RUNNING),
                           (0, jobs_state.ManagedJobStatus.RUNNING),
                           (1, jobs_state.ManagedJobStatus.RUNNING),
                           (1, jobs_state.ManagedJobStatus.RUNNING),
                           (1, jobs_state.ManagedJobStatus.RUNNING),
                           (1, jobs_state.ManagedJobStatus.RUNNING),
                       ]), \
            mock.patch('sky.jobs.utils.managed_job_state.get_task_name',
                       side_effect=['task-0', 'task-0', 'task-0', 'task-1', 'task-1', 'task-1', 'task-1']), \
            mock.patch('sky.jobs.utils.managed_job_state.get_pool_from_job_id',
                       return_value=None), \
            mock.patch('sky.jobs.utils.global_user_state.get_handle_from_cluster_name',
                       side_effect=[handle_task0, None, handle_task0, handle_task1, handle_task1, handle_task1, handle_task1]) as mock_get_handle:
        status_display = _setup_status_context(mock_safe_status)

        backend = mock_backend_cls.return_value
        backend.tail_logs.side_effect = [
            exceptions.JobExitCode.SUCCEEDED.value,
            exceptions.JobExitCode.SUCCEEDED.value,
        ]
        backend.get_job_status.return_value = {
            'task': job_lib.JobStatus.SUCCEEDED
        }

        msg, exit_code = utils.stream_logs_by_id(job_id, follow=True)

        assert msg == ''
        assert exit_code == exceptions.JobExitCode.SUCCEEDED
        assert mock_get_handle.call_count >= 3  # called for both tasks
        assert backend.tail_logs.call_count == 2
        assert sleep_mock.call_count >= 2
        # After first task completion we expect spinner update for next task
        assert any('Waiting for the next task' in str(call.args[0])
                   for call in status_display.update.mock_calls)


def test_stream_logs_by_id_prints_existing_logs_when_terminal() -> None:
    job_id = 555
    with tempfile.NamedTemporaryFile('w+', delete=False) as tmp:
        tmp.write(f'{log_lib.LOG_FILE_START_STREAMING_AT}\nhello world\n')
        log_path = tmp.name

    try:
        with mock.patch('sky.jobs.utils.time.sleep', new=lambda *_, **__: None), \
                mock.patch('sky.jobs.utils.rich_utils.safe_status') as mock_safe_status, \
                mock.patch('sky.jobs.utils.managed_job_state.get_num_tasks',
                           return_value=1), \
                mock.patch('sky.jobs.utils.managed_job_state.get_status',
                           return_value=jobs_state.ManagedJobStatus.SUCCEEDED), \
                mock.patch('sky.jobs.utils.managed_job_state.get_all_task_ids_names_statuses_logs',
                           return_value=[(0, 'task0', jobs_state.ManagedJobStatus.SUCCEEDED, log_path)]), \
                mock.patch('sky.jobs.utils.managed_job_state.get_failure_reason',
                           return_value=None), \
                mock.patch('builtins.print') as mock_print:
            _setup_status_context(mock_safe_status)

            msg, exit_code = utils.stream_logs_by_id(job_id, follow=True)

            assert msg == ''
            assert exit_code == exceptions.JobExitCode.SUCCEEDED
            assert any('hello world' in str(call.args[0])
                       for call in mock_print.mock_calls
                       if call.args)
    finally:
        os.remove(log_path)


def test_stream_logs_by_id_shows_cancelled_message() -> None:
    job_id = 777
    handle = cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name='cluster-777',
        cluster_name_on_cloud='cluster-777',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=sky_resources.Resources(),
    )

    status_sequence = [
        jobs_state.ManagedJobStatus.RUNNING,
        jobs_state.ManagedJobStatus.RUNNING,
        jobs_state.ManagedJobStatus.CANCELLING,
        jobs_state.ManagedJobStatus.CANCELLED,
        jobs_state.ManagedJobStatus.CANCELLED,
    ]

    with mock.patch('sky.jobs.utils.time.sleep', new=lambda *_, **__: None), \
            mock.patch('sky.jobs.utils.rich_utils.safe_status') as mock_safe_status, \
            mock.patch('sky.jobs.utils.backends.CloudVmRayBackend') as mock_backend_cls, \
            mock.patch('sky.jobs.utils.managed_job_state.get_num_tasks',
                       return_value=1), \
            mock.patch('sky.jobs.utils.managed_job_state.get_status',
                       side_effect=status_sequence), \
            mock.patch('sky.jobs.utils.managed_job_state.get_latest_task_id_status',
                       return_value=(0, jobs_state.ManagedJobStatus.RUNNING)), \
            mock.patch('sky.jobs.utils.managed_job_state.get_task_name',
                       return_value='task-0'), \
            mock.patch('sky.jobs.utils.managed_job_state.get_pool_from_job_id',
                       return_value=None), \
            mock.patch('sky.jobs.utils.global_user_state.get_handle_from_cluster_name',
                       return_value=handle):
        status_display = _setup_status_context(mock_safe_status)

        backend = mock_backend_cls.return_value
        backend.tail_logs.return_value = 1
        backend.get_job_status.return_value = {
            'task': job_lib.JobStatus.CANCELLED
        }

        msg, exit_code = utils.stream_logs_by_id(job_id, follow=True)

        assert msg == ''
        assert exit_code == exceptions.JobExitCode.CANCELLED
        cancelled_msgs = [
            call.args[0]
            for call in status_display.update.mock_calls
            if call.args
        ]
        assert utils._JOB_CANCELLED_MESSAGE in cancelled_msgs
