"""Unit tests for sky/server/requests/process.py."""
from concurrent.futures import Future
import concurrent.futures.process
import os
import signal
import time
import unittest.mock

import pytest

from sky.server.requests.process import BurstableExecutor
from sky.server.requests.process import DisposableExecutor
from sky.server.requests.process import PoolExecutor


def dummy_task(sleep_time=0.1):
    """A dummy task that sleeps for a given time."""
    time.sleep(sleep_time)
    return True


def failing_task():
    """A task that raises an exception."""
    raise ValueError('Task failed')


def killed_task():
    """A task whose process is killed before it returns."""
    os.kill(os.getpid(), signal.SIGKILL)


def none_task():
    return None


def verify_workers_cleanup(executor):
    """Verify workers to be cleaned up.

    Args:
        executor: The DisposableExecutor instance

    Returns:
        bool: True if workers are cleaned up, False if timeout
    """
    with executor._lock:
        if len(executor.workers) == 0:
            return True


def wait_for_futures(futures, timeout=20):
    """Wait for futures to complete.

    Args:
        futures: List of futures to wait for
        timeout: Maximum time to wait in seconds

    Returns:
        bool: True if all futures completed, False if timeout
    """
    start_time = time.time()
    try:
        for future in futures:
            remaining = max(0, timeout - (time.time() - start_time))
            future.result(timeout=remaining)
        return True
    except TimeoutError:
        return False


def test_pool_executor():
    """Test PoolExecutor functionality."""
    executor = PoolExecutor(max_workers=2)
    futures = []
    try:
        # Test submit and has_idle_workers
        assert executor.has_idle_workers()
        future = executor.submit(dummy_task, sleep_time=0.1)
        futures.append(future)
        assert isinstance(future, Future)

        # Test multiple tasks
        for _ in range(2):
            futures.append(executor.submit(dummy_task, sleep_time=0.1))
        # Should have no idle workers when both are running
        assert not executor.has_idle_workers()

        # Wait for all futures to complete before shutdown
        assert wait_for_futures(futures), "Tasks did not complete in time"
        assert all(f.done() for f in futures), "Not all tasks completed"
        assert all(f.result() for f in futures), "Some tasks failed"

        # Should have idle workers after completion
        assert executor.has_idle_workers()
    finally:
        # Wait a bit to ensure all tasks are truly done
        time.sleep(0.1)
        executor.shutdown()


def test_disposable_executor():
    """Test DisposableExecutor functionality."""
    executor = DisposableExecutor(max_workers=2)
    try:
        futs = []
        # Test submit and has_idle_workers
        assert executor.has_idle_workers()
        futs.append(executor.submit(dummy_task))

        # Test multiple tasks
        futs.append(executor.submit(dummy_task))
        assert not executor.has_idle_workers()  # No idle workers when full

        concurrent.futures.wait(futs)
        assert verify_workers_cleanup(executor), "Workers not cleaned up"
        assert executor.has_idle_workers()  # Should have idle workers now

        # Test with failing task
        failed_fut = executor.submit(failing_task)
        concurrent.futures.wait([failed_fut])
        assert verify_workers_cleanup(
            executor), "Failed task worker not cleaned up"
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


def test_burstable_executor_pool_recovery():
    """Test BurstableExecutor recovery from BrokenProcessPool exception."""

    executor = BurstableExecutor(garanteed_workers=1, burst_workers=0)

    try:
        # Store reference to original executor
        original_executor = executor._executor
        submit_call_count = 0

        # Mock the PoolExecutor.submit method at class level to control
        # behavior across all instances
        original_submit = PoolExecutor.submit

        def mock_submit(self, fn, *args, **kwargs):
            nonlocal submit_call_count
            submit_call_count += 1
            if submit_call_count == 1:
                # First call raises BrokenProcessPool to simulate pool failure
                raise concurrent.futures.process.BrokenProcessPool(
                    "Simulated process pool failure")
            else:
                # Subsequent calls should work normally
                # Call the original submit method
                return original_submit(self, fn, *args, **kwargs)

        with unittest.mock.patch.object(PoolExecutor, 'submit',
                                        new=mock_submit):
            # This should trigger the pool recovery logic in
            # _submit_to_guaranteed_pool
            future = executor.submit_until_success(dummy_task)
            # Recovery replaces the guaranteed pool with a brand-new
            # ProcessPoolExecutor, so this future's task runs on a worker
            # that must be spawned from scratch (fork/spawn + ``import sky``
            # in the child). That cold start can take several seconds under
            # CI load, so use a generous timeout to keep the test
            # deterministic while still catching genuine hangs.
            result = future.result(timeout=60.0)

            # Verify the task completed successfully despite initial failure
            assert result is True

            # Verify that submit was called at least twice
            # (initial failure + successful retry)
            assert submit_call_count >= 2

            # Verify that a new executor was created after the failure
            assert executor._executor is not original_executor

    finally:
        executor.shutdown()


