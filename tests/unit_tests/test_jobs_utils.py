import logging
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


@pytest.mark.asyncio
@mock.patch('sky.jobs.utils.logger')
@mock.patch('sky.global_user_state.get_handle_from_cluster_name')
async def test_get_job_status_timeout(mock_get_handle, mock_logger):
    """Test that get_job_status times."""
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

    # Patch the timeout so the test passes quickly, while still checking the
    # timeout logic.
    with mock.patch.object(utils, '_JOB_STATUS_FETCH_TIMEOUT_SECONDS',
                           timeout_override):
        result = await utils.get_job_status(backend=mock_backend,
                                            cluster_name='test-cluster',
                                            job_id=1)
    assert result is None, 'Expected None when timeout occurs'

    elapsed_time = time.time() - start_time
    assert timeout_override <= elapsed_time < timeout_override + 1.0, (
        f'Expected timeout around {timeout_override}s, but took {elapsed_time}s'
    )

    # === Checking the job status... ===
    # Failed to get job status: Job status check timed out after 30s
    # ==================================
    assert mock_logger.info.call_count == 3
    error_log_line = mock_logger.info.call_args_list[1][0][0]
    assert 'Failed to get job status:' in error_log_line
    assert f'timed out after {timeout_override}s' in error_log_line


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
