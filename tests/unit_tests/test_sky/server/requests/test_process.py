"""Unit tests for sky/server/requests/process.py."""
from concurrent.futures import Future
import concurrent.futures.process
import multiprocessing
import os
import signal
import sys
import threading
import time
import unittest.mock

import prometheus_client
import pytest

from sky.metrics import utils as metrics_utils
from sky.server.requests import process as requests_process
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


def record_init(log_path, seconds):
    """Initializer that sleeps `seconds` and logs when it ran."""
    start = time.time()
    time.sleep(seconds)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(f'{start} {time.time()}\n')


def die_in_init():
    """Initializer that kills the worker before it reports ready."""
    os._exit(1)


def hang_until(marker, seconds):
    """Initializer that hangs until `marker` exists (at most `seconds`)."""
    deadline = time.time() + seconds
    while not os.path.exists(marker) and time.time() < deadline:
        time.sleep(0.05)


def first_fast_rest_slow(marker, seconds):
    """Initializer: the first worker starts at once and writes its pid to
    `marker`; every other worker sleeps `seconds` first."""
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        time.sleep(seconds)
        return
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)


def first_fast_rest_exit(marker, delay, how):
    """Initializer: the first worker starts at once and writes its pid to
    `marker`; every other worker waits `delay` seconds, then exits the way
    `how` says, before it is ready."""
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        time.sleep(delay)
        if how == 'exit1':
            os._exit(1)
        if how == 'exit0':
            os._exit(0)
        os.kill(os.getpid(), getattr(signal, how))
        time.sleep(60)  # The signal ends the process.
        return
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)


# Exit code of each way a worker exits in first_fast_rest_exit.
_SELF_EXIT_CODES = {
    'exit1': 1,
    'exit0': 0,
    'SIGSEGV': -signal.SIGSEGV,
    'SIGABRT': -signal.SIGABRT,
    'SIGKILL': -signal.SIGKILL,
}


def raise_in_init():
    """Initializer that fails the way a broken import or plugin would."""
    raise RuntimeError('start-up failed')


def _sample(name, **labels):
    return prometheus_client.REGISTRY.get_sample_value(name, labels) or 0.0


def _start_failures(pool, reason):
    return _sample('sky_apiserver_executor_worker_start_failures_total',
                   pool=pool,
                   reason=reason)


def _pool_broken(pool, phase):
    return _sample('sky_apiserver_executor_pool_broken_total',
                   pool=pool,
                   phase=phase)


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A child that exited but was not reaped yet still has its pid.
    try:
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


def _peak_overlap(log_path):
    """Most initializer runs in `log_path` that overlapped in time."""
    with open(log_path, encoding='utf-8') as f:
        windows = [tuple(map(float, line.split())) for line in f]
    events = sorted([(start, 1) for start, _ in windows] +
                    [(end, -1) for _, end in windows])
    overlap = peak = 0
    for _, delta in events:
        overlap += delta
        peak = max(peak, overlap)
    return len(windows), peak


