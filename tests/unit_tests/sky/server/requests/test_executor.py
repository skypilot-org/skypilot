"""Unit tests for sky.server.requests.executor."""
import concurrent.futures
import time
from unittest import mock

import pytest

from sky.server.requests import executor


def _dummy_task():
    """A dummy task that sleeps for a short time."""
    time.sleep(0.1)
    return 42


@pytest.fixture
def mock_process_pool():
    """Fixture to mock ProcessPoolExecutor."""
    with mock.patch('sky.server.requests.executor.ProcessPoolExecutor',
                    autospec=True) as mock_pool:
        # Mock _max_workers attribute
        mock_pool.return_value._max_workers = 2
        yield mock_pool


def test_burstable_process_pool_executor_init():
    """Test BurstableProcessPoolExecutor initialization."""
    # Test initialization without burst workers
    executor_no_burst = executor.BurstableProcessPoolExecutor(max_workers=2)
    assert executor_no_burst._burst_pool is None

    # Test initialization with burst workers
    executor_with_burst = executor.BurstableProcessPoolExecutor(max_workers=2,
                                                                burst_workers=1)
    assert executor_with_burst._burst_pool is not None
    assert executor_with_burst._burst_pool._max_workers == 1
    assert not executor_with_burst._burst_pool._reuse_worker

    # Cleanup
    executor_no_burst.shutdown()
    executor_with_burst.shutdown()


def test_burstable_process_pool_executor_submit(mock_process_pool):
    """Test task submission behavior of BurstableProcessPoolExecutor."""
    pool = executor.BurstableProcessPoolExecutor(max_workers=1, burst_workers=1)

    try:
        # Configure mock behavior
        mock_process_pool.return_value.has_idle_workers.return_value = False
        mock_process_pool.return_value.submit.return_value.result.return_value = 42

        # Submit first task to main pool
        future1 = pool.submit(_dummy_task)
        # Main pool should be busy now
        assert not pool.has_idle_workers()

        # Submit second task - should go to burst pool
        future2 = pool.submit(_dummy_task)

        # Both tasks should complete successfully
        assert future1.result() == 42
        assert future2.result() == 42

    finally:
        pool.shutdown()


def test_burstable_process_pool_executor_shutdown():
    """Test shutdown behavior of BurstableProcessPoolExecutor."""
    # Create a mock for the parent class's shutdown method
    with mock.patch.object(executor.ProcessPoolExecutor,
                           'shutdown',
                           autospec=True) as mock_parent_shutdown:
        # Create the pool
        pool = executor.BurstableProcessPoolExecutor(max_workers=1,
                                                     burst_workers=1)
        # Create and set a mock burst pool
        mock_burst = mock.Mock()
        pool._burst_pool = mock_burst

        # Call shutdown
        pool.shutdown(wait=True)

        # Verify both pools are shut down with correct arguments
        mock_burst.shutdown.assert_called_once_with(wait=True)
        mock_parent_shutdown.assert_called_once_with(mock.ANY, wait=True)


def test_burstable_process_pool_executor_no_burst_when_main_idle(
        mock_process_pool):
    """Test that tasks go to main pool when it has idle workers."""
    pool = executor.BurstableProcessPoolExecutor(max_workers=2, burst_workers=1)

    try:
        # Configure mock behavior
        mock_process_pool.return_value.has_idle_workers.return_value = True
        mock_process_pool.return_value.submit.return_value.result.return_value = 42

        # Mock burst pool submit to verify it's not called
        with mock.patch.object(executor.ProcessPoolExecutor,
                               'submit') as mock_burst_submit:
            future = pool.submit(_dummy_task)
            assert future.result() == 42
            mock_burst_submit.assert_not_called()

    finally:
        pool.shutdown()


def test_burstable_process_pool_executor_burst_when_main_busy(
        mock_process_pool):
    """Test that tasks go to burst pool when main pool is busy."""
    pool = executor.BurstableProcessPoolExecutor(max_workers=1, burst_workers=1)

    try:
        # Configure mock behavior for main pool
        mock_process_pool.return_value.has_idle_workers.return_value = False
        mock_process_pool.return_value.submit.return_value.result.return_value = 42

        # Submit task to fill main pool
        future1 = pool.submit(_dummy_task)

        # Configure mock for burst pool
        mock_burst_pool = mock.Mock()
        mock_burst_pool.has_idle_workers.return_value = True
        mock_burst_pool.submit.return_value.result.return_value = 42
        pool._burst_pool = mock_burst_pool

        # Submit another task - should go to burst pool
        future2 = pool.submit(_dummy_task)

        # Both tasks should complete
        assert future1.result() == 42
        assert future2.result() == 42

        # Verify burst pool was used
        mock_burst_pool.submit.assert_called_once()

    finally:
        pool.shutdown()
