"""Unit tests for sky/server/requests/process.py."""
import time
from concurrent.futures import Future
import pytest

from sky.server.requests.process import (PoolExecutor, DisposableExecutor,
                                       BurstableExecutor)


def dummy_task(sleep_time=0.1):
    """A dummy task that sleeps for a given time."""
    time.sleep(sleep_time)
    return True


def failing_task():
    """A task that raises an exception."""
    raise ValueError('Task failed')


def wait_for_workers_cleanup(executor, timeout=5):
    """Wait for workers to be cleaned up.
    
    Args:
        executor: The DisposableExecutor instance
        timeout: Maximum time to wait in seconds
    
    Returns:
        bool: True if workers are cleaned up, False if timeout
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        with executor._lock:
            if len(executor.workers) == 0:
                return True
        time.sleep(0.1)
    return False


def test_pool_executor():
    """Test PoolExecutor functionality."""
    executor = PoolExecutor(max_workers=2)
    try:
        # Test submit and has_idle_workers
        assert executor.has_idle_workers()
        future = executor.submit(dummy_task)
        assert isinstance(future, Future)
        assert future.result()

        # Test multiple tasks
        futures = [
            executor.submit(dummy_task, sleep_time=0.1) for _ in range(2)
        ]
        # Should have no idle workers when both are running
        assert not executor.has_idle_workers()
        # Wait for completion
        assert all(f.result() for f in futures)
        # Should have idle workers after completion
        assert executor.has_idle_workers()
    finally:
        executor.shutdown()


def test_disposable_executor():
    """Test DisposableExecutor functionality."""
    executor = DisposableExecutor(max_workers=2)
    try:
        # Test submit and has_idle_workers
        assert executor.has_idle_workers()
        assert executor.submit(dummy_task)

        # Test multiple tasks
        assert executor.submit(dummy_task)
        assert not executor.has_idle_workers()  # No idle workers when full

        # Wait for tasks to complete and workers to be cleaned up
        assert wait_for_workers_cleanup(executor), "Workers not cleaned up"
        assert executor.has_idle_workers()  # Should have idle workers now

        # Test with failing task
        assert executor.submit(failing_task)
        assert wait_for_workers_cleanup(executor), "Failed task worker not cleaned up"
        assert executor.has_idle_workers()  # Worker should be cleaned up
    finally:
        executor.shutdown()


def test_burstable_executor():
    """Test BurstableExecutor functionality."""
    executor = BurstableExecutor(garanteed_workers=1, burst_workers=1)
    try:
        # Submit tasks that should go to guaranteed pool first
        executor.submit_until_success(dummy_task)
        # Submit another task that should go to burst pool
        executor.submit_until_success(dummy_task)
        # Submit one more task that should wait and go to guaranteed pool
        executor.submit_until_success(dummy_task)
        # Wait for tasks to complete
        time.sleep(0.3)
    finally:
        executor.shutdown()


def test_burstable_executor_no_guaranteed():
    """Test BurstableExecutor with only burst workers."""
    executor = BurstableExecutor(garanteed_workers=0, burst_workers=1)
    try:
        # Should use burst pool
        executor.submit_until_success(dummy_task)
        time.sleep(0.2)
        # Should be able to submit another task after first one completes
        executor.submit_until_success(dummy_task)
    finally:
        executor.shutdown()


def test_burstable_executor_no_burst():
    """Test BurstableExecutor with only guaranteed workers."""
    executor = BurstableExecutor(garanteed_workers=1, burst_workers=0)
    try:
        # Should use guaranteed pool
        executor.submit_until_success(dummy_task)
        # Should queue to guaranteed pool even when busy
        executor.submit_until_success(dummy_task)
    finally:
        executor.shutdown()
