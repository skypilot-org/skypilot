"""Unit tests for sky.server.requests.requests module."""
import asyncio
import logging
import pathlib
import time
from typing import List, Optional
import unittest.mock as mock

import filelock
import pytest

from sky.server.requests import payloads
from sky.server.requests import requests
from sky.server.requests.requests import RequestStatus
from sky.server.requests.requests import ScheduleType


def dummy():
    return None


@pytest.fixture()
def isolated_database(tmp_path):
    """Create an isolated database for each test to prevent interference."""
    # Create temporary paths for database and logs
    temp_db_path = tmp_path / "requests.db"
    temp_log_path = tmp_path / "logs"
    temp_log_path.mkdir()

    # Patch the database path and log path constants
    with mock.patch('sky.server.constants.API_SERVER_REQUEST_DB_PATH',
                    str(temp_db_path)):
        with mock.patch('sky.server.requests.requests.REQUEST_LOG_PATH_PREFIX',
                        str(temp_log_path)):
            # Reset the global database variable to force re-initialization
            requests._DB = None
            yield
            # Clean up after the test
            requests._DB = None


@pytest.mark.asyncio
@pytest.mark.parametrize('test_async', [True, False])
async def test_set_request_failed(isolated_database, test_async):
    request = requests.Request(request_id='test-request-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.RUNNING,
                               created_at=0.0,
                               finished_at=0.0,
                               user_id='test-user')

    await requests.create_if_not_exists_async(request)
    try:
        raise ValueError('Boom!')
    except ValueError as e:
        if test_async:
            await requests.set_request_failed_async('test-request-1', e)
        else:
            requests.set_request_failed('test-request-1', e)

    # Get the updated request
    if test_async:
        updated_request = await requests.get_request_async('test-request-1')
    else:
        updated_request = requests.get_request('test-request-1')

    # Verify the request was updated correctly
    assert updated_request is not None
    assert updated_request.status == RequestStatus.FAILED

    # Verify the error was set correctly
    error = updated_request.get_error()
    assert error is not None
    assert error['type'] == 'ValueError'
    assert error['message'] == 'Boom!'
    assert error['object'] is not None


def test_set_request_failed_nonexistent_request(isolated_database):
    # Try to set a non-existent request as failed
    with pytest.raises(AssertionError):
        requests.set_request_failed('nonexistent-request',
                                    ValueError('Test error'))


@pytest.mark.asyncio
@pytest.mark.parametrize('test_async', [True, False])
async def test_set_request_succeeded(isolated_database, test_async):
    request = requests.Request(request_id='test-request-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.RUNNING,
                               created_at=0.0,
                               finished_at=0.0,
                               user_id='test-user')

    await requests.create_if_not_exists_async(request)
    result = {'key': 'value', 'number': 42}
    if test_async:
        await requests.set_request_succeeded_async('test-request-1', result)
    else:
        requests.set_request_succeeded('test-request-1', result)

    # Get the updated request
    if test_async:
        updated_request = await requests.get_request_async('test-request-1')
    else:
        updated_request = requests.get_request('test-request-1')

    # Verify the request was updated correctly
    assert updated_request is not None
    assert updated_request.status == RequestStatus.SUCCEEDED
    assert updated_request.finished_at > 0.0

    # Verify the return value was set correctly
    returned_value = updated_request.get_return_value()
    assert returned_value == result


def test_set_request_succeeded_nonexistent_request(isolated_database):
    # Try to set a non-existent request as succeeded
    with pytest.raises(AssertionError):
        requests.set_request_succeeded('nonexistent-request', {'result': 'ok'})


@pytest.mark.asyncio
async def test_clean_finished_requests_with_retention(isolated_database):
    """Test cleaning up old finished requests."""
    current_time = time.time()
    retention_seconds = 60  # 1 minute retention

    # Create test requests with different statuses and ages
    old_finished_request = requests.Request(
        request_id='old-finished-1',
        name='test-request',
        entrypoint=dummy,
        request_body=payloads.RequestBody(),
        status=RequestStatus.SUCCEEDED,
        created_at=current_time - 180,
        finished_at=current_time - 120,  # 2 minutes old
        user_id='test-user')

    recent_finished_request = requests.Request(
        request_id='recent-finished-1',
        name='test-request',
        entrypoint=dummy,
        request_body=payloads.RequestBody(),
        status=RequestStatus.FAILED,
        created_at=current_time - 180,
        finished_at=current_time - 30,  # 30 seconds old
        user_id='test-user')

    old_running_request = requests.Request(
        request_id='old-running-1',
        name='test-request',
        entrypoint=dummy,
        request_body=payloads.RequestBody(),
        status=RequestStatus.RUNNING,
        created_at=current_time - 180,
        finished_at=current_time - 120,  # 2 minutes old
        user_id='test-user')

    # Create the requests in the database
    await requests.create_if_not_exists_async(old_finished_request)
    await requests.create_if_not_exists_async(recent_finished_request)
    await requests.create_if_not_exists_async(old_running_request)

    # Mock log file unlinking
    with mock.patch.object(pathlib.Path, 'unlink') as mock_unlink:
        with mock.patch('sky.server.requests.requests.logger') as mock_logger:
            mock_logger.getEffectiveLevel.return_value = logging.INFO
            await requests.clean_finished_requests_with_retention(
                retention_seconds)

    # Verify old finished request was deleted
    assert requests.get_request('old-finished-1') is None

    # Verify recent finished request was NOT deleted
    assert requests.get_request('recent-finished-1') is not None

    # Verify old running request was NOT deleted
    assert requests.get_request('old-running-1') is not None

    # Verify log file unlink was called for both current and legacy paths
    # (2 calls per deleted request: current path + legacy path)
    assert mock_unlink.call_count == 2

    # Verify logging
    mock_logger.info.assert_called_once()
    log_message = mock_logger.info.call_args[0][0]
    assert 'Cleaned up 1 finished requests' in log_message


@pytest.mark.asyncio
async def test_clean_finished_requests_with_retention_batch_size_functionality(
        isolated_database):
    """Test that the batch_size parameter controls batch size in cleanup operations."""
    current_time = time.time()
    retention_seconds = 60  # 1 minute retention

    # Create 25 old finished requests
    old_requests = []
    for i in range(25):
        request = requests.Request(
            request_id=f'old-batch-{i:03d}',
            name='test-request',
            entrypoint=dummy,
            request_body=payloads.RequestBody(),
            status=RequestStatus.SUCCEEDED,
            created_at=current_time - 180,
            finished_at=current_time -
            120,  # 2 minutes old (older than retention)
            user_id='test-user')
        old_requests.append(request)
        await requests.create_if_not_exists_async(request)

    # Create 5 recent finished requests (should not be deleted)
    recent_requests = []
    for i in range(5):
        request = requests.Request(
            request_id=f'recent-batch-{i:03d}',
            name='test-request',
            entrypoint=dummy,
            request_body=payloads.RequestBody(),
            status=RequestStatus.SUCCEEDED,
            created_at=current_time - 60,
            finished_at=current_time -
            30,  # 30 seconds old (newer than retention)
            user_id='test-user')
        recent_requests.append(request)
        await requests.create_if_not_exists_async(request)

    # Verify all requests exist before cleanup
    assert len([
        req for req in old_requests
        if requests.get_request(req.request_id) is not None
    ]) == 25
    assert len([
        req for req in recent_requests
        if requests.get_request(req.request_id) is not None
    ]) == 5

    # Test with limit=10 - should process in batches of 10
    with mock.patch.object(pathlib.Path, 'unlink') as mock_unlink:
        with mock.patch('sky.server.requests.requests.logger') as mock_logger:
            mock_logger.getEffectiveLevel.return_value = logging.INFO
            # Track how get_request_tasks_async is called to verify batching
            original_get_request_tasks_async = requests.get_request_tasks_async
            call_counts = []

            async def track_get_request_tasks_async(req_filter):
                result = await original_get_request_tasks_async(req_filter)
                call_counts.append(len(result))
                return result

            with mock.patch(
                    'sky.server.requests.requests.get_request_tasks_async',
                    side_effect=track_get_request_tasks_async):
                await requests.clean_finished_requests_with_retention(
                    retention_seconds, batch_size=10)

    # Verify all old requests were deleted
    for req in old_requests:
        assert requests.get_request(req.request_id) is None

    # Verify recent requests were NOT deleted
    for req in recent_requests:
        assert requests.get_request(req.request_id) is not None

    # Verify batching occurred - should have made multiple calls with decreasing counts
    # First call should return 10 (limited), second call should return 10, third call should return 5
    assert len(call_counts) == 3  # At 3 calls (10, 10, 5)
    assert call_counts[0] == 10  # First batch
    assert call_counts[1] == 10  # Second batch
    assert call_counts[2] == 5  # Third batch (remaining)

    # Verify log file unlink was called for each deleted request
    # (2 calls per request: current path + legacy path)
    assert mock_unlink.call_count == 50

    # Verify logging shows correct total
    mock_logger.info.assert_called_once()
    log_message = mock_logger.info.call_args[0][0]
    assert 'Cleaned up 25 finished requests' in log_message


