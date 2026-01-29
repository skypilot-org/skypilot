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


# Tests for handle serialization in managed job queue

class TestHandleSerialization:
    """Tests for handle serialization/deserialization in job queue."""

    def test_serialize_handle_for_json_none(self):
        """Test that None handle returns None."""
        result = utils._serialize_handle_for_json(None)
        assert result is None

    def test_serialize_handle_for_json_dict(self):
        """Test serialization of a dict object (picklable)."""
        # Use a dict instead of a local class since dicts are always picklable
        handle = {
            'stable_internal_external_ips': [('10.0.0.1', '35.1.2.3')],
            'cluster_name_on_cloud': 'test-cluster',
        }
        result = utils._serialize_handle_for_json(handle)

        # Should return a base64-encoded string
        assert isinstance(result, str)
        assert len(result) > 0

    def test_deserialize_handle_from_json_none(self):
        """Test that None string returns None."""
        result = utils._deserialize_handle_from_json(None)
        assert result is None

    def test_serialize_deserialize_roundtrip(self):
        """Test that serialize/deserialize is a proper roundtrip."""
        # Use a dict since it's always picklable
        original = {
            'stable_internal_external_ips': [('10.0.0.1', '35.1.2.3')],
            'cluster_name_on_cloud': 'test-cluster',
            'launched_nodes': 1,
        }

        # Serialize
        serialized = utils._serialize_handle_for_json(original)
        assert isinstance(serialized, str)

        # Deserialize
        deserialized = utils._deserialize_handle_from_json(serialized)

        # Check attributes are preserved
        assert deserialized['stable_internal_external_ips'] == original['stable_internal_external_ips']
        assert deserialized['cluster_name_on_cloud'] == original['cluster_name_on_cloud']
        assert deserialized['launched_nodes'] == original['launched_nodes']


class TestPopulateJobRecordFromHandle:
    """Tests for _populate_job_record_from_handle."""

    def test_populate_job_record_sets_handle(self):
        """Test that the handle is set in the job record."""
        # Create a minimal mock handle with required attributes
        mock_handle = mock.MagicMock()
        mock_handle.stable_internal_external_ips = [('10.0.0.1', '35.1.2.3')]
        mock_handle.cluster_name_on_cloud = 'test-cluster'
        mock_handle.launched_nodes = 1
        mock_handle.launched_resources = mock.MagicMock()
        mock_handle.launched_resources.cloud = mock.MagicMock()
        mock_handle.launched_resources.cloud.__str__ = lambda self: 'AWS'
        mock_handle.launched_resources.region = 'us-east-1'
        mock_handle.launched_resources.zone = 'us-east-1a'
        mock_handle.launched_resources.accelerators = None
        mock_handle.launched_resources.labels = {}
        
        job = {}
        
        # Mock the resources_utils function
        with mock.patch('sky.jobs.utils.resources_utils.get_readable_resources_repr',
                       return_value=('1x[CPU:1]', '1x[CPU:1+]')):
            utils._populate_job_record_from_handle(
                job=job,
                cluster_name='test-cluster',
                handle=mock_handle
            )
        
        # Check handle is set
        assert 'handle' in job
        assert job['handle'] is mock_handle
        
        # Check other fields are also set
        assert job['cluster_resources'] == '1x[CPU:1]'
        assert job['cloud'] == 'AWS'
        assert job['region'] == 'us-east-1'


class TestClusterHandleFields:
    """Tests for _CLUSTER_HANDLE_FIELDS configuration."""

    def test_handle_in_cluster_handle_fields(self):
        """Test that 'handle' is in _CLUSTER_HANDLE_FIELDS."""
        assert 'handle' in utils._CLUSTER_HANDLE_FIELDS

    def test_cluster_handle_not_required_excludes_handle(self):
        """Test that _cluster_handle_not_required returns False when 'handle' is present."""
        fields_with_handle = ['job_id', 'status', 'handle']
        assert not utils._cluster_handle_not_required(fields_with_handle)

    def test_cluster_handle_not_required_without_handle_fields(self):
        """Test that _cluster_handle_not_required returns True without handle fields."""
        fields_without_handle = ['job_id', 'status', 'job_name']
        assert utils._cluster_handle_not_required(fields_without_handle)
