"""Unit tests for sky/server/requests/threads.py."""

import concurrent.futures
import os
import queue
import sys
import threading
import time
import weakref

import prometheus_client
import pytest

from sky import exceptions
from sky.metrics import utils as metrics_utils
from sky.server import hung_threads
from sky.server.requests import threads as threads_mod
from sky.server.requests.threads import OnDemandThreadExecutor


def dummy_task(sleep_time=0.05):
    """A dummy task that sleeps for a given time."""
    time.sleep(sleep_time)
    return True


def failing_task():
    """A task that raises an exception."""
    raise ValueError('Task failed')


def blocking_task(release_event: threading.Event):
    """A task that blocks until release_event is set."""
    release_event.wait(timeout=5)
    return True


def thread_name_task(name_queue: queue.Queue):
    """Put current thread name into the queue and return True."""
    name_queue.put(threading.current_thread().name)
    return True


def test_on_demand_executor_submit_and_result():
    executor = OnDemandThreadExecutor(name='test', max_workers=2)
    try:
        futs = [executor.submit(dummy_task), executor.submit(dummy_task)]
        done, not_done = concurrent.futures.wait(
            futs, timeout=5, return_when=concurrent.futures.ALL_COMPLETED)
        assert not not_done
        assert all(f.result() is True for f in done)
        # running should be back to 0 after completion
        assert executor.running.get() == 0
    finally:
        executor.shutdown()


def test_on_demand_executor_exception_propagation():
    executor = OnDemandThreadExecutor(name='test', max_workers=1)
    try:
        fut = executor.submit(failing_task)
        with pytest.raises(ValueError):
            fut.result(timeout=5)
        # running should be back to 0 even on exception
        assert executor.running.get() == 0
    finally:
        executor.shutdown()


def test_on_demand_executor_concurrency_limit():
    executor = OnDemandThreadExecutor(name='test', max_workers=2)
    release_event = threading.Event()
    try:
        f1 = executor.submit(blocking_task, release_event)
        f2 = executor.submit(blocking_task, release_event)
        # Third submit should exceed max_workers and raise
        with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
            executor.submit(blocking_task, release_event)
        # Allow running tasks to finish
        release_event.set()
        concurrent.futures.wait([f1, f2], timeout=5)
        assert f1.result() is True
        assert f2.result() is True
        assert executor.running.get() == 0
    finally:
        executor.shutdown()


def test_on_demand_executor_check_available_borrow_semantics():
    executor = OnDemandThreadExecutor(name='test', max_workers=1)
    try:
        # borrow=False should not change running
        assert executor.running.get() == 0
        count = executor.check_available()
        assert count == 1
        assert executor.running.get() == 0

        # borrow=True should increment, and exceeding should rollback
        count2 = executor.check_available(borrow=True)
        assert count2 == 1
        assert executor.running.get() == 1
        with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
            executor.check_available(borrow=True)
        # Still 1 borrowed
        assert executor.running.get() == 1
        # Return the borrowed worker
        executor.running.decrement()
        assert executor.running.get() == 0
    finally:
        executor.shutdown()


def test_on_demand_executor_shutdown_prevents_submit():
    executor = OnDemandThreadExecutor(name='test', max_workers=1)
    executor.shutdown()
    with pytest.raises(RuntimeError):
        executor.submit(dummy_task)


def test_on_demand_executor_thread_naming():
    executor = OnDemandThreadExecutor(name='myexec', max_workers=1)
    try:
        q = queue.Queue()
        fut = executor.submit(thread_name_task, q)
        assert fut.result(timeout=5) is True
        thread_name = q.get(timeout=5)
        assert thread_name.startswith('myexec-')
    finally:
        executor.shutdown()


def test_on_demand_executor_threads_dict_cleanup():
    executor = OnDemandThreadExecutor(name='test', max_workers=1)
    try:
        fut = executor.submit(dummy_task)
        assert fut.result(timeout=5) is True
        assert len(executor._threads) == 0
        assert executor.running.get() == 0
    finally:
        executor.shutdown()