@pytest.mark.asyncio
async def test_clean_finished_requests_with_retention_limit_larger_than_total(
        isolated_database):
    """Test cleanup when batch_size is larger than total number of old requests."""
    current_time = time.time()
    retention_seconds = 60

    # Create only 5 old finished requests
    old_requests = []
    for i in range(5):
        request = requests.Request(
            request_id=f'small-batch-{i:03d}',
            name='test-request',
            entrypoint=dummy,
            request_body=payloads.RequestBody(),
            status=RequestStatus.SUCCEEDED,
            created_at=current_time - 180,
            finished_at=current_time - 120,  # 2 minutes old
            user_id='test-user')
        old_requests.append(request)
        await requests.create_if_not_exists_async(request)

    # Test with limit=100 (much larger than the 5 requests)
    with mock.patch.object(pathlib.Path, 'unlink'):
        with mock.patch('sky.server.requests.requests.logger') as mock_logger:
            mock_logger.getEffectiveLevel.return_value = logging.INFO
            # Track calls to verify single batch processing
            original_get_request_tasks_async = requests.get_request_tasks_async
            call_counts = []

            async def track_get_request_tasks_async(req_filter):
                result = await original_get_request_tasks_async(req_filter)
                call_counts.append(len(result))
                return result

            with mock.patch(
                    'sky.server.requests.requests.get_request_tasks_async',
                    side_effect=track_get_request_tasks_async):
                await requests.clean_finished_requests_with_retention(
                    retention_seconds, batch_size=100)

    # Verify all old requests were deleted
    for req in old_requests:
        assert requests.get_request(req.request_id) is None

    # Should only need 1 calls: one returning 5 requests(termination)
    assert len(call_counts) == 1
    assert call_counts[0] == 5  # All 5 requests in the batch

    # Verify logging
    mock_logger.info.assert_called_once()
    log_message = mock_logger.info.call_args[0][0]
    assert 'Cleaned up 5 finished requests' in log_message


@pytest.mark.asyncio
async def test_clean_finished_requests_with_retention_batch_size_one(
        isolated_database):
    """Test cleanup with batch_size=1 to verify single-request batching."""
    current_time = time.time()
    retention_seconds = 60

    # Create 3 old finished requests
    old_requests = []
    for i in range(3):
        request = requests.Request(
            request_id=f'single-batch-{i:03d}',
            name='test-request',
            entrypoint=dummy,
            request_body=payloads.RequestBody(),
            status=RequestStatus.SUCCEEDED,
            created_at=current_time - 180,
            finished_at=current_time - 120,  # 2 minutes old
            user_id='test-user')
        old_requests.append(request)
        await requests.create_if_not_exists_async(request)

    # Test with limit=1 (process one at a time)
    with mock.patch.object(pathlib.Path, 'unlink'):
        with mock.patch('sky.server.requests.requests.logger') as mock_logger:
            mock_logger.getEffectiveLevel.return_value = logging.INFO
            # Track calls to verify single-request processing
            original_get_request_tasks_async = requests.get_request_tasks_async
            call_counts = []

            async def track_get_request_tasks_async(req_filter):
                result = await original_get_request_tasks_async(req_filter)
                call_counts.append(len(result))
                return result

            with mock.patch(
                    'sky.server.requests.requests.get_request_tasks_async',
                    side_effect=track_get_request_tasks_async):
                await requests.clean_finished_requests_with_retention(
                    retention_seconds, batch_size=1)

    # Verify all old requests were deleted
    for req in old_requests:
        assert requests.get_request(req.request_id) is None

    # Should need 4 calls: three returning 1 request each, one returning 0 (termination)
    assert len(call_counts) == 4
    assert call_counts[0] == 1  # First request
    assert call_counts[1] == 1  # Second request
    assert call_counts[2] == 1  # Third request
    assert call_counts[3] == 0  # Empty result terminates loop

    # Verify logging
    mock_logger.info.assert_called_once()
    log_message = mock_logger.info.call_args[0][0]
    assert 'Cleaned up 3 finished requests' in log_message


@pytest.mark.asyncio
async def test_clean_finished_requests_with_retention_no_old_requests(
        isolated_database):
    """Test cleanup when there are no old requests to clean."""
    current_time = time.time()
    retention_seconds = 60

    # Create a recent finished request
    recent_request = requests.Request(
        request_id='recent-test-1',
        name='test-request',
        entrypoint=dummy,
        request_body=payloads.RequestBody(),
        status=RequestStatus.SUCCEEDED,
        created_at=current_time - 180,
        finished_at=current_time - 30,  # 30 seconds old
        user_id='test-user')

    await requests.create_if_not_exists_async(recent_request)

    with mock.patch('sky.server.requests.requests.logger') as mock_logger:
        mock_logger.getEffectiveLevel.return_value = logging.INFO
        await requests.clean_finished_requests_with_retention(retention_seconds)

    # Verify request was NOT deleted
    assert requests.get_request('recent-test-1') is not None

    # Verify logging shows 0 cleaned requests
    mock_logger.info.assert_called_once()
    log_message = mock_logger.info.call_args[0][0]
    assert 'Cleaned up 0 finished requests' in log_message


@pytest.mark.asyncio
async def test_clean_finished_requests_with_retention_all_statuses(
        isolated_database):
    """Test cleanup works for all finished statuses."""
    current_time = time.time()
    retention_seconds = 60

    # Create old requests with all finished statuses
    succeeded_request = requests.Request(request_id='old-succeeded-1',
                                         name='test-request',
                                         entrypoint=dummy,
                                         request_body=payloads.RequestBody(),
                                         status=RequestStatus.SUCCEEDED,
                                         created_at=current_time - 180,
                                         finished_at=current_time - 120,
                                         user_id='test-user')

    failed_request = requests.Request(request_id='old-failed-1',
                                      name='test-request',
                                      entrypoint=dummy,
                                      request_body=payloads.RequestBody(),
                                      status=RequestStatus.FAILED,
                                      created_at=current_time - 180,
                                      finished_at=current_time - 120,
                                      user_id='test-user')

    cancelled_request = requests.Request(request_id='old-cancelled-1',
                                         name='test-request',
                                         entrypoint=dummy,
                                         request_body=payloads.RequestBody(),
                                         status=RequestStatus.CANCELLED,
                                         created_at=current_time - 180,
                                         finished_at=current_time - 120,
                                         user_id='test-user')

    await requests.create_if_not_exists_async(succeeded_request)
    await requests.create_if_not_exists_async(failed_request)
    await requests.create_if_not_exists_async(cancelled_request)

    with mock.patch.object(pathlib.Path, 'unlink'):
        with mock.patch('sky.server.requests.requests.logger') as mock_logger:
            mock_logger.getEffectiveLevel.return_value = logging.INFO
            await requests.clean_finished_requests_with_retention(
                retention_seconds)

    # Verify all finished requests were deleted
    assert requests.get_request('old-succeeded-1') is None
    assert requests.get_request('old-failed-1') is None
    assert requests.get_request('old-cancelled-1') is None

    # Verify logging shows 3 cleaned requests
    mock_logger.info.assert_called_once()
    log_message = mock_logger.info.call_args[0][0]
    assert 'Cleaned up 3 finished requests' in log_message


