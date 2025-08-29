"""Unit tests for sky.server.requests.requests module."""
import asyncio
import pathlib
import time
import unittest.mock as mock

import pytest

from sky.server.requests import payloads
from sky.server.requests import requests
from sky.server.requests.requests import RequestStatus


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


def test_set_request_failed(isolated_database):
    request = requests.Request(request_id='test-request-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.RUNNING,
                               created_at=0.0,
                               finished_at=0.0,
                               user_id='test-user')

    requests.create_if_not_exists(request)
    try:
        raise ValueError('Boom!')
    except ValueError as e:
        requests.set_request_failed('test-request-1', e)

    # Get the updated request
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


def test_clean_finished_requests_with_retention(isolated_database):
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
    requests.create_if_not_exists(old_finished_request)
    requests.create_if_not_exists(recent_finished_request)
    requests.create_if_not_exists(old_running_request)

    # Mock log file unlinking
    with mock.patch.object(pathlib.Path, 'unlink') as mock_unlink:
        with mock.patch('sky.server.requests.requests.logger') as mock_logger:
            requests.clean_finished_requests_with_retention(retention_seconds)

    # Verify old finished request was deleted
    assert requests.get_request('old-finished-1') is None

    # Verify recent finished request was NOT deleted
    assert requests.get_request('recent-finished-1') is not None

    # Verify old running request was NOT deleted
    assert requests.get_request('old-running-1') is not None

    # Verify log file unlink was called for the deleted request
    mock_unlink.assert_called_once()

    # Verify logging
    mock_logger.info.assert_called_once()
    log_message = mock_logger.info.call_args[0][0]
    assert 'Cleaned up 1 finished requests' in log_message


def test_clean_finished_requests_with_retention_no_old_requests(
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

    requests.create_if_not_exists(recent_request)

    with mock.patch('sky.server.requests.requests.logger') as mock_logger:
        requests.clean_finished_requests_with_retention(retention_seconds)

    # Verify request was NOT deleted
    assert requests.get_request('recent-test-1') is not None

    # Verify logging shows 0 cleaned requests
    mock_logger.info.assert_called_once()
    log_message = mock_logger.info.call_args[0][0]
    assert 'Cleaned up 0 finished requests' in log_message


def test_clean_finished_requests_with_retention_all_statuses(isolated_database):
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

    requests.create_if_not_exists(succeeded_request)
    requests.create_if_not_exists(failed_request)
    requests.create_if_not_exists(cancelled_request)

    with mock.patch.object(pathlib.Path, 'unlink'):
        with mock.patch('sky.server.requests.requests.logger') as mock_logger:
            requests.clean_finished_requests_with_retention(retention_seconds)

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
    """Test the garbage collection daemon runs correctly."""
    with mock.patch(
            'sky.server.requests.requests.skypilot_config') as mock_config:
        with mock.patch(
                'sky.server.requests.requests.clean_finished_requests_with_retention'
        ) as mock_clean:
            with mock.patch('asyncio.sleep') as mock_sleep:
                # Configure retention seconds
                mock_config.get_nested.return_value = 120  # 2 minutes

                # Make sleep raise CancelledError after first iteration
                # to exit loop
                mock_sleep.side_effect = [None, asyncio.CancelledError()]

                # Run the daemon
                with pytest.raises(asyncio.CancelledError):
                    await requests.requests_gc_daemon()

                # Verify cleanup was called
                mock_clean.assert_called_with(120 * 3600)

                # Verify sleep was called with max(retention, 3600)
                assert mock_sleep.call_count == 2
                mock_sleep.assert_any_call(120 * 3600)


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


@pytest.mark.asyncio
async def test_get_request_async(isolated_database):
    """Test getting a request asynchronously."""
    request = requests.Request(request_id='test-request-async-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=time.time(),
                               user_id='test-user')

    # Create the request
    requests.create_if_not_exists(request)

    # Get the request asynchronously
    retrieved_request = await requests.get_request_async('test-request-async-1')

    # Verify the request was retrieved correctly
    assert retrieved_request is not None
    assert retrieved_request.request_id == 'test-request-async-1'
    assert retrieved_request.name == 'test-request'
    assert retrieved_request.status == RequestStatus.PENDING
    assert retrieved_request.user_id == 'test-user'


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
        requests.create_if_not_exists(request)

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
        requests.create_if_not_exists(request)

    # Test that limit is respected
    bulk_result = await requests.get_api_request_ids_start_with('bulk-')
    assert len(bulk_result) == 1000  # Should be limited to 1000