def _gauge_value(name: str, executor_name: str) -> float:
    value = prometheus_client.REGISTRY.get_sample_value(name, {
        'pid': str(os.getpid()),
        'name': executor_name
    })
    assert value is not None
    return value


def test_on_demand_executor_metrics(monkeypatch):
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)
    name = 'metrics_test'
    executor = OnDemandThreadExecutor(name=name, max_workers=2)
    release_event = threading.Event()
    try:
        # The gauges must use 'liveall': the multiprocess collector strips
        # any label named 'pid' for the aggregating modes (livesum etc.) and
        # merges all processes into one series, hiding per-process
        # exhaustion. This registry-level test cannot catch that (the
        # multiprocess merge only runs when PROMETHEUS_MULTIPROC_DIR is
        # set), so pin the mode directly.
        # pylint: disable=protected-access
        assert (metrics_utils.SKY_APISERVER_THREADS_ACTIVE._multiprocess_mode ==
                'liveall')
        assert (metrics_utils.SKY_APISERVER_THREADS_MAX._multiprocess_mode ==
                'liveall')
        # pylint: enable=protected-access

        assert _gauge_value('sky_apiserver_threads_max', name) == 2
        assert _gauge_value('sky_apiserver_threads_active', name) == 0

        f1 = executor.submit(blocking_task, release_event)
        f2 = executor.submit(blocking_task, release_event)
        assert _gauge_value('sky_apiserver_threads_active', name) == 2

        with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
            executor.submit(blocking_task, release_event)
        assert prometheus_client.REGISTRY.get_sample_value(
            'sky_apiserver_threads_exhausted_total', {'name': name}) == 1
        # The rejected submit must not leak into the active gauge.
        assert _gauge_value('sky_apiserver_threads_active', name) == 2

        release_event.set()
        concurrent.futures.wait([f1, f2], timeout=5)
        assert f1.result() is True
        assert f2.result() is True
        # The gauge is decremented in the worker thread after the future
        # resolves; poll briefly to avoid racing that final decrement.
        deadline = time.time() + 5
        while time.time() < deadline:
            if _gauge_value('sky_apiserver_threads_active', name) == 0:
                break
            time.sleep(0.01)
        assert _gauge_value('sky_apiserver_threads_active', name) == 0
    finally:
        executor.shutdown()


# --- stuck-thread detection -------------------------------------------------


def _poll_forever(sock):
    """Block in poll(2) on a socket that never becomes readable, the way a
    thread parked in libpq does."""
    import select  # pylint: disable=import-outside-toplevel
    poller = select.poll()
    poller.register(sock.fileno(), select.POLLIN)
    poller.poll(-1)
    return True


def _capture_warnings(monkeypatch):
    messages = []
    monkeypatch.setattr(threads_mod.logger, 'warning',
                        lambda msg, *a, **k: messages.append(str(msg)))
    return messages


