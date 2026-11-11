"""Unit tests for sky/server/requests/threads.py."""
# pylint: disable=protected-access

import asyncio
import concurrent.futures
import os
import queue
import threading
import time
from unittest import mock

import prometheus_client
import pytest

from sky import exceptions
from sky.metrics import utils as metrics_utils
from sky.server.requests import threads
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


def test_on_demand_executor_reuses_threads():
    executor = OnDemandThreadExecutor(name='test', max_workers=4)
    try:
        q = queue.Queue()
        for _ in range(100):
            assert executor.submit(thread_name_task, q).result(timeout=5)
        names = {q.get(timeout=5) for _ in range(100)}
        assert len(names) == 1
        assert len(executor._threads) == 1
        assert executor.running.get() == 0
    finally:
        executor.shutdown()


def test_on_demand_executor_prestart():
    executor = OnDemandThreadExecutor(name='test', max_workers=4)
    try:
        executor.prestart(2)
        assert len(executor._threads) == 2
        executor.prestart(10)
        assert len(executor._threads) == 4
        started = threading.Thread.start
        with mock.patch.object(threading.Thread,
                               'start',
                               autospec=True,
                               side_effect=started) as start:
            release_event = threading.Event()
            futs = [
                executor.submit(blocking_task, release_event) for _ in range(4)
            ]
            release_event.set()
            assert all(f.result(timeout=5) for f in futs)
            for _ in range(100):
                assert executor.submit(dummy_task, 0).result(timeout=5)
        start.assert_not_called()
        assert len(executor._threads) == 4
    finally:
        executor.shutdown()


def test_on_demand_executor_queue_when_full():
    executor = OnDemandThreadExecutor(name='test',
                                      max_workers=2,
                                      queue_when_full=True)
    release_event = threading.Event()
    try:
        futs = [executor.submit(blocking_task, release_event) for _ in range(5)]
        assert len(executor._threads) == 2
        assert not any(f.done() for f in futs)
        release_event.set()
        assert all(f.result(timeout=5) for f in futs)
        assert len(executor._threads) == 2
        assert executor.running.get() == 0
    finally:
        release_event.set()
        executor.shutdown()


def test_on_demand_executor_skips_cancelled_queued_task():
    executor = OnDemandThreadExecutor(name='test',
                                      max_workers=1,
                                      queue_when_full=True)
    release_event = threading.Event()
    ran = threading.Event()
    try:
        first = executor.submit(blocking_task, release_event)
        queued = executor.submit(ran.set)
        assert queued.cancel()
        release_event.set()
        assert first.result(timeout=5) is True
        # Runs after the cancelled task in FIFO order.
        assert executor.submit(dummy_task, 0).result(timeout=5) is True
        assert not ran.is_set()
        assert executor.running.get() == 0
    finally:
        release_event.set()
        executor.shutdown()


def test_on_demand_executor_asyncio_cancel_while_running():
    executor = OnDemandThreadExecutor(name='test', max_workers=1)
    started = threading.Event()
    release_event = threading.Event()

    def task():
        started.set()
        return blocking_task(release_event)

    async def main():
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(executor, task)
        await loop.run_in_executor(None, started.wait, 5)
        fut.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fut
        # The thread still runs the task and holds its slot.
        assert executor.running.get() == 1
        with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
            executor.submit(dummy_task)

    try:
        asyncio.run(main())
        release_event.set()
        deadline = time.time() + 5
        while executor.running.get() and time.time() < deadline:
            time.sleep(0.01)
        assert executor.running.get() == 0
        assert executor.submit(dummy_task, 0).result(timeout=5)
    finally:
        release_event.set()
        executor.shutdown()


def test_on_demand_executor_shutdown_waits_and_stops_threads():
    executor = OnDemandThreadExecutor(name='test', max_workers=3)
    executor.prestart(3)
    release_event = threading.Event()
    fut = executor.submit(blocking_task, release_event)
    threading.Timer(0.2, release_event.set).start()
    executor.shutdown(wait=True)
    assert fut.done() and fut.result() is True
    assert not any(t.is_alive() for t in executor._threads)
    executor.prestart(1)
    assert not any(t.is_alive() for t in executor._threads)


def test_on_demand_executor_threads_are_daemons():
    executor = OnDemandThreadExecutor(name='test', max_workers=1)
    try:
        executor.prestart(1)
        assert all(t.daemon for t in executor._threads)
    finally:
        executor.shutdown()


def test_count_thread_starts(monkeypatch):
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)
    monkeypatch.setattr(threads, '_thread_start_counter_installed', False)
    monkeypatch.setattr(threading.Thread, 'start', threading.Thread.start)

    def sample(on_loop: str) -> float:
        return prometheus_client.REGISTRY.get_sample_value(
            'sky_apiserver_threads_started_total', {'on_loop': on_loop}) or 0

    threads.count_thread_starts()
    threads.count_thread_starts()
    before_on, before_off = sample('true'), sample('false')

    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()

    async def start_on_loop():
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()

    asyncio.run(start_on_loop())
    assert sample('false') >= before_off + 1
    assert sample('true') == before_on + 1


def test_keep_anyio_worker_threads(monkeypatch):
    # pylint: disable=import-outside-toplevel
    from anyio._backends import _asyncio as anyio_asyncio
    monkeypatch.setattr(anyio_asyncio.WorkerThread, 'MAX_IDLE_TIME', 10)
    threads.keep_anyio_worker_threads()
    assert anyio_asyncio.WorkerThread.MAX_IDLE_TIME == (
        threads._ANYIO_WORKER_MAX_IDLE_SECONDS)


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
        # Counted once a thread starts running the task.
        deadline = time.time() + 5
        while (_gauge_value('sky_apiserver_threads_active', name) < 2 and
               time.time() < deadline):
            time.sleep(0.01)
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
        assert _gauge_value('sky_apiserver_threads_active', name) == 0
    finally:
        executor.shutdown()
