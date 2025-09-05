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
    requests.create_if_not_exists(old_finished_request)
    requests.create_if_not_exists(recent_finished_request)
    requests.create_if_not_exists(old_running_request)

    # Mock log file unlinking
    with mock.patch.object(pathlib.Path, 'unlink') as mock_unlink:
        with mock.patch('sky.server.requests.requests.logger') as mock_logger:
            await requests.clean_finished_requests_with_retention(
                retention_seconds)

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

    requests.create_if_not_exists(recent_request)

    with mock.patch('sky.server.requests.requests.logger') as mock_logger:
        await requests.clean_finished_requests_with_retention(retention_seconds)

    # Verify request was NOT deleted
    assert requests.get_request('recent-test-1') is not None

    # Verify logging shows 0 cleaned requests
    mock_logger.info.assert_called_once()
    log_message = mock_logger.info.call_args[0][0]
    assert 'Cleaned up 0 finished requests' in log_message


@pytest.mark.asyncio
async def test_clean_finished_requests_with_retention_all_statuses(isolated_database):
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
            await requests.clean_finished_requests_with_retention(retention_seconds)

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
    requests.create_if_not_exists(old_finished_request)
    requests.create_if_not_exists(recent_finished_request)
    requests.create_if_not_exists(running_request)

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


@pytest.mark.asyncio
async def test_get_request_async_race_condition(isolated_database):
    """Test that get_request_async is concurrent safe."""

    async def write_then_read(req: requests.Request):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, requests.create_if_not_exists, req)
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
        requests.create_if_not_exists(req)

    # Test 1: Get all requests (no filter) - async
    all_requests = await requests.get_request_tasks_async(
        requests.RequestTaskFilter())
    assert len(all_requests) == 3
    # Should be ordered by created_at DESC
    assert all_requests[0].request_id == 'async-req-3'  # newest
    assert all_requests[-1].request_id == 'async-req-1'  # oldest

    # Test 2: Filter by status - async
    pending_requests = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(status=[RequestStatus.PENDING]))
    assert len(pending_requests) == 1
    assert pending_requests[0].request_id == 'async-req-1'

    # Test 3: Filter by user_id - async
    user1_requests = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(user_id='async-user1'))
    assert len(user1_requests) == 2
    assert all(req.user_id == 'async-user1' for req in user1_requests)

    # Test 4: Filter by cluster_names - async
    cluster1_requests = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(cluster_names=['async-cluster1']))
    assert len(cluster1_requests) == 2
    assert all(
        req.cluster_name == 'async-cluster1' for req in cluster1_requests)

    # Test 5: Complex combined filtering - async
    combined_requests = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(
            status=[RequestStatus.PENDING, RequestStatus.RUNNING],
            user_id='async-user1'))
    assert len(combined_requests) == 2
    assert all(req.user_id == 'async-user1' and
               req.status in [RequestStatus.PENDING, RequestStatus.RUNNING]
               for req in combined_requests)


@pytest.mark.asyncio
async def test_get_request_tasks_async_empty_results(isolated_database):
    """Test async version with filters that return no results."""
    # Test with empty database
    empty_results = await requests.get_request_tasks_async(
        requests.RequestTaskFilter())
    assert len(empty_results) == 0

    # Create one test request
    test_request = requests.Request(request_id='async-test-req',
                                    name='async-test-name',
                                    entrypoint=dummy,
                                    request_body=payloads.RequestBody(),
                                    status=RequestStatus.PENDING,
                                    created_at=time.time(),
                                    user_id='async-test-user')
    requests.create_if_not_exists(test_request)

    # Test filtering that returns no results
    empty_results = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(status=[RequestStatus.CANCELLED]))
    assert len(empty_results) == 0

    # Test filtering with non-existent user
    empty_results = await requests.get_request_tasks_async(
        requests.RequestTaskFilter(user_id='nonexistent-async-user'))
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
        requests.create_if_not_exists(req)

    # Test that sync and async versions return identical results
    filter_obj = requests.RequestTaskFilter(user_id='consistency-user')

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
        requests.create_if_not_exists(req)

    # Define concurrent query functions
    async def query_all():
        return await requests.get_request_tasks_async(
            requests.RequestTaskFilter())

    async def query_by_status():
        return await requests.get_request_tasks_async(
            requests.RequestTaskFilter(status=[RequestStatus.PENDING]))

    async def query_by_user(user_id):
        return await requests.get_request_tasks_async(
            requests.RequestTaskFilter(user_id=user_id))

    async def query_by_cluster(cluster_name):
        return await requests.get_request_tasks_async(
            requests.RequestTaskFilter(cluster_names=[cluster_name]))

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
    filter_empty = requests.RequestTaskFilter()
    sql, params = filter_empty.build_query()
    expected_columns = ', '.join(requests.REQUEST_COLUMNS)
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []

    # Test status filter
    filter_status = requests.RequestTaskFilter(
        status=[RequestStatus.PENDING, RequestStatus.RUNNING])
    sql, params = filter_status.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE status IN (\'PENDING\',\'RUNNING\') '
                    'ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []

    # Test cluster_names filter
    filter_clusters = requests.RequestTaskFilter(
        cluster_names=['cluster1', 'cluster2'])
    sql, params = filter_clusters.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE cluster_name IN (\'cluster1\',\'cluster2\') '
                    'ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []

    # Test user_id filter (uses parameterized query)
    filter_user = requests.RequestTaskFilter(user_id='test-user-123')
    sql, params = filter_user.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE user_id = ? ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == ['test-user-123']

    # Test exclude_request_names filter
    filter_exclude = requests.RequestTaskFilter(
        exclude_request_names=['request1', 'request2'])
    sql, params = filter_exclude.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE name NOT IN (\'request1\',\'request2\') '
                    'ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []

    # Test include_request_names filter
    filter_include = requests.RequestTaskFilter(
        include_request_names=['request3', 'request4'])
    sql, params = filter_include.build_query()
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE name IN (\'request3\',\'request4\') '
                    'ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []

    # Test finished_before filter (uses parameterized query)
    timestamp = 1234567890.0
    filter_finished = requests.RequestTaskFilter(finished_before=timestamp)
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
        finished_before=9876543210.0)
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
                                   include_request_names=['req2'])

    # Test special characters in names are properly escaped with repr()
    filter_special_chars = requests.RequestTaskFilter(
        cluster_names=['cluster\'with\'quotes', 'cluster\"with\"double'])
    sql, params = filter_special_chars.build_query()
    # repr() should properly escape the quotes
    expected_sql = (f'SELECT {expected_columns} FROM {requests.REQUEST_TABLE} '
                    'WHERE cluster_name IN (\"cluster\'with\'quotes\",'
                    '\'cluster\"with\"double\') ORDER BY created_at DESC')
    assert sql == expected_sql
    assert params == []
