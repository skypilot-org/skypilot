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
    """Test async version of get_request."""
    request = requests.Request(request_id='test-async-request-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=time.time(),
                               user_id='test-user')

    # Create the request first
    requests.create_if_not_exists(request)

    # Test retrieving the request asynchronously
    retrieved_request = await requests.get_request_async('test-async-request-1')

    assert retrieved_request is not None
    assert retrieved_request.request_id == 'test-async-request-1'
    assert retrieved_request.name == 'test-request'
    assert retrieved_request.status == RequestStatus.PENDING
    assert retrieved_request.user_id == 'test-user'


@pytest.mark.asyncio
async def test_get_request_async_nonexistent(isolated_database):
    """Test get_request_async with non-existent request."""
    result = await requests.get_request_async('nonexistent-request')
    assert result is None


@pytest.mark.asyncio
async def test_create_if_not_exists_async(isolated_database):
    """Test async version of create_if_not_exists."""
    request = requests.Request(request_id='test-async-create-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=time.time(),
                               user_id='test-user')

    # Test creating a new request
    created = await requests.create_if_not_exists_async(request)
    assert created is True

    # Verify the request was created
    retrieved_request = await requests.get_request_async('test-async-create-1')
    assert retrieved_request is not None
    assert retrieved_request.request_id == 'test-async-create-1'

    # Test creating the same request again (should return False)
    created_again = await requests.create_if_not_exists_async(request)
    assert created_again is False


@pytest.mark.asyncio
async def test_update_request_async(isolated_database):
    """Test async version of update_request."""
    request = requests.Request(request_id='test-async-update-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.PENDING,
                               created_at=time.time(),
                               user_id='test-user')

    # Create the request first
    await requests.create_if_not_exists_async(request)

    # Test updating the request asynchronously
    async with requests.update_request_async('test-async-update-1') as req:
        assert req is not None
        assert req.status == RequestStatus.PENDING
        # Update the status
        req.status = RequestStatus.RUNNING
        req.pid = 12345

    # Verify the request was updated
    updated_request = await requests.get_request_async('test-async-update-1')
    assert updated_request is not None
    assert updated_request.status == RequestStatus.RUNNING
    assert updated_request.pid == 12345


@pytest.mark.asyncio
async def test_update_request_async_nonexistent(isolated_database):
    """Test update_request_async with non-existent request."""
    async with requests.update_request_async('nonexistent-request') as req:
        assert req is None
