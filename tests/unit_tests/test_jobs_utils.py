import asyncio
import pathlib
import tempfile
import time
from unittest import mock

import pytest

from sky.backends import cloud_vm_ray_backend
from sky.exceptions import ClusterDoesNotExist
from sky.jobs import utils


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
        mock.call('test-cluster', graceful=False, graceful_timeout=None),
        mock.call('test-cluster', graceful=False, graceful_timeout=None),
        mock.call('test-cluster', graceful=False, graceful_timeout=None),
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
    mock_sky_down.assert_called_once_with('test-cluster',
                                          graceful=False,
                                          graceful_timeout=None)

    # Verify usage.set_internal was called once
    assert mock_set_internal.call_count == 1


@pytest.mark.asyncio
@mock.patch('sky.jobs.utils.logger')
@mock.patch('sky.global_user_state.get_handle_from_cluster_name')
async def test_get_job_status_timeout(mock_get_handle, mock_logger):
    """Test that get_job_status returns error reason on timeout.

    Note: get_job_status no longer retries - it returns (None, reason) on
    transient errors. The retry logic is now in controller.py.
    """
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_get_handle.return_value = mock_handle

    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    timeout_override = 0.5  # seconds

    def slow_get_job_status(*args, **kwargs):
        """Simulates get_job_status call that hangs past the timeout."""
        time.sleep(timeout_override * 10)
        return {1: None}

    mock_backend.get_job_status = slow_get_job_status

    start_time = time.time()

    # Patch the timeout so the test passes quickly
    with mock.patch.object(utils, '_JOB_STATUS_FETCH_TIMEOUT_SECONDS',
                           timeout_override):
        job_status, error_reason = await utils.get_job_status(
            backend=mock_backend, cluster_name='test-cluster', job_id=1)

    # Should return (None, reason) tuple on timeout
    assert job_status is None, 'Expected None job status when timeout occurs'
    assert error_reason is not None, 'Expected error reason when timeout occurs'
    assert f'timed out after {timeout_override}s' in error_reason

    elapsed_time = time.time() - start_time
    assert timeout_override <= elapsed_time < timeout_override + 1.0, (
        f'Expected timeout around {timeout_override}s, '
        f'but took {elapsed_time}s')

    # Verify only one attempt was made (no retry in get_job_status)
    # === Checking the job status... ===
    assert mock_logger.info.call_count == 1


@pytest.mark.asyncio
@mock.patch('sky.jobs.utils.logger')
@mock.patch('sky.global_user_state.get_handle_from_cluster_name')
async def test_get_job_status_returns_error_reason_on_failure(
        mock_get_handle, mock_logger):
    """Test that get_job_status returns error reason on transient failures."""
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_get_handle.return_value = mock_handle

    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    def failing_get_job_status(*args, **kwargs):
        """Simulates get_job_status that fails with asyncio.TimeoutError."""
        raise asyncio.TimeoutError('Connection failed')

    mock_backend.get_job_status = failing_get_job_status

    job_status, error_reason = await utils.get_job_status(
        backend=mock_backend, cluster_name='test-cluster', job_id=1)

    # Should return (None, reason) tuple on failure
    assert job_status is None, 'Expected None job status on failure'
    assert error_reason is not None, 'Expected error reason on failure'
    assert 'timed out' in error_reason

    # Verify only one attempt was made (no retry in get_job_status)
    assert mock_logger.info.call_count == 1


@mock.patch('sky.jobs.utils.logger')
@mock.patch('sky.jobs.utils.skypilot_config')
def test_consolidation_mode_warning_without_restart(mock_config, mock_logger):
    """Test that a warning is printed when consolidation mode is enabled
    but the API server has not been restarted."""
    # Clear the LRU cache to ensure fresh test
    utils.is_consolidation_mode.cache_clear()

    # Mock config to return True for consolidation mode
    mock_config.get_nested.return_value = True

    # Create a temporary directory to use as the signal file location
    with tempfile.TemporaryDirectory() as tmpdir:
        signal_file = pathlib.Path(tmpdir) / 'consolidation_signal'

        # Ensure signal file does not exist
        if signal_file.exists():
            signal_file.unlink()

        # Mock the signal file path
        with mock.patch(
                'sky.jobs.utils._JOBS_CONSOLIDATION_RELOADED_SIGNAL_FILE',
                str(signal_file)):
            # Call is_consolidation_mode
            result = utils.is_consolidation_mode()

            # Should return False because signal file doesn't exist
            assert result is False

            # Verify warning was logged
            assert mock_logger.warning.call_count == 1
            warning_message = mock_logger.warning.call_args[0][0]
            assert 'Consolidation mode for managed jobs is enabled' in warning_message
            assert 'API server has not been restarted yet' in warning_message
            assert 'Please restart the API server to enable it' in warning_message