@pytest.mark.asyncio
async def test_requests_gc_daemon(isolated_database):
    """Test the garbage collection daemon runs correctly with real cleanup."""
    current_time = time.time()

    # Create test requests: some old finished, some recent, some running
    old_finished_request = requests.Request(
        request_id='old-finished-gc-1',
        name='test-request',
        entrypoint=dummy,
        request_body=payloads.RequestBody(),
        status=RequestStatus.SUCCEEDED,
        created_at=current_time - 180,
        finished_at=current_time - 150,  # Finished 150 seconds ago
        user_id='test-user')

    recent_finished_request = requests.Request(
        request_id='recent-finished-gc-1',
        name='test-request',
        entrypoint=dummy,
        request_body=payloads.RequestBody(),
        status=RequestStatus.FAILED,
        created_at=current_time - 60,
        finished_at=current_time - 30,  # Finished 30 seconds ago
        user_id='test-user')

    running_request = requests.Request(request_id='running-gc-1',
                                       name='test-request',
                                       entrypoint=dummy,
                                       request_body=payloads.RequestBody(),
                                       status=RequestStatus.RUNNING,
                                       created_at=current_time - 180,
                                       user_id='test-user')

    # Create the requests in the database
    await requests.create_if_not_exists_async(old_finished_request)
    await requests.create_if_not_exists_async(recent_finished_request)
    await requests.create_if_not_exists_async(running_request)

    # Verify all requests exist before cleanup
    assert requests.get_request('old-finished-gc-1') is not None
    assert requests.get_request('recent-finished-gc-1') is not None
    assert requests.get_request('running-gc-1') is not None

    # Mock the entire daemon to run only one iteration by patching the loop
    async def single_iteration_daemon():
        """Modified daemon that runs only one iteration."""
        logger = requests.logger
        logger.info('Running requests GC daemon...')
        # Use the latest config.
        requests.skypilot_config.reload_config()
        retention_seconds = requests.skypilot_config.get_nested(
            ('api_server', 'requests_retention_hours'),
            requests.DEFAULT_REQUESTS_RETENTION_HOURS) * 3600
        try:
            # Negative value disables the requests GC
            if retention_seconds >= 0:
                await requests.clean_finished_requests_with_retention(
                    retention_seconds)
        except Exception as e:  # pylint: disable=broad-except
            import traceback
            logger.error(f'Error running requests GC daemon: {e}'
                         f'traceback: {traceback.format_exc()}')
        # Don't loop - just return after one iteration

    with mock.patch(
            'sky.server.requests.requests.skypilot_config') as mock_config:
        # Configure retention to 2 minutes (0.033 hours)
        # This will be converted to 0.033 * 3600 = ~120 seconds
        mock_config.get_nested.return_value = 120 / 3600  # 2 minutes in hours
        mock_config.reload_config.return_value = None

        # Run the single iteration daemon
        await single_iteration_daemon()

        # Verify config was called
        mock_config.reload_config.assert_called_once()
        mock_config.get_nested.assert_called_once_with(
            ('api_server', 'requests_retention_hours'), 24)

        # Verify cleanup was actually performed on old finished requests
        # Old finished request should be deleted (finished 150s ago > 120s)
        assert requests.get_request('old-finished-gc-1') is None

        # Recent finished request should still exist (finished 30s ago < 120s)
        assert requests.get_request('recent-finished-gc-1') is not None

        # Running request should still exist (not finished)
        assert requests.get_request('running-gc-1') is not None


@pytest.mark.asyncio
async def test_requests_gc_daemon_disabled(isolated_database):
    """Test daemon when retention is negative (disabled)."""
    with mock.patch(
            'sky.server.requests.requests.skypilot_config') as mock_config:
        with mock.patch(
                'sky.server.requests.requests.clean_finished_requests_with_retention'
        ) as mock_clean:
            with mock.patch('asyncio.sleep') as mock_sleep:
                # Configure negative retention (disabled)
                mock_config.get_nested.return_value = -1

                # Make sleep raise CancelledError after first iteration
                mock_sleep.side_effect = [None, asyncio.CancelledError()]

                # Run the daemon
                with pytest.raises(asyncio.CancelledError):
                    await requests.requests_gc_daemon()

                # Verify cleanup was NOT called due to negative retention
                mock_clean.assert_not_called()

                # Verify sleep was called with max(-1, 3600) = 3600
                assert mock_sleep.call_count == 2
                mock_sleep.assert_any_call(3600)


def test_get_legacy_log_path():
    """Test _get_legacy_log_path returns correct legacy path."""
    # Test that legacy log path uses LEGACY_REQUEST_LOG_PATH_PREFIX
    request_id = 'test-request-123'
    legacy_path = requests._get_legacy_log_path(request_id)

    # Verify the path is under the legacy prefix
    expected_prefix = pathlib.Path(
        requests.LEGACY_REQUEST_LOG_PATH_PREFIX).expanduser().absolute()
    assert legacy_path.parent == expected_prefix
    assert legacy_path.name == f'{request_id}.log'
    assert str(legacy_path).endswith('.log')

    # Verify it's different from the current path
    current_path_prefix = pathlib.Path(
        requests.REQUEST_LOG_PATH_PREFIX).expanduser().absolute()
    assert legacy_path.parent != current_path_prefix


@pytest.mark.asyncio
async def test_clean_finished_requests_cleans_both_paths(
        isolated_database, tmp_path):
    """Test that GC cleans logs from both current and legacy paths."""
    current_time = time.time()
    retention_seconds = 60

    # Create an old finished request
    old_request = requests.Request(request_id='legacy-test-req-1',
                                   name='test-request',
                                   entrypoint=dummy,
                                   request_body=payloads.RequestBody(),
                                   status=RequestStatus.SUCCEEDED,
                                   created_at=current_time - 180,
                                   finished_at=current_time - 120,
                                   user_id='test-user')

    await requests.create_if_not_exists_async(old_request)

    # Track which paths unlink was called on
    unlinked_paths = []

    async def track_unlink(self, missing_ok=False):
        unlinked_paths.append(str(self))

    # Patch both the legacy constant and the unlink method
    legacy_log_dir = tmp_path / 'legacy_logs'
    legacy_log_dir.mkdir()

    with mock.patch(
            'sky.server.requests.requests.LEGACY_REQUEST_LOG_PATH_PREFIX',
            str(legacy_log_dir)):
        with mock.patch('anyio.Path.unlink', track_unlink):
            with mock.patch(
                    'sky.server.requests.requests.logger') as mock_logger:
                mock_logger.getEffectiveLevel.return_value = logging.INFO
                await requests.clean_finished_requests_with_retention(
                    retention_seconds)

    # Verify that unlink was called for both current and legacy paths
    assert len(unlinked_paths) == 2

    # One path should be under the current log path prefix (from isolated_database)
    # One path should be under the legacy log path prefix
    current_path_count = sum(
        1 for p in unlinked_paths if 'legacy-test-req-1.log' in p)
    assert current_path_count == 2  # Both paths should have the request ID

    # Verify the request was deleted
    assert requests.get_request('legacy-test-req-1') is None


