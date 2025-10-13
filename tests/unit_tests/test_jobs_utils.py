import logging
import sys
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
    """Test that get_job_status times out after 30 seconds."""
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_get_handle.return_value = mock_handle

    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    def slow_get_job_status(*args, **kwargs):
        """Simulates get_job_status call that hangs."""
        time.sleep(60)  # Sleep longer than the 30s timeout.
        return {1: None}

    mock_backend.get_job_status = slow_get_job_status

    test_logger = logging.getLogger('test_logger')

    start_time = time.time()
    result = await utils.get_job_status(backend=mock_backend,
                                        cluster_name='test-cluster',
                                        job_id=1,
                                        job_logger=test_logger)
    assert result is None, 'Expected None when timeout occurs'

    elapsed_time = time.time() - start_time
    assert elapsed_time >= 30 and elapsed_time < 31, f'Expected timeout around 30s, but took {elapsed_time}s'

    # one for "checking the job status" message, one for failure reason.
    assert mock_logger.info.call_count == 2

    second_call = mock_logger.info.call_args_list[0][0][0]
    assert 'Failed to get job status:' in second_call
    assert 'timed out after 30s' in second_call