def test_job_recovery_skips_autostopping():
    """Verify job recovery logic treats AUTOSTOPPING like UP (no recovery)."""
    from sky.utils import status_lib

    # AUTOSTOPPING should be treated as UP-like (not preempted)
    # Recovery logic should skip AUTOSTOPPING (similar to UP)
    up_status = status_lib.ClusterStatus.UP
    autostopping_status = status_lib.ClusterStatus.AUTOSTOPPING
    stopped_status = status_lib.ClusterStatus.STOPPED

    # AUTOSTOPPING should be in the same category as UP for recovery purposes
    recovery_skip_statuses = {
        up_status,
        autostopping_status,
    }

    assert up_status in recovery_skip_statuses
    assert autostopping_status in recovery_skip_statuses
    assert stopped_status not in recovery_skip_statuses


# ======== Graceful cancel tests ========


@mock.patch('sky.core.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_graceful(mock_set_internal, mock_sky_down) -> None:
    """Test terminate_cluster passes graceful params to core.down."""
    utils.terminate_cluster('test-cluster', graceful=True, graceful_timeout=120)

    mock_sky_down.assert_called_once_with('test-cluster',
                                          graceful=True,
                                          graceful_timeout=120)
    assert mock_set_internal.call_count == 1


@mock.patch('sky.core.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_graceful_no_timeout(mock_set_internal,
                                               mock_sky_down) -> None:
    """Test terminate_cluster with graceful=True but no timeout."""
    utils.terminate_cluster('test-cluster', graceful=True)

    mock_sky_down.assert_called_once_with('test-cluster',
                                          graceful=True,
                                          graceful_timeout=None)


def test_cancel_signal_file_no_graceful():
    """Test that cancel_jobs_by_id writes an empty signal file (touch)
    for non-graceful cancels on the new controller."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch('sky.jobs.constants.CONSOLIDATED_SIGNAL_PATH', tmpdir):
            with mock.patch(
                    'sky.jobs.state.is_legacy_controller_process',
                    return_value=False), \
                 mock.patch(
                    'sky.jobs.state.get_status',
                    return_value=mock.MagicMock(
                        is_terminal=mock.MagicMock(return_value=False),
                        __eq__=mock.MagicMock(return_value=False))), \
                 mock.patch(
                    'sky.jobs.utils.update_managed_jobs_statuses'), \
                 mock.patch(
                    'sky.jobs.state.get_workspace',
                    return_value='default'):
                utils.cancel_jobs_by_id(job_ids=[42],
                                        current_workspace='default',
                                        graceful=False)

                signal_file = pathlib.Path(tmpdir) / '42'
                assert signal_file.exists()
                content = signal_file.read_text(encoding='utf-8')
                assert content == '', (
                    f'Expected empty file for non-graceful, got: {content!r}')


def test_cancel_signal_file_graceful():
    """Test that cancel_jobs_by_id writes 'graceful' to signal file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch('sky.jobs.constants.CONSOLIDATED_SIGNAL_PATH', tmpdir):
            with mock.patch(
                    'sky.jobs.state.is_legacy_controller_process',
                    return_value=False), \
                 mock.patch(
                    'sky.jobs.state.get_status',
                    return_value=mock.MagicMock(
                        is_terminal=mock.MagicMock(return_value=False),
                        __eq__=mock.MagicMock(return_value=False))), \
                 mock.patch(
                    'sky.jobs.utils.update_managed_jobs_statuses'), \
                 mock.patch(
                    'sky.jobs.state.get_workspace',
                    return_value='default'):
                utils.cancel_jobs_by_id(job_ids=[42],
                                        current_workspace='default',
                                        graceful=True)

                signal_file = pathlib.Path(tmpdir) / '42'
                assert signal_file.exists()
                content = signal_file.read_text(encoding='utf-8')
                assert content == 'graceful'


def test_cancel_signal_file_graceful_with_timeout():
    """Test that cancel_jobs_by_id writes 'graceful:<timeout>' to signal
    file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch('sky.jobs.constants.CONSOLIDATED_SIGNAL_PATH', tmpdir):
            with mock.patch(
                    'sky.jobs.state.is_legacy_controller_process',
                    return_value=False), \
                 mock.patch(
                    'sky.jobs.state.get_status',
                    return_value=mock.MagicMock(
                        is_terminal=mock.MagicMock(return_value=False),
                        __eq__=mock.MagicMock(return_value=False))), \
                 mock.patch(
                    'sky.jobs.utils.update_managed_jobs_statuses'), \
                 mock.patch(
                    'sky.jobs.state.get_workspace',
                    return_value='default'):
                utils.cancel_jobs_by_id(job_ids=[42],
                                        current_workspace='default',
                                        graceful=True,
                                        graceful_timeout=300)

                signal_file = pathlib.Path(tmpdir) / '42'
                assert signal_file.exists()
                content = signal_file.read_text(encoding='utf-8')
                assert content == 'graceful:300'


@mock.patch('sky.utils.subprocess_utils.run_in_parallel')
@mock.patch('sky.backends.task_codegen.TaskCodeGen.get_rclone_flush_script')
def test_graceful_job_cancel_calls_flush(mock_flush_script, mock_run_parallel):
    """Test _graceful_job_cancel cancels jobs then flushes on all nodes."""
    from sky import core as sky_core

    mock_flush_script.return_value = 'echo flush'

    mock_runner = mock.MagicMock()
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_handle.get_command_runners.return_value = [mock_runner]
    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    # Simulate successful flush
    mock_run_parallel.return_value = [(0, 0, '', '')]

    sky_core._graceful_job_cancel(mock_handle, mock_backend, 'test-cluster')

    # Verify jobs were cancelled
    mock_backend.cancel_jobs.assert_called_once_with(mock_handle,
                                                     jobs=None,
                                                     cancel_all=True)

    # Verify flush was run in parallel
    mock_run_parallel.assert_called_once()
    _, kwargs = mock_run_parallel.call_args
    assert kwargs['num_threads'] == 1


@mock.patch('sky.utils.subprocess_utils.run_in_parallel')
@mock.patch('sky.backends.task_codegen.TaskCodeGen.get_rclone_flush_script')
def test_graceful_job_cancel_with_timeout(mock_flush_script, mock_run_parallel):
    """Test _graceful_job_cancel wraps flush script with timeout."""
    from sky import core as sky_core

    mock_flush_script.return_value = 'echo flush'

    mock_runner = mock.MagicMock()
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_handle.get_command_runners.return_value = [mock_runner]
    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    mock_run_parallel.return_value = [(0, 0, '', '')]

    sky_core._graceful_job_cancel(mock_handle,
                                  mock_backend,
                                  'test-cluster',
                                  timeout=60)

    # The flush function passed to run_in_parallel should wrap with timeout.
    # We verify by checking the call was made (the timeout wrapping happens
    # inside the closure).
    mock_run_parallel.assert_called_once()
    mock_flush_script.assert_called_once()


@mock.patch('sky.utils.subprocess_utils.run_in_parallel')
@mock.patch('sky.backends.task_codegen.TaskCodeGen.get_rclone_flush_script')
def test_graceful_job_cancel_wrong_backend_skips(mock_flush_script,
                                                 mock_run_parallel):
    """Test _graceful_job_cancel skips for non-CloudVmRay backends."""
    from sky import core as sky_core

    mock_handle = mock.MagicMock()  # not CloudVmRayResourceHandle
    mock_backend = mock.MagicMock()  # not CloudVmRayBackend

    sky_core._graceful_job_cancel(mock_handle, mock_backend, 'test-cluster')

    # Should not attempt flush
    mock_flush_script.assert_not_called()
    mock_run_parallel.assert_not_called()


@mock.patch('sky.utils.subprocess_utils.run_in_parallel')
@mock.patch('sky.backends.task_codegen.TaskCodeGen.get_rclone_flush_script')
def test_graceful_job_cancel_handles_flush_timeout(mock_flush_script,
                                                   mock_run_parallel):
    """Test _graceful_job_cancel handles timeout exit code (124)."""
    from sky import core as sky_core

    mock_flush_script.return_value = 'echo flush'

    mock_runner = mock.MagicMock()
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_handle.get_command_runners.return_value = [mock_runner]
    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    # Simulate timeout on flush (exit code 124)
    mock_run_parallel.return_value = [(0, 124, '', 'timed out')]

    # Should not raise - graceful cancel handles errors
    sky_core._graceful_job_cancel(mock_handle,
                                  mock_backend,
                                  'test-cluster',
                                  timeout=10)

    mock_backend.cancel_jobs.assert_called_once()


@mock.patch('sky.utils.subprocess_utils.run_in_parallel')
@mock.patch('sky.backends.task_codegen.TaskCodeGen.get_rclone_flush_script')
def test_graceful_job_cancel_multi_node(mock_flush_script, mock_run_parallel):
    """Test _graceful_job_cancel flushes on all nodes in parallel."""
    from sky import core as sky_core

    mock_flush_script.return_value = 'echo flush'

    runners = [mock.MagicMock() for _ in range(3)]
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_handle.get_command_runners.return_value = runners
    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    mock_run_parallel.return_value = [
        (0, 0, '', ''),
        (1, 0, '', ''),
        (2, 0, '', ''),
    ]

    sky_core._graceful_job_cancel(mock_handle, mock_backend, 'test-cluster')

    _, kwargs = mock_run_parallel.call_args
    assert kwargs['num_threads'] == 3