@pytest.mark.asyncio
async def test_cleanup_legacy_directory_if_empty(tmp_path):
    """Test _cleanup_legacy_directory_if_empty removes empty directory."""
    # Create a temporary legacy directory
    legacy_dir = tmp_path / 'legacy_logs'
    legacy_dir.mkdir()

    # Test with empty directory - should be removed
    with mock.patch(
            'sky.server.requests.requests.LEGACY_REQUEST_LOG_PATH_PREFIX',
            str(legacy_dir)):
        await requests._cleanup_legacy_directory_if_empty()

    assert not legacy_dir.exists()


@pytest.mark.asyncio
async def test_cleanup_legacy_directory_not_empty(tmp_path):
    """Test _cleanup_legacy_directory_if_empty preserves non-empty directory."""
    # Create a temporary legacy directory with a file
    legacy_dir = tmp_path / 'legacy_logs'
    legacy_dir.mkdir()
    (legacy_dir / 'some-request.log').touch()

    # Test with non-empty directory - should be preserved
    with mock.patch(
            'sky.server.requests.requests.LEGACY_REQUEST_LOG_PATH_PREFIX',
            str(legacy_dir)):
        await requests._cleanup_legacy_directory_if_empty()

    assert legacy_dir.exists()
    assert (legacy_dir / 'some-request.log').exists()


@pytest.mark.asyncio
async def test_cleanup_legacy_directory_not_exists(tmp_path):
    """Test _cleanup_legacy_directory_if_empty handles non-existent directory."""
    # Point to a non-existent directory
    legacy_dir = tmp_path / 'nonexistent_logs'

    # Should not raise any error
    with mock.patch(
            'sky.server.requests.requests.LEGACY_REQUEST_LOG_PATH_PREFIX',
            str(legacy_dir)):
        await requests._cleanup_legacy_directory_if_empty()

    # Directory should still not exist
    assert not legacy_dir.exists()


@pytest.mark.asyncio
async def test_get_request_with_fields(isolated_database):
    """Test getting a request with specific fields."""
    request = requests.Request(request_id='test-request-with-fields-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=time.time(),
                               user_id='test-user')
    await requests.create_if_not_exists_async(request)
    retrieved_request = requests.get_request('test-request-with-fields-1',
                                             fields=['request_id'])
    assert retrieved_request is not None
    assert retrieved_request.request_id == 'test-request-with-fields-1'
    assert 'test-request-with-fields-1' in str(retrieved_request.log_path)

    retrieved_request = requests.get_request('test-request-with-fields-1',
                                             fields=['name'])
    assert retrieved_request is not None
    assert retrieved_request.name == 'test-request'

    retrieved_request = requests.get_request('test-request-with-fields-1',
                                             fields=['status'])
    assert retrieved_request is not None
    assert retrieved_request.status == RequestStatus.PENDING

    retrieved_request = requests.get_request(
        'test-request-with-fields-1', fields=['request_id', 'name', 'status'])
    assert retrieved_request is not None
    assert retrieved_request.request_id == 'test-request-with-fields-1'
    assert retrieved_request.name == 'test-request'
    assert retrieved_request.status == RequestStatus.PENDING


@pytest.mark.asyncio
async def test_get_request_async(isolated_database):
    """Test getting a request asynchronously."""
    request = requests.Request(request_id='test-request-async-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               schedule_type=ScheduleType.LONG,
                               status_msg='test-status-msg',
                               status=RequestStatus.PENDING,
                               created_at=time.time(),
                               should_retry=True,
                               user_id='test-user')

    # Create the request
    await requests.create_if_not_exists_async(request)

    # Get the request asynchronously
    retrieved_request = await requests.get_request_async('test-request-async-1')

    # Verify the request was retrieved correctly
    assert retrieved_request is not None
    assert retrieved_request.request_id == 'test-request-async-1'
    assert retrieved_request.name == 'test-request'
    assert retrieved_request.status == RequestStatus.PENDING
    assert retrieved_request.user_id == 'test-user'

    retrieved_request = await requests.get_request_async('test-request-async-1',
                                                         fields=['request_id'])
    assert retrieved_request is not None
    assert retrieved_request.request_id == 'test-request-async-1'
    assert 'test-request-async-1' in str(retrieved_request.log_path)

    retrieved_request = await requests.get_request_async('test-request-async-1',
                                                         fields=['name'])
    assert retrieved_request.name == 'test-request'

    retrieved_request = await requests.get_request_async('test-request-async-1',
                                                         fields=['status'])
    assert retrieved_request is not None
    assert retrieved_request.status == RequestStatus.PENDING

    retrieved_request = await requests.get_request_async(
        'test-request-async-1', fields=['name', 'should_retry'])
    assert retrieved_request is not None
    assert retrieved_request.name == 'test-request'
    assert retrieved_request.should_retry is True

    retrieved_request = await requests.get_request_async(
        'test-request-async-1',
        fields=['request_id', 'name', 'schedule_type', 'status', 'status_msg'])
    assert retrieved_request is not None
    assert retrieved_request.request_id == 'test-request-async-1'
    assert retrieved_request.name == 'test-request'
    assert retrieved_request.schedule_type == ScheduleType.LONG
    assert retrieved_request.status == RequestStatus.PENDING
    assert retrieved_request.status_msg == 'test-status-msg'


@pytest.mark.asyncio
async def test_get_request_async_nonexistent(isolated_database):
    """Test getting a non-existent request asynchronously."""
    retrieved_request = await requests.get_request_async('nonexistent-request')
    assert retrieved_request is None


@pytest.mark.asyncio
async def test_create_if_not_exists_async(isolated_database):
    """Test creating a request asynchronously if it doesn't exist."""
    request = requests.Request(request_id='test-request-async-create-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=time.time(),
                               user_id='test-user')

    # Create the request asynchronously
    created = await requests.create_if_not_exists_async(request)

    # Verify the request was created
    assert created is True

    # Verify we can retrieve it
    retrieved_request = await requests.get_request_async(
        'test-request-async-create-1')
    assert retrieved_request is not None
    assert retrieved_request.request_id == 'test-request-async-create-1'
    assert retrieved_request.name == 'test-request'
    assert retrieved_request.status == RequestStatus.PENDING


@pytest.mark.asyncio
async def test_create_if_not_exists_async_already_exists(isolated_database):
    """Test creating a request asynchronously when it already exists."""
    request = requests.Request(request_id='test-request-async-create-2',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=time.time(),
                               user_id='test-user')

    # Create the request first time
    created_first = await requests.create_if_not_exists_async(request)
    assert created_first is True

    # Try to create the same request again
    created_second = await requests.create_if_not_exists_async(request)
    assert created_second is False


@pytest.mark.asyncio
async def test_async_database_operations(isolated_database):
    """Test async database operations work together correctly."""
    # Create a request asynchronously
    request = requests.Request(request_id='test-async-ops-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=time.time(),
                               user_id='test-user')

    # Test create and get operations work together
    created = await requests.create_if_not_exists_async(request)
    assert created is True

    retrieved = await requests.get_request_async('test-async-ops-1')
    assert retrieved is not None
    assert retrieved.request_id == 'test-async-ops-1'
    assert retrieved.status == RequestStatus.PENDING

    # Test that we can create a new request and get both
    request2 = requests.Request(request_id='test-async-ops-2',
                                name='test-request-2',
                                entrypoint=dummy,
                                request_body=payloads.RequestBody(),
                                status=RequestStatus.RUNNING,
                                created_at=time.time(),
                                user_id='test-user-2')

    created2 = await requests.create_if_not_exists_async(request2)
    assert created2 is True

    # Verify both requests exist
    retrieved1 = await requests.get_request_async('test-async-ops-1')
    retrieved2 = await requests.get_request_async('test-async-ops-2')

    assert retrieved1 is not None
    assert retrieved2 is not None
    assert retrieved1.user_id == 'test-user'
    assert retrieved2.user_id == 'test-user-2'