class _Clock:
    """Stand-in for the ``time`` module inside sky.server.requests.threads so
    the dump policy can be tested without sleeping through its intervals.
    Only ``monotonic`` is faked; ``sleep`` stays real for the watchdog."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    @staticmethod
    def sleep(seconds):
        time.sleep(seconds)


def _fake_clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(threads_mod, 'time', clock)
    # No ownerless-socket scan in these tests (covered in test_hung_threads).
    monkeypatch.setattr(threads_mod, '_last_scan', float('inf'))
    monkeypatch.setattr(threads_mod, '_db_peers', threads_mod._DbPeers([], []))  # pylint: disable=protected-access
    return clock


def _wait_for(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_observe_counts_stuck_threads_and_oldest_age():
    executor = OnDemandThreadExecutor(name='stuck_test',
                                      max_workers=2,
                                      stuck_after_seconds=0.2)
    release_event = threading.Event()
    try:
        assert executor.observe() == (0, 0.0)
        fut = executor.submit(blocking_task, release_event)
        stuck, oldest = executor.observe()
        assert stuck == 0 and 0 <= oldest < 0.2
        time.sleep(0.35)
        stuck, oldest = executor.observe()
        assert stuck == 1
        assert oldest >= 0.3
        release_event.set()
        assert fut.result(timeout=5) is True
        assert _wait_for(lambda: executor.observe() == (0, 0.0))
    finally:
        executor.shutdown()


def test_observe_without_threshold_reports_age_only():
    executor = OnDemandThreadExecutor(name='nothreshold_test', max_workers=1)
    release_event = threading.Event()
    try:
        fut = executor.submit(blocking_task, release_event)
        time.sleep(0.1)
        stuck, oldest = executor.observe()
        assert stuck == 0
        assert oldest >= 0.1
        release_event.set()
        assert fut.result(timeout=5) is True
    finally:
        executor.shutdown()


def test_task_records_start_and_deadline(monkeypatch):
    clock = _fake_clock(monkeypatch)
    executor = OnDemandThreadExecutor(name='deadline_test',
                                      max_workers=1,
                                      stuck_after_seconds=30.0)
    release_event = threading.Event()
    try:
        fut = executor.submit(blocking_task, release_event)
        # pylint: disable=protected-access
        (task,) = executor._threads.values()
        assert task.started == 1000.0
        assert task.deadline == 1030.0
        assert task.name.endswith('.blocking_task')
        clock.now = 1029.0
        assert executor.observe()[0] == 0
        clock.now = 1031.0
        assert executor.observe()[0] == 1
        # pylint: enable=protected-access
        release_event.set()
        assert fut.result(timeout=5) is True
    finally:
        executor.shutdown()


def test_stuck_metrics_gauges(monkeypatch):
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)
    name = 'stuck_metrics_test'
    executor = OnDemandThreadExecutor(name=name,
                                      max_workers=2,
                                      stuck_after_seconds=0.1)
    release_event = threading.Event()
    try:
        # pylint: disable=protected-access
        assert (metrics_utils.SKY_APISERVER_THREADS_STUCK._multiprocess_mode ==
                'liveall')
        assert (metrics_utils.SKY_APISERVER_THREADS_OLDEST_AGE_SECONDS.
                _multiprocess_mode == 'liveall')
        # pylint: enable=protected-access
        fut = executor.submit(blocking_task, release_event)
        time.sleep(0.2)
        executor.observe()
        assert _gauge_value('sky_apiserver_threads_stuck', name) == 1
        assert _gauge_value('sky_apiserver_threads_oldest_age_seconds',
                            name) >= 0.2
        release_event.set()
        assert fut.result(timeout=5) is True
        assert _wait_for(lambda: executor.observe() == (0, 0.0))
        assert _gauge_value('sky_apiserver_threads_stuck', name) == 0
        assert _gauge_value('sky_apiserver_threads_oldest_age_seconds',
                            name) == 0
    finally:
        executor.shutdown()


def test_exhaustion_dump_describes_blocked_thread(monkeypatch):
    """Exhaustion with a thread deliberately parked in poll(2): the log names
    the task, the frame, the syscall and the descriptor; the gauges count it."""
    import socket  # pylint: disable=import-outside-toplevel
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)
    messages = _capture_warnings(monkeypatch)
    name = 'dump_test'
    executor = OnDemandThreadExecutor(name=name,
                                      max_workers=1,
                                      stuck_after_seconds=0.1)
    a, b = socket.socketpair()
    try:
        executor.submit(_poll_forever, a)
        time.sleep(0.2)
        with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
            executor.submit(dummy_task)
        assert prometheus_client.REGISTRY.get_sample_value(
            'sky_apiserver_threads_exhausted_total', {'name': name}) == 1
        executor.observe()
        assert _gauge_value('sky_apiserver_threads_stuck', name) == 1
        assert _gauge_value('sky_apiserver_threads_oldest_age_seconds',
                            name) >= 0.2
        # The dump runs in its own thread; wait for it.
        assert _wait_for(lambda: len(messages) == 1)
        dump = messages[0]
        assert 'Executor [dump_test]' in dump
        assert 'all workers busy' in dump
        assert '1 of 1 workers running' in dump
        assert '(limit 0.1s)' in dump
        # The task name (unwrapped) and the blocking Python frame.
        assert 'test_threads._poll_forever' in dump
        assert '_poll_forever' in dump.split('python stack')[1]
        if sys.platform == 'linux':
            # The kernel view names the wait and the descriptor, and the
            # descriptor line says what it is now.
            assert 'syscall=poll' in dump or 'syscall=ppoll' in dump
            assert 'timeout=infinite' in dump
            assert f'fd={a.fileno()}' in dump
            assert f'fd {a.fileno()}: socket:[' in dump
            assert 'unix' in dump and 'recv-q=0' in dump
        # A second exhaustion moments later starts no dump thread (the
        # per-second start gate and the 60 s floor). The same-holder check
        # is covered by test_exhaustion_dump_only_when_slot_holders_change.
        with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
            executor.submit(dummy_task)
        time.sleep(0.2)
        assert len(messages) == 1
    finally:
        # Wake the poller so the executor can shut down.
        b.sendall(b'x')
        executor.shutdown()
        a.close()
        b.close()


def test_watchdog_dump_on_change_then_backoff(monkeypatch):
    """The watchdog dumps when a thread newly crosses the threshold, then
    repeats at doubling intervals while the stuck set is unchanged."""
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(threads_mod, '_DUMP_MIN_INTERVAL_SECONDS', 60.0)
    monkeypatch.setattr(threads_mod, '_DUMP_MAX_INTERVAL_SECONDS', 240.0)
    messages = _capture_warnings(monkeypatch)
    executor = OnDemandThreadExecutor(name='backoff_test',
                                      max_workers=4,
                                      stuck_after_seconds=30.0)
    release_a, release_b = threading.Event(), threading.Event()
    try:
        fut_a = executor.submit(blocking_task, release_a)
        clock.now += 31  # A crosses the threshold.
        assert executor.observe()[0] == 1
        assert len(messages) == 1
        assert 'running longer than 30s' in messages[0]
        assert 'unchanged' not in messages[0]
        # Same set, inside the minimum interval: nothing.
        clock.now += 59
        executor.observe()
        assert len(messages) == 1
        # Heartbeat at 60 s, then 120 s, then 240 s (the cap).
        clock.now += 2
        executor.observe()
        assert len(messages) == 2
        assert 'unchanged for 61s; next report in 120s' in messages[1]
        clock.now += 100
        executor.observe()
        assert len(messages) == 2
        clock.now += 25
        executor.observe()
        assert len(messages) == 3
        assert 'next report in 240s' in messages[2]
        clock.now += 245
        executor.observe()
        assert len(messages) == 4
        assert 'next report in 240s' in messages[3]  # capped
        # A new thread crossing the threshold dumps at once (past the
        # minimum interval) and resets the backoff.
        fut_b = executor.submit(blocking_task, release_b)
        clock.now += 61
        assert executor.observe()[0] == 2
        assert len(messages) == 5
        assert 'unchanged' not in messages[4]
        assert '2 thread(s) running longer' in messages[4]
        clock.now += 61
        executor.observe()
        assert len(messages) == 6
        assert 'next report in 120s' in messages[5]
        # Everything returns: state resets.
        release_a.set()
        release_b.set()
        assert fut_a.result(timeout=5) is True
        assert fut_b.result(timeout=5) is True
        assert _wait_for(lambda: executor.observe() == (0, 0.0))
        assert len(messages) == 6
    finally:
        release_a.set()
        release_b.set()
        executor.shutdown()


def test_exhaustion_dump_only_when_slot_holders_change(monkeypatch):
    """A saturated executor without a threshold dumps its oldest tasks once
    per distinct set of slot holders, not once per rejected submit."""
    clock = _fake_clock(monkeypatch)
    messages = _capture_warnings(monkeypatch)
    executor = OnDemandThreadExecutor(name='holders_test', max_workers=1)
    release_a, release_b = threading.Event(), threading.Event()
    try:
        fut_a = executor.submit(blocking_task, release_a)
        with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
            executor.submit(dummy_task)
        assert _wait_for(lambda: len(messages) == 1)
        assert 'all workers busy' in messages[0]
        assert 'test_threads.blocking_task' in messages[0]
        # Same holder, an hour later: nothing new to say.
        clock.now += 3600
        with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
            executor.submit(dummy_task)
        time.sleep(0.2)
        assert len(messages) == 1
        # A different holder: dumped again.
        release_a.set()
        assert fut_a.result(timeout=5) is True
        fut_b = executor.submit(blocking_task, release_b)
        clock.now += 61
        with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
            executor.submit(dummy_task)
        assert _wait_for(lambda: len(messages) == 2)
        release_b.set()
        assert fut_b.result(timeout=5) is True
    finally:
        release_a.set()
        release_b.set()
        executor.shutdown()


def test_dump_includes_rate_limited_ownerless_socket_scan(monkeypatch):
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(threads_mod, '_last_scan', float('-inf'))
    pooler, database = ('127.0.0.1', 6432), ('db.example.internal', 5432)
    monkeypatch.setattr(threads_mod, '_db_peers',
                        threads_mod._DbPeers([pooler], [database]))  # pylint: disable=protected-access
    calls = []

    def fake_scan(extra_peers=(), exclude_peers=()):
        calls.append((clock.now, list(extra_peers), list(exclude_peers)))
        return ['ownerless sockets: 1 ...', '  socket:[1] a -> b']

    monkeypatch.setattr(hung_threads, 'scan_ownerless_sockets', fake_scan)
    messages = _capture_warnings(monkeypatch)
    executor = OnDemandThreadExecutor(name='scan_test',
                                      max_workers=2,
                                      stuck_after_seconds=1.0)
    release_a, release_b = threading.Event(), threading.Event()
    try:
        executor.submit(blocking_task, release_a)
        clock.now += 2
        executor.observe()
        assert len(messages) == 1 and 'ownerless sockets: 1' in messages[0]
        # The scan looks at the pooler and leaves the database behind it out.
        assert calls == [(1002.0, [pooler], [database])]
        # A second dump inside the scan interval carries no scan.
        executor.submit(blocking_task, release_b)
        clock.now += 61
        executor.observe()
        assert len(messages) == 2 and 'ownerless' not in messages[1]
        assert len(calls) == 1
        # Past the scan interval it runs again.
        release_a.set()
        clock.now += 300
        executor.observe()
        assert len(messages) == 3 and 'ownerless' in messages[2]
        assert [c[0] for c in calls] == [1002.0, 1363.0]
    finally:
        release_a.set()
        release_b.set()
        executor.shutdown()


def test_state_db_peers_from_environment(monkeypatch):
    """The scan looks at the endpoint this process connects to for state
    database work and leaves the database behind a pooler out: a sidecar
    pooler's own connections to it live in another process namespace and
    would all be reported as ownerless."""
    # pylint: disable=protected-access
    from sky.skylet import constants  # pylint: disable=import-outside-toplevel
    database = ('db.example.internal', 5433)
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    monkeypatch.setenv(constants.ENV_VAR_DB_CONNECTION_URI,
                       'postgresql://u:p@db.example.internal:5433/sky')

    # Pooler as host:port rewrite (the sidecar case).
    monkeypatch.setattr(threads_mod, '_db_peers', None)
    monkeypatch.setenv(constants.ENV_VAR_DB_POOL_HOSTPORT, '127.0.0.1:6432')
    peers = threads_mod._state_db_peers()
    assert peers.scan == [('127.0.0.1', 6432)]
    assert peers.skip == [database]

    # Pooler as a full connection URI.
    monkeypatch.setattr(threads_mod, '_db_peers', None)
    monkeypatch.delenv(constants.ENV_VAR_DB_POOL_HOSTPORT)
    monkeypatch.setenv(constants.ENV_VAR_DB_POOL_CONNECTION_URI,
                       'postgresql://u:p@pooler.example.internal:6432/sky')
    peers = threads_mod._state_db_peers()
    assert peers.scan == [('pooler.example.internal', 6432)]
    assert peers.skip == [database]

    # No pooler: the database itself is scanned, nothing is skipped.
    monkeypatch.setattr(threads_mod, '_db_peers', None)
    monkeypatch.delenv(constants.ENV_VAR_DB_POOL_CONNECTION_URI)
    peers = threads_mod._state_db_peers()
    assert peers.scan == [database]
    assert peers.skip == []

    # No database configured (SQLite): nothing to scan.
    monkeypatch.setattr(threads_mod, '_db_peers', None)
    monkeypatch.delenv(constants.ENV_VAR_DB_CONNECTION_URI)
    assert threads_mod._state_db_peers() == threads_mod._DbPeers([], [])
    monkeypatch.setattr(threads_mod, '_db_peers', None)


def test_saturated_submit_raises_exhausted_when_dump_thread_cannot_start(
        monkeypatch):
    """The exhaustion dump runs in a thread of its own. A process at its
    thread limit cannot start it; the caller must still get the executor's
    own saturation error, which is what every submit site handles, and not
    the RuntimeError of the failed start."""
    debug_messages = []
    monkeypatch.setattr(threads_mod.logger, 'debug',
                        lambda msg, *a, **k: debug_messages.append(str(msg)))
    real_start = threading.Thread.start

    def start_unless_dump(self):
        if self.name.endswith('-exhaustion-dump'):
            raise RuntimeError('cannot start new thread (simulated)')
        return real_start(self)

    monkeypatch.setattr(threading.Thread, 'start', start_unless_dump)
    executor = OnDemandThreadExecutor(name='nodump_test',
                                      max_workers=1,
                                      stuck_after_seconds=30.0)
    release = threading.Event()
    try:
        fut = executor.submit(blocking_task, release)
        with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
            executor.submit(dummy_task)
        assert any('could not start the dump thread' in m
                   for m in debug_messages), debug_messages
        release.set()
        assert fut.result(timeout=5) is True
    finally:
        release.set()
        executor.shutdown()


def test_executor_construction_survives_watchdog_start_failure(monkeypatch):
    """The first executor built in a process starts the watchdog thread. If
    the process cannot start it, the executor is still built and usable, and
    the next executor built tries again."""
    # pylint: disable=protected-access
    monkeypatch.setattr(threads_mod, '_watchdog_pid', None)
    monkeypatch.setattr(threads_mod, '_watchdog_loop', lambda: None)
    attempts = []
    real_start = threading.Thread.start

    def start_failing_once(self):
        if self.name == 'thread-executor-watchdog':
            attempts.append(self)
            if len(attempts) == 1:
                raise RuntimeError('cannot start new thread (simulated)')
        return real_start(self)

    monkeypatch.setattr(threading.Thread, 'start', start_failing_once)
    first = OnDemandThreadExecutor(name='wd_fail_a', max_workers=1)
    try:
        assert len(attempts) == 1
        assert threads_mod._watchdog_pid is None
        assert first.submit(dummy_task).result(timeout=5) is True
        second = OnDemandThreadExecutor(name='wd_fail_b', max_workers=1)
        second.shutdown()
        assert len(attempts) == 2
        assert threads_mod._watchdog_pid == os.getpid()
        attempts[1].join(5)
    finally:
        first.shutdown()


def test_snapshot_drops_dead_threads():
    """After a fork the child inherits the table but not the threads; a dead
    entry must not be reported as stuck forever."""
    executor = OnDemandThreadExecutor(name='dead_test',
                                      max_workers=2,
                                      stuck_after_seconds=0.01)
    try:
        ghost = threading.Thread(target=lambda: None)
        ghost.start()
        ghost.join()
        # pylint: disable=protected-access
        executor._threads[ghost] = threads_task(time.monotonic() - 100)
        assert executor.observe() == (0, 0.0)
        assert ghost not in executor._threads
        # pylint: enable=protected-access
    finally:
        executor.shutdown()


def threads_task(started):
    return threads_mod._Task(started, started + 0.01, 'ghost', 0)  # pylint: disable=protected-access


def test_describe_task_unwraps_context_run():
    import contextvars  # pylint: disable=import-outside-toplevel
    import functools  # pylint: disable=import-outside-toplevel

    # What context_utils.to_thread_with_executor submits.
    wrapped = functools.partial(contextvars.copy_context().run, dummy_task,
                                0.01)
    assert threads_mod._describe_task(wrapped) == (  # pylint: disable=protected-access
        f'{dummy_task.__module__}.dummy_task')
    assert threads_mod._describe_task(dummy_task).endswith('.dummy_task')  # pylint: disable=protected-access


def test_watchdog_thread_started_once():
    OnDemandThreadExecutor(name='wd_a', max_workers=1).shutdown()
    OnDemandThreadExecutor(name='wd_b', max_workers=1).shutdown()
    names = [t.name for t in threading.enumerate()]
    assert names.count('thread-executor-watchdog') == 1


def test_snapshot_keeps_registered_but_unstarted_thread(monkeypatch):
    """submit() registers the thread, then starts it. A snapshot landing in
    between (a watchdog tick or an exhaustion dump) sees is_alive() False
    and must not purge the entry, or the task runs untracked for life."""
    executor = OnDemandThreadExecutor(name='prestart_test',
                                      max_workers=2,
                                      stuck_after_seconds=0.05)
    seen = {}
    orig_start = threading.Thread.start

    def start_after_snapshot(self):
        if self.name.startswith('prestart_test-'):
            # pylint: disable-next=protected-access
            seen['tracked_during_start'] = len(executor._snapshot())
        return orig_start(self)

    monkeypatch.setattr(threading.Thread, 'start', start_after_snapshot)
    release = threading.Event()
    try:
        fut = executor.submit(blocking_task, release)
        assert seen['tracked_during_start'] == 1
        time.sleep(0.1)
        # Still tracked, and now past the threshold.
        assert executor.observe()[0] == 1
        release.set()
        assert fut.result(timeout=5) is True
        assert _wait_for(lambda: executor.observe() == (0, 0.0))
    finally:
        release.set()
        executor.shutdown()


class _Registry(weakref.WeakSet):
    """Stand-in for the module's executor set: iterates in name order (a set
    has none) and records whether the watchdog lock was held while doing so."""

    def __init__(self):
        super().__init__()
        self.locked_during_iteration = []

    def __iter__(self):
        # pylint: disable-next=protected-access
        self.locked_during_iteration.append(threads_mod._watchdog_lock.locked())
        return iter(sorted(super().__iter__(), key=lambda e: e.name))


def test_watchdog_tick_isolates_a_failing_executor(monkeypatch):
    """One executor whose observe() raises must not cost the others their
    observation, and the tick must return normally."""
    registry = _Registry()
    bad = OnDemandThreadExecutor(name='wd_a_bad', max_workers=1)
    good = OnDemandThreadExecutor(name='wd_b_good', max_workers=1)
    registry.add(bad)
    registry.add(good)
    monkeypatch.setattr(threads_mod, '_executors', registry)
    observed = []

    def failing_observe():
        raise RuntimeError('observe failed')

    monkeypatch.setattr(bad, 'observe', failing_observe)
    monkeypatch.setattr(good, 'observe', lambda: observed.append(1) or (0, 0.0))
    try:
        threads_mod._watchdog_tick()  # pylint: disable=protected-access
        threads_mod._watchdog_tick()  # pylint: disable=protected-access
    finally:
        bad.shutdown()
        good.shutdown()
    assert observed == [1, 1]
    # The set is copied under the lock that registration takes, so a
    # concurrent constructor cannot change it mid-iteration.
    assert registry.locked_during_iteration == [True, True]


def test_watchdog_loop_survives_failed_ticks_under_registration_churn(
        monkeypatch):
    """The real loop keeps ticking after ticks raised, while executors are
    built and dropped on another thread (the WeakSet changes size)."""
    monkeypatch.setattr(threads_mod, '_WATCHDOG_INTERVAL_SECONDS', 0.001)
    real_tick = threads_mod._watchdog_tick  # pylint: disable=protected-access
    state = {'calls': 0, 'good': 0}
    under_test = {}

    def tick():
        if threading.current_thread() is not under_test.get('thread'):
            # The process's own watchdog is not part of this test.
            return real_tick()
        state['calls'] += 1
        if state['calls'] <= 3:
            raise RuntimeError('Set changed size during iteration')
        real_tick()
        state['good'] += 1
        if state['good'] >= 100:
            # Ends this loop thread; threading ignores SystemExit silently.
            raise SystemExit
        return None

    monkeypatch.setattr(threads_mod, '_watchdog_tick', tick)
    stop = threading.Event()

    def churn():
        while not stop.is_set():
            OnDemandThreadExecutor(name='churn', max_workers=1).shutdown()

    churner = threading.Thread(target=churn, name='churn', daemon=True)
    loop = threading.Thread(
        target=threads_mod._watchdog_loop,  # pylint: disable=protected-access
        name='watchdog-under-test',
        daemon=True)
    under_test['thread'] = loop
    churner.start()
    loop.start()
    try:
        assert _wait_for(lambda: state['good'] >= 100, timeout=20)
    finally:
        stop.set()
        churner.join(timeout=5)
    loop.join(timeout=5)
    assert not loop.is_alive()
    assert state['calls'] >= 103


def test_watchdog_dump_floor_applies_to_a_newly_stuck_thread(monkeypatch):
    """A thread crossing the threshold within the minimum interval of the
    previous dump waits for the interval; this is the guard that bounds a
    ramp to one dump per minute per executor."""
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(threads_mod, '_DUMP_MIN_INTERVAL_SECONDS', 60.0)
    messages = _capture_warnings(monkeypatch)
    executor = OnDemandThreadExecutor(name='floor_test',
                                      max_workers=4,
                                      stuck_after_seconds=30.0)
    release = threading.Event()
    try:
        fut_a = executor.submit(blocking_task, release)
        clock.now += 31  # A crosses the threshold and is dumped.
        assert executor.observe()[0] == 1
        assert len(messages) == 1
        fut_b = executor.submit(blocking_task, release)
        clock.now += 31  # B crosses; only 31 s since the last dump.
        assert executor.observe()[0] == 2
        assert len(messages) == 1
        clock.now += 30  # 61 s since the last dump: B is reported.
        executor.observe()
        assert len(messages) == 2
        assert '2 thread(s) running longer' in messages[1]
        assert 'unchanged' not in messages[1]
        release.set()
        assert fut_a.result(timeout=5) is True
        assert fut_b.result(timeout=5) is True
    finally:
        release.set()
        executor.shutdown()


def test_exhaustion_dump_thread_started_at_most_once_per_second(monkeypatch):
    """A saturated executor rejects every submit; the reject path must not
    start a dump thread per rejection once the picture is unchanged."""
    clock = _fake_clock(monkeypatch)
    messages = _capture_warnings(monkeypatch)
    executor = OnDemandThreadExecutor(name='throttle_test', max_workers=1)
    release = threading.Event()
    snapshots = []
    real_snapshot = executor._snapshot  # pylint: disable=protected-access

    def counting_snapshot():
        snapshots.append(threading.current_thread().name)
        return real_snapshot()

    def dump_thread_snapshots():
        return [n for n in snapshots if n.endswith('-exhaustion-dump')]

    monkeypatch.setattr(executor, '_snapshot', counting_snapshot)

    def reject(n):
        for _ in range(n):
            with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
                executor.submit(dummy_task)

    try:
        fut = executor.submit(blocking_task, release)
        reject(50)
        assert _wait_for(lambda: len(messages) == 1)
        time.sleep(0.1)
        assert len(dump_thread_snapshots()) == 1
        # Past the dump floor, same slot holder: every reject passes the
        # floor check, but only the first in a second starts a thread.
        clock.now += 61
        reject(50)
        assert _wait_for(lambda: len(dump_thread_snapshots()) == 2)
        time.sleep(0.1)
        assert len(dump_thread_snapshots()) == 2
        assert len(messages) == 1
        # The next second lets another look happen.
        clock.now += 1
        reject(1)
        assert _wait_for(lambda: len(dump_thread_snapshots()) == 3)
        assert len(messages) == 1
        release.set()
        assert fut.result(timeout=5) is True
    finally:
        release.set()
        executor.shutdown()