def test_disposable_executor_fails_a_killed_worker():
    executor = DisposableExecutor(max_workers=1)
    try:
        killed = executor.submit(killed_task)
        with pytest.raises(concurrent.futures.process.BrokenProcessPool):
            killed.result(timeout=30)
        assert executor.submit(none_task).result(timeout=30) is None
    finally:
        executor.shutdown()


def get_pid():
    """Returns the pid of the worker that ran the task."""
    return os.getpid()


def slow_init(log_path, seconds):
    """Initializer that sleeps and records its start and end time."""
    start = time.time()
    time.sleep(seconds)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(f'{os.getpid()} {start} {time.time()}\n')


def _wait_until(predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_pool_executor_lazy_by_default():
    """Without warm start, no worker starts until a task is submitted."""
    executor = PoolExecutor(max_workers=3)
    try:
        time.sleep(0.5)
        assert len(executor._processes) == 0
        assert executor.wait_for_warm_start(timeout=0)
    finally:
        executor.shutdown()


def test_pool_executor_warm_start_starts_all_workers():
    """Warm start brings up every worker before any task is submitted."""
    executor = PoolExecutor(max_workers=4, warm_start=True)
    try:
        assert executor.wait_for_warm_start(timeout=60)
        pids = set(executor._processes)
        assert len(pids) == 4
        assert all(p.is_alive() for p in executor._processes.values())
        assert _wait_until(lambda: executor.starting_workers() == 0)
        # Tasks run on the pre-started workers; no extra worker is spawned.
        futures = [executor.submit(get_pid) for _ in range(8)]
        assert {f.result(timeout=30) for f in futures} <= pids
        assert set(executor._processes) == pids
    finally:
        executor.shutdown()


def test_pool_executor_warm_start_runs_initializer(tmp_path):
    """The pool's own initializer still runs once in every worker."""
    log_path = str(tmp_path / 'init.log')
    executor = PoolExecutor(max_workers=3,
                            warm_start=True,
                            initializer=slow_init,
                            initargs=(log_path, 0.05))
    try:
        assert executor.wait_for_warm_start(timeout=60)
        assert _wait_until(lambda: executor.starting_workers() == 0)
        with open(log_path, encoding='utf-8') as f:
            init_pids = {int(line.split()[0]) for line in f}
        assert init_pids == set(executor._processes)
    finally:
        executor.shutdown()


def test_pool_executor_warm_start_limits_concurrent_startup(tmp_path):
    """At most _WARM_START_CONCURRENCY workers start at the same time."""
    from sky.server.requests import (
        process)  # pylint: disable=import-outside-toplevel
    log_path = str(tmp_path / 'init.log')
    executor = PoolExecutor(max_workers=5,
                            warm_start=True,
                            initializer=slow_init,
                            initargs=(log_path, 0.4))
    try:
        assert executor.wait_for_warm_start(timeout=60)
        assert _wait_until(lambda: executor.starting_workers() == 0)
        with open(log_path, encoding='utf-8') as f:
            windows = [tuple(map(float, line.split()[1:])) for line in f]
        assert len(windows) == 5
        events = sorted([(start, 1) for start, _ in windows] +
                        [(end, -1) for _, end in windows])
        overlap = peak = 0
        for _, delta in events:
            overlap += delta
            peak = max(peak, overlap)
        assert peak <= process._WARM_START_CONCURRENCY
    finally:
        executor.shutdown()


def test_pool_executor_warm_start_stops_on_shutdown(tmp_path):
    """Shutting down during warm start stops it without errors."""
    log_path = str(tmp_path / 'init.log')
    executor = PoolExecutor(max_workers=6,
                            warm_start=True,
                            initializer=slow_init,
                            initargs=(log_path, 0.5))
    executor.shutdown()
    assert executor.wait_for_warm_start(timeout=10)
    assert len(executor._processes or {}) < 6


def test_pool_executor_warm_start_rejects_fork():
    """Warm start refuses a fork context."""
    import multiprocessing  # pylint: disable=import-outside-toplevel
    if 'fork' not in multiprocessing.get_all_start_methods():
        pytest.skip('fork is not available on this platform')
    with pytest.raises(ValueError):
        PoolExecutor(max_workers=1,
                     warm_start=True,
                     mp_context=multiprocessing.get_context('fork'))


def test_burstable_executor_warm_start_applies_to_guaranteed_pool():
    """Warm start reaches the guaranteed pool, including a rebuilt one."""
    executor = BurstableExecutor(garanteed_workers=2,
                                 burst_workers=1,
                                 warm_start=True)
    try:
        assert executor._executor is not None
        assert executor._executor.wait_for_warm_start(timeout=60)
        assert len(executor._executor._processes) == 2
        assert executor._guaranteed_pool_kwargs['warm_start'] is True
    finally:
        executor.shutdown()