@pytest.mark.asyncio
async def test_get_api_request_ids_start_with(isolated_database):
    """Test request ID completion prioritizes alive requests and orders correctly."""
    current_time = time.time()

    # Create test requests with various statuses and timestamps
    requests_data = [
        # Alive requests (should be prioritized)
        ('pending-new', RequestStatus.PENDING, current_time - 10
        ),  # newest alive
        ('pending-old', RequestStatus.PENDING,
         current_time - 100),  # older alive
        ('running-mid', RequestStatus.RUNNING,
         current_time - 50),  # middle alive

        # Finished requests (should come after alive ones)
        ('succeeded-new', RequestStatus.SUCCEEDED, current_time - 5
        ),  # newest finished
        ('failed-old', RequestStatus.FAILED,
         current_time - 200),  # oldest finished
        ('cancelled-mid', RequestStatus.CANCELLED,
         current_time - 75),  # middle finished

        # Non-matching prefixes (should not appear)
        ('other-request', RequestStatus.RUNNING, current_time - 20),
    ]

    # Create all test requests
    for request_id, status, created_at in requests_data:
        request = requests.Request(
            request_id=request_id,
            name='test-request',
            entrypoint=dummy,
            request_body=payloads.RequestBody(),
            status=status,
            created_at=created_at,
            finished_at=created_at +
            1 if status in RequestStatus.finished_status() else None,
            user_id='test-user')
        await requests.create_if_not_exists_async(request)

    # Test completion with prefix that matches multiple requests
    result = await requests.get_api_request_ids_start_with('pen'
                                                          )  # matches pending-*

    # Should return only pending requests, ordered by newest first
    expected = ['pending-new', 'pending-old']
    assert result == expected

    # Test completion with broader prefix
    result = await requests.get_api_request_ids_start_with(
        '')  # matches all except 'other-request'

    # Should return alive requests first (newest first), then finished (newest first)
    expected_alive = ['pending-new', 'running-mid',
                      'pending-old']  # alive, newest first
    expected_finished = ['succeeded-new', 'cancelled-mid',
                         'failed-old']  # finished, newest first
    expected_all = expected_alive + expected_finished

    # Filter out 'other-request' which doesn't match our tested prefixes
    result_filtered = [r for r in result if not r.startswith('other')]
    assert result_filtered == expected_all

    # Test empty result for non-matching prefix
    result = await requests.get_api_request_ids_start_with('nonexistent')
    assert result == []

    # Test limit functionality by creating many requests
    for i in range(1005):  # More than the 1000 limit
        request = requests.Request(
            request_id=f'bulk-{i:04d}',
            name='test-request',
            entrypoint=dummy,
            request_body=payloads.RequestBody(),
            status=RequestStatus.SUCCEEDED,
            created_at=current_time + i,  # newest first due to ordering
            finished_at=current_time + i + 1,
            user_id='test-user')
        await requests.create_if_not_exists_async(request)

    # Test that limit is respected
    bulk_result = await requests.get_api_request_ids_start_with('bulk-')
    assert len(bulk_result) == 1000  # Should be limited to 1000


@pytest.mark.asyncio
async def test_get_request_async_race_condition(isolated_database):
    """Test that get_request_async is concurrent safe."""

    async def write_then_read(req: requests.Request):
        await requests.create_if_not_exists_async(req)
        retrieved = await requests.get_request_async(req.request_id)
        assert retrieved is not None

    reqs = []
    for i in range(128):
        req = requests.Request(request_id=f'test-request-{i}',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=time.time(),
                               user_id='test-user')
        reqs.append(asyncio.create_task(write_then_read(req)))
    await asyncio.gather(*reqs)


@pytest.mark.asyncio
async def test_get_request_tasks_async(isolated_database):
    """Test async version of get_request_tasks with basic filtering."""
    current_time = time.time()

    # Create test requests
    test_requests = [
        requests.Request(request_id='async-req-1',
                         name='async-test-1',
                         entrypoint=dummy,
                         request_body=payloads.RequestBody(),
                         status=RequestStatus.PENDING,
                         created_at=current_time - 30,
                         user_id='async-user1',
                         cluster_name='async-cluster1'),
        requests.Request(request_id='async-req-2',
                         name='async-test-2',
                         entrypoint=dummy,
                         request_body=payloads.RequestBody(),
                         status=RequestStatus.RUNNING,
                         created_at=current_time - 20,
                         user_id='async-user1',
                         cluster_name='async-cluster2'),
        requests.Request(request_id='async-req-3',
                         name='async-test-3',
                         entrypoint=dummy,
                         request_body=payloads.RequestBody(),
                         status=RequestStatus.SUCCEEDED,
                         created_at=current_time - 10,
                         finished_at=current_time - 5,
                         user_id='async-user2',
                         cluster_name='async-cluster1'),
    ]

    # Create all requests synchronously for setup
    for req in test_requests:
        await requests.create_if_not_exists_async(req)

    # Test 1: Get all requests (no filter) - async
    all_requests = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(sort=True))
    assert len(all_requests) == 3
    # Should be ordered by created_at DESC
    assert all_requests[0].request_id == 'async-req-3'  # newest
    assert all_requests[-1].request_id == 'async-req-1'  # oldest

    # Test 2: Filter by status - async
    pending_requests = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(status=[RequestStatus.PENDING], sort=True))
    assert len(pending_requests) == 1
    assert pending_requests[0].request_id == 'async-req-1'

    # Test 3: Filter by user_id - async
    user1_requests = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(user_id='async-user1', sort=True))
    assert len(user1_requests) == 2
    assert all(req.user_id == 'async-user1' for req in user1_requests)

    # Test 4: Filter by cluster_names - async
    cluster1_requests = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(cluster_names=['async-cluster1'], sort=True))
    assert len(cluster1_requests) == 2
    assert all(
        req.cluster_name == 'async-cluster1' for req in cluster1_requests)

    # Test 5: Complex combined filtering - async
    combined_requests = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(
            status=[RequestStatus.PENDING, RequestStatus.RUNNING],
            user_id='async-user1',
            sort=True))
    assert len(combined_requests) == 2
    assert all(req.user_id == 'async-user1' and
               req.status in [RequestStatus.PENDING, RequestStatus.RUNNING]
               for req in combined_requests)


@pytest.mark.asyncio
async def test_get_request_tasks_async_empty_results(isolated_database):
    """Test async version with filters that return no results."""
    # Test with empty database
    empty_results = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(sort=True))
    assert len(empty_results) == 0

    # Create one test request
    test_request = requests.Request(request_id='async-test-req',
                                    name='async-test-name',
                                    entrypoint=dummy,
                                    request_body=payloads.RequestBody(),
                                    status=RequestStatus.PENDING,
                                    created_at=time.time(),
                                    user_id='async-test-user')
    await requests.create_if_not_exists_async(test_request)

    # Test filtering that returns no results
    empty_results = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(status=[RequestStatus.CANCELLED], sort=True))
    assert len(empty_results) == 0

    # Test filtering with non-existent user
    empty_results = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(user_id='nonexistent-async-user', sort=True))
    assert len(empty_results) == 0