def _wait_until(predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


requires_warm_start = pytest.mark.skipif(
    sys.version_info < (3, 11), reason='Warm start needs Python 3.11+.')


def test_pool_executor_lazy_by_default():
    """Without warm start, no worker starts until a task is submitted."""
    executor = PoolExecutor(max_workers=3)
    try:
        time.sleep(0.5)
        assert len(executor._processes) == 0
    finally:
        executor.shutdown()


@requires_warm_start
def test_pool_executor_warm_start_starts_all_workers():
    """Warm start starts every worker without any task being submitted."""
    executor = PoolExecutor(max_workers=4, warm_start=True)
    try:
        assert _wait_until(lambda: len(executor._processes) == 4)
        pids = set(executor._processes)
        # Tasks run on the workers warm start started; no extra worker starts.
        futures = [executor.submit(get_pid) for _ in range(8)]
        assert {f.result(timeout=30) for f in futures} <= pids
        assert set(executor._processes) == pids
    finally:
        executor.shutdown()


@requires_warm_start
def test_pool_executor_serves_tasks_during_warm_start():
    """Ready workers run tasks while warm start is still starting others,
    and shutdown stops warm start."""
    executor = PoolExecutor(max_workers=30, warm_start=True)
    try:
        pid = executor.submit(get_pid).result(timeout=30)
        assert pid in executor._processes
        assert executor._warm_start_thread.is_alive()
        assert len(executor._processes) < 30
    finally:
        executor.shutdown()
    assert not executor._warm_start_thread.is_alive()


@requires_warm_start
def test_pool_executor_warm_start_caps_starting_workers(tmp_path):
    """Warm start never has more than _WARM_START_MAX_STARTING workers
    starting at once, and the pool initializer runs in every worker."""
    log_path = str(tmp_path / 'init.log')
    executor = PoolExecutor(max_workers=5,
                            warm_start=True,
                            initializer=record_init,
                            initargs=(log_path, 0.5))
    try:
        assert executor._warm_start_done.wait(60)
        runs, peak = _peak_overlap(log_path)
        assert runs == 5
        assert peak <= requests_process._WARM_START_MAX_STARTING
    finally:
        executor.shutdown()


@requires_warm_start
def test_pool_executor_warm_start_caps_workers_started_by_submits(tmp_path):
    """Tasks submitted during warm start wait for a ready worker instead of
    starting more workers, so the limit on workers starting at once holds."""
    log_path = str(tmp_path / 'init.log')
    executor = PoolExecutor(max_workers=6,
                            warm_start=True,
                            initializer=record_init,
                            initargs=(log_path, 0.6))
    try:
        # Submit all at once, before any worker is ready.
        futures = [executor.submit(get_pid) for _ in range(6)]
        assert all(f.result(timeout=60) for f in futures)
        assert executor._warm_start_done.wait(60)
        runs, peak = _peak_overlap(log_path)
        assert runs == 6
        assert peak <= requests_process._WARM_START_MAX_STARTING
    finally:
        executor.shutdown()


@requires_warm_start
def test_pool_executor_warm_start_survives_worker_dying_in_init():
    """If a worker dies before it is ready, the pool breaks, but warm start
    and shutdown still finish instead of hanging."""
    executor = PoolExecutor(max_workers=4,
                            warm_start=True,
                            initializer=die_in_init)
    executor._warm_start_thread.join(timeout=30)
    assert not executor._warm_start_thread.is_alive()
    assert executor.broke_during_warm_start()
    shutdown = threading.Thread(target=executor.shutdown)
    shutdown.start()
    shutdown.join(timeout=30)
    assert not shutdown.is_alive()


@requires_warm_start
def test_pool_executor_warm_start_stops_when_workers_stall(
        tmp_path, monkeypatch):
    """If starting workers never become ready, warm start gives up and the
    rest of the pool starts on demand."""
    monkeypatch.setattr(requests_process, '_WARM_START_STALL_SECONDS', 1)
    executor = PoolExecutor(max_workers=6,
                            warm_start=True,
                            initializer=record_init,
                            initargs=(str(tmp_path / 'init.log'), 60))
    try:
        executor._warm_start_thread.join(timeout=30)
        assert not executor._warm_start_thread.is_alive()
        started = len(executor._processes)
        assert started <= requests_process._WARM_START_MAX_STARTING
    finally:
        executor.shutdown()


def test_pool_executor_warm_start_off_with_fork():
    """Warm start is skipped with fork, and before Python 3.11."""
    executor = PoolExecutor(max_workers=2,
                            warm_start=True,
                            mp_context=multiprocessing.get_context('fork'))
    try:
        assert executor._warm_start_thread is None
    finally:
        executor.shutdown()


@requires_warm_start
def test_burstable_executor_rebuilt_pool_starts_workers_on_demand():
    """If a pool breaks after warm start, the break counts as "serving" and
    the new pool does not use warm start: it starts only the workers its
    tasks need."""
    before = _pool_broken('rebuild-test', 'serving')
    executor = BurstableExecutor(garanteed_workers=2,
                                 warm_start=True,
                                 name='rebuild-test')
    try:
        original = executor._executor
        assert original._warm_start_done.wait(60)
        os.kill(next(iter(original._processes)), signal.SIGKILL)
        assert _wait_until(lambda: original._broken)
        assert executor.submit_until_success(dummy_task).result(timeout=60)
        rebuilt = executor._executor
        assert rebuilt is not original
        assert rebuilt._warm_start_thread is None
        time.sleep(2)  # A warm start would have started the second worker.
        assert len(rebuilt._processes) == 1
        assert _pool_broken('rebuild-test', 'serving') == before + 1
    finally:
        executor.shutdown()


def test_pool_executor_counts_worker_start_errors():
    """A pool worker whose initializer raises is counted as a start failure."""
    before = _start_failures('pool-error-test', 'error')
    executor = PoolExecutor(max_workers=1,
                            name='pool-error-test',
                            initializer=raise_in_init)
    try:
        with pytest.raises(concurrent.futures.process.BrokenProcessPool):
            executor.submit(get_pid).result(timeout=60)
        executor.drain_start_signals()
        assert _start_failures('pool-error-test', 'error') == before + 1
    finally:
        executor.shutdown()


@pytest.mark.parametrize('initializer,reason', [(raise_in_init, 'error'),
                                                (die_in_init, 'died')])
def test_disposable_executor_counts_worker_start_failures(initializer, reason):
    """A one-off worker that fails to start is counted with the reason; a
    task that fails after start-up is not counted."""
    pool = f'disposable-{reason}-test'
    before = _start_failures(pool, reason)
    executor = DisposableExecutor(max_workers=1,
                                  initializer=initializer,
                                  name=pool)
    try:
        with pytest.raises(BaseException):
            executor.submit(get_pid).result(timeout=60)
        assert _wait_until(lambda: _start_failures(pool, reason) == before + 1)
    finally:
        executor.shutdown()

    healthy = DisposableExecutor(max_workers=1, name='disposable-ok-test')
    try:
        with pytest.raises(ValueError):
            healthy.submit(failing_task).result(timeout=60)
        time.sleep(0.5)
        assert _start_failures('disposable-ok-test', 'error') == 0
        assert _start_failures('disposable-ok-test', 'died') == 0
    finally:
        healthy.shutdown()


@requires_warm_start
def test_burstable_executor_counts_worker_killed_during_warm_start():
    """If workers die during warm start, the break counts as "warm_start"
    and as a worker that died while starting."""
    pool = 'warm-died-test'
    broken_before = _pool_broken(pool, 'warm_start')
    died_before = _start_failures(pool, 'died')
    executor = BurstableExecutor(garanteed_workers=2,
                                 warm_start=True,
                                 name=pool,
                                 initializer=die_in_init)
    try:
        assert _wait_until(lambda: executor._executor._broken)
        with executor._guaranteed_pool_lock:
            executor._record_broken_pool(executor._executor)
        assert _pool_broken(pool, 'warm_start') == broken_before + 1
        assert _start_failures(pool, 'died') == died_before + 1
    finally:
        executor.shutdown()


@requires_warm_start
def test_pool_break_after_warm_start_stalls_is_not_a_start_failure(
        tmp_path, monkeypatch):
    """After warm start gives up, a later break counts as "serving", and
    tasks queued during warm start get workers."""
    monkeypatch.setattr(requests_process, '_WARM_START_STALL_SECONDS', 1)
    executor = PoolExecutor(max_workers=3,
                            warm_start=True,
                            initializer=record_init,
                            initargs=(str(tmp_path / 'init.log'), 2.5))
    try:
        futures = [executor.submit(get_pid) for _ in range(3)]
        executor._warm_start_thread.join(timeout=30)
        assert executor._warm_start_end == 'stalled'
        # After warm start gave up, every queued task got a worker.
        assert all(f.result(timeout=60) for f in futures)
        os.kill(next(iter(executor._processes)), signal.SIGKILL)
        assert _wait_until(lambda: executor._broken)
        executor.drain_start_signals()
        assert not executor.broke_during_warm_start()
    finally:
        executor.shutdown()


@requires_warm_start
def test_task_death_during_warm_start_is_not_a_start_failure(tmp_path):
    """If a ready worker dies while running a task during warm start, it is
    not counted as a start failure."""
    executor = PoolExecutor(max_workers=2,
                            warm_start=True,
                            initializer=record_init,
                            initargs=(str(tmp_path / 'init.log'), 0.1))
    try:
        assert _wait_until(lambda: executor.drain_start_signals() >= 0 and
                           executor._ready_count == 2)
        # Act as if warm start is still running, then kill a ready worker.
        executor._warm_start_end = None
        os.kill(next(iter(executor._processes)), signal.SIGKILL)
        assert _wait_until(lambda: executor._broken)
        executor.drain_start_signals()
        assert not executor.broke_during_warm_start()
    finally:
        executor.shutdown()


@requires_warm_start
def test_pool_executor_first_submit_starts_a_worker_during_warm_start(
        monkeypatch):
    """A task submitted before warm start has started any worker starts the
    first worker itself, and shutting down right after does not hang."""
    monkeypatch.setattr(requests_process, '_WARM_START_POLL_SECONDS', 5)
    executor = PoolExecutor(max_workers=3, warm_start=True)
    future = executor.submit(get_pid)
    assert len(executor._processes) == 1
    assert future.result(timeout=60) in executor._processes
    manager = executor._executor_manager_thread
    shutdown = threading.Thread(target=executor.shutdown)
    shutdown.start()
    shutdown.join(timeout=30)
    assert not shutdown.is_alive()
    # A manager thread left waiting for a worker would block interpreter exit.
    manager.join(timeout=30)
    assert not manager.is_alive()


@requires_warm_start
def test_pool_executor_notices_fallback_worker_death_after_stall(
        tmp_path, monkeypatch):
    """A worker started after warm start gives up, that then dies, fails its
    task, even while the other workers are still stuck starting."""
    monkeypatch.setattr(requests_process, '_WARM_START_STALL_SECONDS', 3)
    marker = str(tmp_path / 'go')
    executor = PoolExecutor(max_workers=3,
                            warm_start=True,
                            initializer=hang_until,
                            initargs=(marker, 60))
    try:
        assert _wait_until(lambda: len(executor._processes) == 2)
        stuck = set(executor._processes)
        future = executor.submit(dummy_task, 30)
        # The task is queued during warm start, so only the fallback after
        # the stall starts a worker for it.
        assert executor._warm_start_running()
        assert len(executor._processes) == 2
        # Release the workers only after the stall, so the fallback has to
        # start the third one for the queued task.
        executor._warm_start_thread.join(timeout=30)
        assert executor._warm_start_end == 'stalled'
        assert _wait_until(lambda: len(executor._processes) == 3)
        fallback = (set(executor._processes) - stuck).pop()
        open(marker, 'w', encoding='utf-8').close()
        os.kill(fallback, signal.SIGKILL)
        with pytest.raises(concurrent.futures.process.BrokenProcessPool):
            future.result(timeout=20)
    finally:
        executor.shutdown()


@requires_warm_start
def test_serving_crash_while_another_worker_starts_is_not_a_start_failure(
        tmp_path):
    """If a ready worker dies while another is still starting, it counts as
    a crash while serving, not as a start failure."""
    marker = str(tmp_path / 'first')
    executor = PoolExecutor(max_workers=2,
                            warm_start=True,
                            initializer=first_fast_rest_slow,
                            initargs=(marker, 30))
    try:
        assert _wait_until(lambda: executor.drain_start_signals(
        ) >= 0 and executor._ready_count == 1 and len(executor._processes) == 2)
        with open(marker, encoding='utf-8') as f:
            ready_pid = int(f.read())
        starting = executor._processes[(set(executor._processes) -
                                        {ready_pid}).pop()]
        os.kill(ready_pid, signal.SIGKILL)
        assert _wait_until(lambda: executor._broken)
        executor.wait_until_broken_handled(30)
        # The stdlib stopped the worker that was still starting; that is not
        # a start failure.
        assert starting.exitcode == -signal.SIGTERM
        executor.drain_start_signals()
        assert not executor.broke_during_warm_start()
    finally:
        executor.shutdown()


@requires_warm_start
def test_starting_worker_killed_while_another_is_ready_is_a_start_failure(
        tmp_path):
    """A worker killed before it is ready (e.g. out of memory) counts as a
    start failure, even if another worker is ready."""
    marker = str(tmp_path / 'first')
    executor = PoolExecutor(max_workers=2,
                            warm_start=True,
                            initializer=first_fast_rest_slow,
                            initargs=(marker, 30))
    try:
        assert _wait_until(lambda: executor.drain_start_signals(
        ) >= 0 and executor._ready_count == 1 and len(executor._processes) == 2)
        with open(marker, encoding='utf-8') as f:
            ready_pid = int(f.read())
        starting_pid = (set(executor._processes) - {ready_pid}).pop()
        os.kill(starting_pid, signal.SIGKILL)
        assert _wait_until(lambda: executor._broken)
        executor.wait_until_broken_handled(30)
        executor.drain_start_signals()
        assert executor.broke_during_warm_start()
    finally:
        executor.shutdown()


@requires_warm_start
@pytest.mark.parametrize('how', sorted(_SELF_EXIT_CODES))
def test_starting_worker_exiting_while_another_is_ready_is_a_start_failure(
        tmp_path, how):
    """A worker that exits on its own before it is ready (exit, crash, abort
    or kill) counts as a start failure, even if another worker is ready."""
    marker = str(tmp_path / 'first')
    executor = PoolExecutor(max_workers=2,
                            warm_start=True,
                            initializer=first_fast_rest_exit,
                            initargs=(marker, 3, how))
    try:
        assert _wait_until(lambda: executor.drain_start_signals(
        ) >= 0 and executor._ready_count == 1 and len(executor._processes) == 2)
        with open(marker, encoding='utf-8') as f:
            ready_pid = int(f.read())
        starting = executor._processes[(set(executor._processes) -
                                        {ready_pid}).pop()]
        assert _wait_until(lambda: executor._broken)
        executor.wait_until_broken_handled(30)
        assert starting.exitcode == _SELF_EXIT_CODES[how]
        assert executor.start_failures == 0  # Nothing was reported.
        assert executor.broke_during_warm_start()
    finally:
        executor.shutdown()


@requires_warm_start
def test_starting_worker_abort_while_another_is_ready_counts_as_warm_start_break(
        tmp_path):
    """If a starting worker aborts while another is ready, the break counts
    as "warm_start" and as a worker that died while starting, not as
    "serving"; the new pool starts workers on demand."""
    pool = 'abort-while-ready-test'
    warm_before = _pool_broken(pool, 'warm_start')
    serving_before = _pool_broken(pool, 'serving')
    died_before = _start_failures(pool, 'died')
    marker = str(tmp_path / 'first')
    # Warm start or the next request records the break, whichever is first.
    wait = unittest.mock.patch.object(
        PoolExecutor,
        'wait_until_broken_handled',
        autospec=True,
        side_effect=PoolExecutor.wait_until_broken_handled).start()
    executor = BurstableExecutor(garanteed_workers=2,
                                 warm_start=True,
                                 name=pool,
                                 initializer=first_fast_rest_exit,
                                 initargs=(marker, 3, 'exit1'))
    try:
        original = executor._executor
        assert _wait_until(lambda: original.drain_start_signals() >= 0 and
                           original._ready_count == 1)
        assert _wait_until(lambda: original._broken)
        executor.submit_until_success(dummy_task)
        # Telling what broke the pool needed the exit codes, once.
        assert wait.call_count == 1
        assert executor._executor is not original
        assert _pool_broken(pool, 'warm_start') == warm_before + 1
        assert _pool_broken(pool, 'serving') == serving_before
        assert _start_failures(pool, 'died') == died_before + 1
        assert executor._executor._warm_start_thread is None
    finally:
        unittest.mock.patch.stopall()
        executor.shutdown()


def test_pool_recovery_without_warm_start_does_not_wait():
    """Replacing a broken pool that did not use warm start does not wait for
    its workers to exit: their exit codes would not change the result."""
    pool = 'no-warm-start-recovery-test'
    serving_before = _pool_broken(pool, 'serving')
    executor = BurstableExecutor(garanteed_workers=1, name=pool)
    try:
        original = executor._executor
        pid = executor.submit_until_success(get_pid).result(timeout=60)
        os.kill(pid, signal.SIGKILL)
        assert _wait_until(lambda: original._broken)
        with unittest.mock.patch.object(PoolExecutor,
                                        'wait_until_broken_handled') as wait:
            assert executor.submit_until_success(get_pid).result(timeout=60)
        wait.assert_not_called()
        assert executor._executor is not original
        assert _pool_broken(pool, 'serving') == serving_before + 1
    finally:
        executor.shutdown()


@requires_warm_start
def test_pool_recovery_does_not_wait_when_no_worker_was_ready():
    """If a pool breaks during warm start before any worker is ready, it is
    counted as "warm_start" without waiting for exit codes."""
    pool = 'no-ready-recovery-test'
    warm_before = _pool_broken(pool, 'warm_start')
    executor = BurstableExecutor(garanteed_workers=2,
                                 warm_start=True,
                                 name=pool,
                                 initializer=die_in_init)
    try:
        original = executor._executor
        assert _wait_until(lambda: original._broken)
        with unittest.mock.patch.object(PoolExecutor,
                                        'wait_until_broken_handled') as wait:
            executor.submit_until_success(dummy_task)
        wait.assert_not_called()
        assert _pool_broken(pool, 'warm_start') == warm_before + 1
    finally:
        executor.shutdown()


def test_warns_when_start_up_installs_a_sigterm_handler():
    """A warning is logged if worker start-up installs a SIGTERM handler,
    since that would make stopped workers look like start failures."""
    previous = signal.getsignal(signal.SIGTERM)
    try:
        with unittest.mock.patch.object(requests_process.logger,
                                        'warning') as warning:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            requests_process._warn_if_sigterm_handled()
            warning.assert_not_called()
            signal.signal(signal.SIGTERM, lambda *_: None)
            requests_process._warn_if_sigterm_handled()
            warning.assert_called_once()
    finally:
        signal.signal(signal.SIGTERM, previous)


@requires_warm_start
def test_workers_crashing_at_start_break_only_the_warm_started_pool():
    """If every worker dies while starting, only the warm-started pool
    breaks that way (counted once); the new pool does not use warm start,
    so the crash is not repeated for every worker."""
    pool = 'crash-loop-test'
    warm_before = _pool_broken(pool, 'warm_start')
    died_before = _start_failures(pool, 'died')
    executor = BurstableExecutor(garanteed_workers=2,
                                 warm_start=True,
                                 name=pool,
                                 initializer=die_in_init)
    try:
        original = executor._executor
        assert original._warm_start_thread is not None
        assert _wait_until(lambda: original._broken)
        # The submit finds the pool broken and replaces it.
        executor.submit_until_success(dummy_task)
        rebuilt = executor._executor
        assert rebuilt is not original
        assert rebuilt._warm_start_thread is None
        assert _pool_broken(pool, 'warm_start') == warm_before + 1
        assert _start_failures(pool, 'died') == died_before + 1
    finally:
        executor.shutdown()


@requires_warm_start
def test_serving_crash_is_counted_as_serving_break(tmp_path):
    """If a ready worker crashes while another is still starting, replacing
    the pool counts it as "serving", with no start failure."""
    pool = 'serving-crash-test'
    serving_before = _pool_broken(pool, 'serving')
    warm_before = _pool_broken(pool, 'warm_start')
    died_before = _start_failures(pool, 'died')
    error_before = _start_failures(pool, 'error')
    marker = str(tmp_path / 'first')
    executor = BurstableExecutor(garanteed_workers=2,
                                 warm_start=True,
                                 name=pool,
                                 initializer=first_fast_rest_slow,
                                 initargs=(marker, 30))
    try:
        original = executor._executor
        assert _wait_until(lambda: original.drain_start_signals(
        ) >= 0 and original._ready_count == 1 and len(original._processes) == 2)
        with open(marker, encoding='utf-8') as f:
            os.kill(int(f.read()), signal.SIGKILL)
        assert _wait_until(lambda: original._broken)
        executor.submit_until_success(dummy_task)
        assert executor._executor is not original
        assert _pool_broken(pool, 'serving') == serving_before + 1
        assert _pool_broken(pool, 'warm_start') == warm_before
        assert _start_failures(pool, 'died') == died_before
        assert _start_failures(pool, 'error') == error_before
    finally:
        executor.shutdown()


@requires_warm_start
def test_pool_break_at_boot_is_counted_without_a_request():
    """If every worker dies during warm start and no request ever reaches the
    pool, the break and the failed start are still counted, exactly once,
    even after a later request and shutdown."""
    pool = 'boot-break-test'
    warm_before = _pool_broken(pool, 'warm_start')
    died_before = _start_failures(pool, 'died')
    executor = BurstableExecutor(garanteed_workers=2,
                                 warm_start=True,
                                 name=pool,
                                 initializer=die_in_init)
    try:
        original = executor._executor
        # No request is sent: warm start notices the break and counts it.
        assert _wait_until(
            lambda: _pool_broken(pool, 'warm_start') == warm_before + 1 and
            _start_failures(pool, 'died') == died_before + 1)
        # A later request replaces the pool without counting the break again.
        executor.submit_until_success(dummy_task)
        assert executor._executor is not original
    finally:
        executor.shutdown()
    assert _pool_broken(pool, 'warm_start') == warm_before + 1
    assert _start_failures(pool, 'died') == died_before + 1


def test_shutdown_does_not_count_a_break_after_start_up():
    """Shutdown does not count a break that happened after start-up: stopping
    the server (e.g. Ctrl-C) can break the pool itself just before shutdown.
    A request that replaces the pool counts such breaks instead."""
    pool = 'shutdown-break-test'
    serving_before = _pool_broken(pool, 'serving')
    executor = BurstableExecutor(garanteed_workers=1, name=pool)
    try:
        pid = executor.submit_until_success(get_pid).result(timeout=60)
        os.kill(pid, signal.SIGKILL)
        assert _wait_until(lambda: executor._executor._broken)
    finally:
        executor.shutdown()
    assert _pool_broken(pool, 'serving') == serving_before


def test_shutdown_stops_workers_when_counting_a_break_fails():
    """If counting a broken pool raises, shutdown still stops the workers."""
    executor = PoolExecutor(max_workers=1)
    pid = executor.submit(get_pid).result(timeout=60)
    with unittest.mock.patch.object(PoolExecutor,
                                    'record_break',
                                    side_effect=RuntimeError('metrics down')):
        executor.shutdown()
    assert _wait_until(lambda: not _pid_alive(pid))


def test_shutdown_stops_workers_when_counting_a_start_failure_fails():
    """If counting a failed start raises during shutdown, shutdown still
    stops a worker that is busy with a task."""
    pool = PoolExecutor(max_workers=1)
    pid = pool.submit(get_pid).result(timeout=60)
    # Keep the worker busy, so only shutdown's kill can stop it.
    pool.submit(dummy_task, 120)
    # A start failure waiting on the start-up pipe when shutdown reads it.
    ready_writer = pool._initargs[0]
    requests_process._send_start_signal(ready_writer,
                                        requests_process._START_FAILED)
    try:
        with unittest.mock.patch.object(
                metrics_utils.
                SKY_APISERVER_EXECUTOR_WORKER_START_FAILURES_TOTAL,
                'labels',
                side_effect=OSError('metrics dir full')) as labels:
            start = time.time()
            pool.shutdown()
        assert labels.called
        # The task sleeps 120 s, so only shutdown's kill can end it this soon.
        assert _wait_until(lambda: not _pid_alive(pid), timeout=30)
        assert time.time() - start < 30
    finally:
        if _pid_alive(pid):
            os.kill(pid, signal.SIGKILL)


@requires_warm_start
def test_boot_break_found_by_shutdown_counts_as_warm_start(
        tmp_path, monkeypatch):
    """If shutdown stops warm start before warm start sees that the pool
    broke, the break still counts as "warm_start", not "serving"."""
    pool = 'shutdown-boot-break-test'
    warm_before = _pool_broken(pool, 'warm_start')
    serving_before = _pool_broken(pool, 'serving')
    marker = str(tmp_path / 'first')
    executor = PoolExecutor(max_workers=2,
                            warm_start=True,
                            name=pool,
                            initializer=first_fast_rest_slow,
                            initargs=(marker, 30))
    try:
        assert _wait_until(lambda: executor.drain_start_signals(
        ) >= 0 and executor._ready_count == 1 and len(executor._processes) == 2)
        # Make warm start's next wait long, and wait until it is in it, so it
        # cannot see the break before shutdown stops it.
        in_long_wait = threading.Event()
        stop_wait = executor._warm_start_stop.wait

        def wait(timeout=None):
            if timeout == 60:
                in_long_wait.set()
            return stop_wait(timeout)

        monkeypatch.setattr(executor._warm_start_stop, 'wait', wait)
        monkeypatch.setattr(requests_process, '_WARM_START_POLL_SECONDS', 60)
        assert in_long_wait.wait(30)
        with open(marker, encoding='utf-8') as f:
            ready_pid = int(f.read())
        starting_pid = (set(executor._processes) - {ready_pid}).pop()
        os.kill(starting_pid, signal.SIGKILL)
        assert _wait_until(lambda: executor._broken)
        assert executor._warm_start_end is None  # Warm start has not seen it.
    finally:
        executor.shutdown()
    assert executor._warm_start_end == 'stopped'
    assert _pool_broken(pool, 'warm_start') == warm_before + 1
    assert _pool_broken(pool, 'serving') == serving_before


@requires_warm_start
def test_burstable_executor_warm_start_applies_to_guaranteed_pool():
    """Warm start applies to the first guaranteed pool, not the burst pool."""
    executor = BurstableExecutor(garanteed_workers=2,
                                 burst_workers=1,
                                 warm_start=True)
    try:
        assert executor._executor._warm_start_thread is not None
        assert _wait_until(lambda: len(executor._executor._processes) == 2)
    finally:
        executor.shutdown()