@pytest.mark.asyncio
async def test_get_request_tasks_async_consistency(isolated_database):
    """Test that async and sync versions return consistent results."""
    current_time = time.time()

    # Create test requests
    test_requests = [
        requests.Request(request_id='consistency-req-1',
                         name='consistency-test-1',
                         entrypoint=dummy,
                         request_body=payloads.RequestBody(),
                         status=RequestStatus.PENDING,
                         created_at=current_time - 30,
                         user_id='consistency-user',
                         cluster_name='consistency-cluster'),
        requests.Request(request_id='consistency-req-2',
                         name='consistency-test-2',
                         entrypoint=dummy,
                         request_body=payloads.RequestBody(),
                         status=RequestStatus.RUNNING,
                         created_at=current_time - 20,
                         user_id='consistency-user',
                         cluster_name='consistency-cluster'),
    ]

    # Create all requests
    for req in test_requests:
        await requests.create_if_not_exists_async(req)

    # Test that sync and async versions return identical results
    filter_obj = requests.RequestTaskFilter(user_id='consistency-user',
                                            sort=True)

    sync_results = requests.get_request_tasks(filter_obj)
    async_results = await requests.get_request_tasks_async(filter_obj)

    assert len(sync_results) == len(async_results)
    assert len(sync_results) == 2

    # Compare each request (order should be the same - created_at DESC)
    for sync_req, async_req in zip(sync_results, async_results):
        assert sync_req.request_id == async_req.request_id
        assert sync_req.name == async_req.name
        assert sync_req.status == async_req.status
        assert sync_req.user_id == async_req.user_id
        assert sync_req.cluster_name == async_req.cluster_name
        assert sync_req.created_at == async_req.created_at


@pytest.mark.asyncio
async def test_get_request_tasks_concurrent_access(isolated_database):
    """Test concurrent access to get_request_tasks_async."""
    current_time = time.time()

    # Create multiple test requests
    test_requests = []
    for i in range(10):
        req = requests.Request(
            request_id=f'concurrent-req-{i}',
            name=f'concurrent-test-{i}',
            entrypoint=dummy,
            request_body=payloads.RequestBody(),
            status=RequestStatus.PENDING if i %
            2 == 0 else RequestStatus.RUNNING,
            created_at=current_time - i,
            user_id=f'user-{i % 3}',  # 3 different users
            cluster_name=f'cluster-{i % 2}'  # 2 different clusters
        )
        test_requests.append(req)
        await requests.create_if_not_exists_async(req)

    # Define concurrent query functions
    async def query_all():
        return await requests.get_request_tasks_async(
            requests.RequestTaskFilter(sort=True))

    async def query_by_status():
        return await requests.get_request_tasks_async(
            requests.RequestTaskFilter(status=[RequestStatus.PENDING],
                                       sort=True))

    async def query_by_user(user_id):
        return await requests.get_request_tasks_async(
            requests.RequestTaskFilter(user_id=user_id, sort=True))

    async def query_by_cluster(cluster_name):
        return await requests.get_request_tasks_async(
            requests.RequestTaskFilter(cluster_names=[cluster_name], sort=True))

    # Run multiple queries concurrently
    results = await asyncio.gather(query_all(),
                                   query_by_status(),
                                   query_by_user('user-0'),
                                   query_by_user('user-1'),
                                   query_by_user('user-2'),
                                   query_by_cluster('cluster-0'),
                                   query_by_cluster('cluster-1'),
                                   return_exceptions=True)

    # Verify all queries completed successfully
    assert all(not isinstance(result, Exception) for result in results)

    all_results, pending_results, user0_results, user1_results, user2_results, cluster0_results, cluster1_results = results

    # Verify result counts
    assert len(all_results) == 10
    assert len(pending_results) == 5  # Even numbered requests are PENDING
    assert len(user0_results) >= 3  # Users 0, 3, 6, 9
    assert len(user1_results) >= 3  # Users 1, 4, 7
    assert len(user2_results) >= 3  # Users 2, 5, 8
    assert len(cluster0_results) == 5  # Even numbered clusters
    assert len(cluster1_results) == 5  # Odd numbered clusters

    # Verify ordering (should be by created_at DESC)
    for result_set in [
            all_results, pending_results, user0_results, user1_results,
            user2_results, cluster0_results, cluster1_results
    ]:
        if len(result_set) > 1:
            for i in range(len(result_set) - 1):
                assert result_set[i].created_at >= result_set[i + 1].created_at


def test_requests_filter():
    """Test RequestTaskFilter.build_query() generates correct SQL."""

    # Test empty filter - should return base query with no WHERE clause
    filter_empty = requests.RequestTaskFilter(sort=True)
    sql, params = filter_empty.build_query()
    expected_columns = ', '.join(requests.REQUEST_COLUMNS)
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []

    # Test status filter
    filter_status = requests.RequestTaskFilter(
        status=[RequestStatus.PENDING, RequestStatus.RUNNING], sort=True)
    sql, params = filter_status.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE status IN (\'PENDING\',\'RUNNING\') '
                    'ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []

    # Test cluster_names filter
    filter_clusters = requests.RequestTaskFilter(
        cluster_names=['cluster1', 'cluster2'], sort=True)
    sql, params = filter_clusters.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE cluster_name IN (\'cluster1\',\'cluster2\') '
                    'ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []

    # Test user_id filter (uses parameterized query)
    filter_user = requests.RequestTaskFilter(user_id='test-user-123', sort=True)
    sql, params = filter_user.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE user_id = ? ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == ['test-user-123']

    # Test exclude_request_names filter
    filter_exclude = requests.RequestTaskFilter(
        exclude_request_names=['request1', 'request2'], sort=True)
    sql, params = filter_exclude.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE name NOT IN (\'request1\',\'request2\') '
                    'ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []

    # Test include_request_names filter
    filter_include = requests.RequestTaskFilter(
        include_request_names=['request3', 'request4'], sort=True)
    sql, params = filter_include.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE name IN (\'request3\',\'request4\') '
                    'ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []

    # Test finished_before filter (uses parameterized query)
    timestamp = 1234567890.0
    filter_finished = requests.RequestTaskFilter(finished_before=timestamp,
                                                 sort=True)
    sql, params = filter_finished.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE finished_at < ? ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == [timestamp]

    # Test combined filters
    filter_combined = requests.RequestTaskFilter(
        status=[RequestStatus.SUCCEEDED, RequestStatus.FAILED],
        cluster_names=['prod-cluster'],
        user_id='admin-user',
        exclude_request_names=['internal-task'],
        finished_before=9876543210.0,
        sort=True)
    sql, params = filter_combined.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE status IN (\'SUCCEEDED\',\'FAILED\') AND '
                    'name NOT IN (\'internal-task\') AND '
                    'cluster_name IN (\'prod-cluster\') AND '
                    'user_id = ? AND finished_at < ? '
                    'ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == ['admin-user', 9876543210.0]

    # Test mutually exclusive filters raise ValueError
    with pytest.raises(ValueError, match='Only one of exclude_request_names'):
        requests.RequestTaskFilter(exclude_request_names=['req1'],
                                   include_request_names=['req2'],
                                   sort=True)

    # Test special characters in names are properly escaped with repr()
    filter_special_chars = requests.RequestTaskFilter(
        cluster_names=['cluster\'with\'quotes', 'cluster\"with\"double'],
        sort=True)
    sql, params = filter_special_chars.build_query()
    # repr() should properly escape the quotes
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE cluster_name IN (\"cluster\'with\'quotes\",'
                    '\'cluster\"with\"double\') ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []


def test_encode_requests_empty_list():
    """Test encoding an empty list of requests."""
    result = requests.encode_requests([])
    assert result == []


def test_encode_requests_single_request():
    """Test encoding a single request."""
    from sky import models

    current_time = time.time()
    request = requests.Request(request_id='test-req-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=current_time,
                               user_id='user-123')

    # Mock global_user_state.get_all_users()
    mock_user = models.User(id='user-123', name='Test User')
    with mock.patch('sky.global_user_state.get_all_users',
                    return_value=[mock_user]):
        result = requests.encode_requests([request])

    assert len(result) == 1
    payload = result[0]
    assert payload.request_id == 'test-req-1'
    assert payload.name == 'test-request'
    assert payload.entrypoint == 'dummy'
    assert payload.status == 'PENDING'
    assert payload.created_at == current_time
    assert payload.user_id == 'user-123'
    assert payload.user_name == 'Test User'
    assert payload.pid is None
    assert payload.return_value == 'null'
    assert payload.error == 'null'


def test_encode_requests_multiple_requests():
    """Test encoding multiple requests."""
    from sky import models

    current_time = time.time()
    test_requests = [
        requests.Request(request_id='req-1',
                         name='request-1',
                         entrypoint=dummy,
                         request_body=payloads.RequestBody(),
                         status=RequestStatus.RUNNING,
                         created_at=current_time,
                         user_id='user-1',
                         cluster_name='cluster-1'),
        requests.Request(request_id='req-2',
                         name='request-2',
                         entrypoint=dummy,
                         request_body=payloads.RequestBody(),
                         status=RequestStatus.SUCCEEDED,
                         created_at=current_time - 10,
                         finished_at=current_time - 5,
                         user_id='user-2',
                         cluster_name='cluster-2'),
        requests.Request(request_id='req-3',
                         name='request-3',
                         entrypoint=dummy,
                         request_body=payloads.RequestBody(),
                         status=RequestStatus.FAILED,
                         created_at=current_time - 20,
                         finished_at=current_time - 15,
                         user_id='user-1')
    ]

    # Mock global_user_state.get_all_users()
    mock_users = [
        models.User(id='user-1', name='User One'),
        models.User(id='user-2', name='User Two')
    ]
    with mock.patch('sky.global_user_state.get_all_users',
                    return_value=mock_users):
        result = requests.encode_requests(test_requests)

    assert len(result) == 3
    # Check first request
    assert result[0].request_id == 'req-1'
    assert result[0].user_name == 'User One'
    assert result[0].cluster_name == 'cluster-1'
    # Check second request
    assert result[1].request_id == 'req-2'
    assert result[1].user_name == 'User Two'
    assert result[1].cluster_name == 'cluster-2'
    assert result[1].finished_at == current_time - 5
    # Check third request
    assert result[2].request_id == 'req-3'
    assert result[2].user_name == 'User One'
    assert result[2].cluster_name is None


def test_encode_requests_user_not_found():
    """Test encoding requests when user is not in the database."""
    current_time = time.time()
    request = requests.Request(request_id='test-req',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=current_time,
                               user_id='nonexistent-user')

    # Mock get_all_users() to return empty list
    with mock.patch('sky.global_user_state.get_all_users', return_value=[]):
        result = requests.encode_requests([request])

    assert len(result) == 1
    payload = result[0]
    assert payload.user_id == 'nonexistent-user'
    assert payload.user_name is None  # User not found


def test_encode_requests_with_none_request_body():
    """Test encoding requests with None request_body."""
    from sky import models

    current_time = time.time()
    request = requests.Request(request_id='test-req',
                               name='test-request',
                               entrypoint=None,
                               request_body=None,
                               status=RequestStatus.PENDING,
                               created_at=current_time,
                               user_id='user-123')

    mock_user = models.User(id='user-123', name='Test User')
    with mock.patch('sky.global_user_state.get_all_users',
                    return_value=[mock_user]):
        result = requests.encode_requests([request])

    assert len(result) == 1
    payload = result[0]
    assert payload.entrypoint == ''
    assert payload.request_body == 'null'


def test_encode_requests_various_statuses():
    """Test encoding requests with various status values."""
    from sky import models

    current_time = time.time()
    statuses = [
        RequestStatus.PENDING, RequestStatus.RUNNING, RequestStatus.SUCCEEDED,
        RequestStatus.FAILED, RequestStatus.CANCELLED
    ]

    test_requests = []
    for i, status in enumerate(statuses):
        req = requests.Request(request_id=f'req-{i}',
                               name=f'request-{i}',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=status,
                               created_at=current_time - i,
                               user_id='user-1')
        test_requests.append(req)

    mock_user = models.User(id='user-1', name='Test User')
    with mock.patch('sky.global_user_state.get_all_users',
                    return_value=[mock_user]):
        result = requests.encode_requests(test_requests)

    assert len(result) == len(statuses)
    for i, status in enumerate(statuses):
        assert result[i].status == status.value


def test_update_request_row_fields_none_fields():
    """Test _update_request_row_fields with None fields returns row as-is."""
    original_row = ('req-1', 'test', 'entry', 'body', 'PENDING', 'ret', 'err',
                    123, 100.0, 'cluster', 'long', 'user-1', 'msg', 1, 200.0)
    result = requests._update_request_row_fields(original_row, None)
    assert result == original_row


def test_update_request_row_fields_partial_fields():
    """Test _update_request_row_fields with partial fields provided."""
    empty_pickled_value = 'gAROLg=='

    # Only provide request_id and name
    row = ('my-request', 'my-name')
    fields = ['request_id', 'name']
    result = requests._update_request_row_fields(row, fields)

    assert len(result) == len(requests.REQUEST_COLUMNS)
    # Provided fields
    assert result[0] == 'my-request'  # request_id
    assert result[1] == 'my-name'  # name
    # Missing required fields should be filled with defaults
    assert result[2] == empty_pickled_value  # entrypoint
    assert result[3] == empty_pickled_value  # request_body
    assert result[4] == RequestStatus.PENDING.value  # status
    assert result[8] == 0  # created_at
    assert result[11] == ''  # user_id


def test_update_request_row_fields_partial_fields_entrypoint():
    """Test _update_request_row_fields with partial fields provided."""
    empty_pickled_value = 'gAROLg=='

    # Only provide request_id and name
    row = ('my-entrypoint',)
    fields = ['entrypoint']
    result = requests._update_request_row_fields(row, fields)

    assert len(result) == len(requests.REQUEST_COLUMNS)
    # Provided fields
    assert result[0] == ''  # request_id
    assert result[1] == ''  # name
    # Missing required fields should be filled with defaults
    assert result[2] == 'my-entrypoint'  # entrypoint
    assert result[3] == empty_pickled_value  # request_body
    assert result[4] == RequestStatus.PENDING.value  # status
    assert result[8] == 0  # created_at
    assert result[11] == ''  # user_id


def test_update_request_row_fields_all_required_fields():
    """Test _update_request_row_fields with all required fields."""
    empty_pickled_value = 'gAROLg=='

    row = ('req-1', 'test-name', 'entrypoint-data', 'body-data', 'RUNNING',
           'null', 'null', 'short', 'user-123', 100.0)
    fields = [
        'request_id', 'name', 'entrypoint', 'request_body', 'status',
        'return_value', 'error', 'schedule_type', 'user_id', 'created_at'
    ]
    result = requests._update_request_row_fields(row, fields)

    assert len(result) == len(requests.REQUEST_COLUMNS)
    # Check provided fields maintain their values
    assert result[0] == 'req-1'
    assert result[1] == 'test-name'
    assert result[2] == 'entrypoint-data'
    assert result[3] == 'body-data'
    assert result[4] == 'RUNNING'
    assert result[11] == 'user-123'
    assert result[8] == 100.0
    # Check optional fields are filled with defaults
    assert result[7] is None  # pid
    assert result[9] is None  # cluster_name
    assert result[12] is None  # status_msg
    assert result[13] is False  # should_retry
    assert result[14] is None  # finished_at


def test_update_request_row_fields_with_optional_fields():
    """Test _update_request_row_fields includes optional fields."""
    row = ('req-1', 'test', 'entry', 'body', 'SUCCEEDED', 'null', 'null', 12345,
           100.0, 'my-cluster', 'long', 'user-1', 'All done', 1, 200.0)
    fields = [
        'request_id', 'name', 'entrypoint', 'request_body', 'status',
        'return_value', 'error', 'pid', 'created_at', 'cluster_name',
        'schedule_type', 'user_id', 'status_msg', 'should_retry', 'finished_at'
    ]
    result = requests._update_request_row_fields(row, fields)

    assert len(result) == len(requests.REQUEST_COLUMNS)
    # Verify all fields are present
    assert result[0] == 'req-1'
    assert result[7] == 12345  # pid
    assert result[9] == 'my-cluster'  # cluster_name
    assert result[12] == 'All done'  # status_msg
    assert result[13] == 1  # should_retry
    assert result[14] == 200.0  # finished_at


def test_update_request_row_fields_maintains_order():
    """Test _update_request_row_fields returns fields in REQUEST_COLUMNS order."""
    # Provide fields in non-standard order
    row = ('user-1', 'test-name', 'req-1')
    fields = ['user_id', 'name', 'request_id']
    result = requests._update_request_row_fields(row, fields)

    # Result should be in REQUEST_COLUMNS order
    assert len(result) == len(requests.REQUEST_COLUMNS)
    # REQUEST_COLUMNS order: request_id, name, ..., user_id
    assert result[0] == 'req-1'  # request_id (first in REQUEST_COLUMNS)
    assert result[1] == 'test-name'  # name (second in REQUEST_COLUMNS)
    assert result[11] == 'user-1'  # user_id (later in REQUEST_COLUMNS)


@pytest.mark.asyncio
async def test_cancel_get_request_async():
    import gc

    async def mock_get_request_async_no_lock(id: str,
                                             fields: Optional[List[str]] = None
                                            ):
        await asyncio.sleep(1)
        return None

    concurrency = 1000

    with mock.patch('sky.server.requests.requests._get_request_no_lock_async',
                    side_effect=mock_get_request_async_no_lock):
        tasks = []
        for i in range(concurrency):
            task = asyncio.create_task(
                requests.get_request_async(f'test-request-id-{i}'))
            tasks.append(task)
        await asyncio.sleep(0.2)
        for task in tasks:
            task.cancel()
            # This is critical to proactively calls GC to ensure GC will not
            # affect the lock release, refer to
            # https://github.com/skypilot-org/skypilot/issues/7663
            # for more details.
            gc.collect()
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            # Expected when tasks are cancelled
            pass
        # Since get_request_async is shielded, task.cancel() will neither cancel or
        # wait the get_request_async coroutine. So we have to wait for a enough time
        # to ensure all the coroutines are done.
        # TODO(aylei): this may have timing issue, but looks good for now.
        await asyncio.sleep(10)
        for i in range(concurrency):
            lock = filelock.FileLock(
                requests.request_lock_path(f'test-request-id-{i}'))
            # The locks must be released properly
            lock.acquire(blocking=False)


@pytest.mark.asyncio
async def test_get_latest_request_id_async(isolated_database):
    """Test getting the latest request ID asynchronously."""
    current_time = time.time()

    request_id = await requests.get_latest_request_id_async()
    assert request_id is None

    request = requests.Request(request_id='test-request-id-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=current_time,
                               user_id='test-user')
    await requests.create_if_not_exists_async(request)
    request_id = await requests.get_latest_request_id_async()
    assert request_id == 'test-request-id-1'

    request = requests.Request(request_id='test-request-id-2',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=current_time + 1,
                               user_id='test-user')
    await requests.create_if_not_exists_async(request)
    request_id = await requests.get_latest_request_id_async()
    assert request_id == 'test-request-id-2'


@pytest.mark.asyncio
@pytest.mark.parametrize('test_async', [True, False])
async def test_get_requests_with_prefix(isolated_database, test_async):
    """Test get_requests_with_prefix."""
    current_time = time.time()

    # Create multiple matching requests
    matching_requests = []
    for i in range(3):
        request = requests.Request(request_id=f'batch-request-{i:03d}',
                                   name=f'test-request-{i}',
                                   entrypoint=dummy,
                                   request_body=payloads.RequestBody(),
                                   status=RequestStatus.PENDING if i %
                                   2 == 0 else RequestStatus.RUNNING,
                                   created_at=current_time + i,
                                   user_id=f'test-user-{i}',
                                   cluster_name=f'cluster-{i}')
        matching_requests.append(request)
        await requests.create_if_not_exists_async(request)

    # Create another request
    non_matching_request = requests.Request(request_id='other-request-1',
                                            name='other-request',
                                            entrypoint=dummy,
                                            request_body=payloads.RequestBody(),
                                            status=RequestStatus.SUCCEEDED,
                                            created_at=current_time + 100,
                                            user_id='other-user')
    await requests.create_if_not_exists_async(non_matching_request)

    # Test with non-matching prefix
    if test_async:
        result = await requests.get_requests_async_with_prefix(
            'nonexistent-prefix')
    else:
        result = requests.get_requests_with_prefix('nonexistent-prefix')
    assert result is None

    # Test with prefix that matches exactly one request
    if test_async:
        result = await requests.get_requests_async_with_prefix(
            'batch-request-000')
    else:
        result = requests.get_requests_with_prefix('batch-request-000')
    assert result is not None
    assert len(result) == 1
    assert result[0].request_id == 'batch-request-000'
    assert result[0].name == 'test-request-0'
    assert result[0].user_id == 'test-user-0'
    assert result[0].cluster_name == 'cluster-0'
    assert result[0].status == RequestStatus.PENDING

    # Test with prefix that matches multiple requests
    if test_async:
        result = await requests.get_requests_async_with_prefix('batch-request')
    else:
        result = requests.get_requests_with_prefix('batch-request')
    assert result is not None
    assert len(result) == 3

    # Verify all returned requests match the prefix
    returned_ids = [req.request_id for req in result]
    expected_ids = [
        'batch-request-000', 'batch-request-001', 'batch-request-002'
    ]
    assert set(returned_ids) == set(expected_ids)

    # Verify request details
    for req in result:
        assert req.request_id.startswith('batch-request')
        assert req.name.startswith('test-request')
        assert req.user_id.startswith('test-user')

    # Test with empty prefix (should match all requests)
    if test_async:
        result = await requests.get_requests_async_with_prefix('')
    else:
        result = requests.get_requests_with_prefix('')
    assert result is not None
    assert len(result) == 4
    returned_ids = [req.request_id for req in result]
    expected_ids = [
        'batch-request-000', 'batch-request-001', 'batch-request-002',
        'other-request-1'
    ]
    assert set(returned_ids) == set(expected_ids)

    # Test with specific fields - only request_id and name
    if test_async:
        result = await requests.get_requests_async_with_prefix(
            'batch-request', fields=['request_id', 'name'])
    else:
        result = requests.get_requests_with_prefix(
            'batch-request', fields=['request_id', 'name'])
    assert result is not None
    assert len(result) == 3

    # Verify that only the requested fields are meaningful
    for req in result:
        assert req.request_id in [
            'batch-request-000', 'batch-request-001', 'batch-request-002'
        ]
        if req.request_id == 'batch-request-000':
            assert req.name == 'test-request-0'
        elif req.request_id == 'batch-request-001':
            assert req.name == 'test-request-1'
        else:
            assert req.name == 'test-request-2'
        assert req.pid is None
        assert req.finished_at is None
        assert req.should_retry is False
        assert req.status_msg is None
