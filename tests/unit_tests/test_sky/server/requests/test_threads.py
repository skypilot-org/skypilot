"""Unit tests for sky/server/requests/threads.py."""

import concurrent.futures
import os
import queue
import threading
import time

import prometheus_client
import pytest

from sky import exceptions
from sky.metrics import utils as metrics_utils
from sky.server.requests.threads import OnDemandThreadExecutor
from sky.server.requests.threads import PooledThreadExecutor


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


@pytest.fixture(params=[OnDemandThreadExecutor, PooledThreadExecutor])
def executor_cls(request):
    return request.param


def _wait_running_zero(executor) -> None:
    # The worker releases the slot just after resolving the future.
    deadline = time.time() + 5
    while executor.running.get() != 0 and time.time() < deadline:
        time.sleep(0.01)
    assert executor.running.get() == 0


def test_executor_submit_and_result(executor_cls):
    executor = executor_cls(name='test', max_workers=2)
    try:
        futs = [executor.submit(dummy_task), executor.submit(dummy_task)]
        done, not_done = concurrent.futures.wait(
            futs, timeout=5, return_when=concurrent.futures.ALL_COMPLETED)
        assert not not_done
        assert all(f.result() is True for f in done)
        _wait_running_zero(executor)
    finally:
        executor.shutdown()


def test_executor_exception_propagation(executor_cls):
    executor = executor_cls(name='test', max_workers=1)
    try:
        fut = executor.submit(failing_task)
        with pytest.raises(ValueError):
            fut.result(timeout=5)
        # running should be back to 0 even on exception
        _wait_running_zero(executor)
    finally:
        executor.shutdown()


def test_executor_concurrency_limit(executor_cls):
    executor = executor_cls(name='test', max_workers=2)
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
        _wait_running_zero(executor)
        # Slots freed by finished tasks are admitted again.
        assert executor.submit(dummy_task, 0).result(timeout=5) is True
    finally:
        release_event.set()
        executor.shutdown()


def test_executor_check_available_borrow_semantics(executor_cls):
    executor = executor_cls(name='test', max_workers=1)
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


def test_executor_shutdown_prevents_submit(executor_cls):
    executor = executor_cls(name='test', max_workers=1)
    executor.shutdown()
    with pytest.raises(RuntimeError):
        executor.submit(dummy_task)
    assert executor.running.get() == 0


def test_executor_thread_naming(executor_cls):
    executor = executor_cls(name='myexec', max_workers=1)
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


def test_pooled_executor_reuses_threads():
    executor = PooledThreadExecutor(name='pooled', max_workers=2)
    try:
        idents = set()
        for _ in range(10):
            idents.add(executor.submit(threading.get_ident).result(timeout=5))
            _wait_running_zero(executor)
        assert len(idents) == 1
    finally:
        executor.shutdown()


def test_pooled_executor_threads_are_daemons():
    executor = PooledThreadExecutor(name='pooled', max_workers=1)
    try:
        assert executor.submit(
            lambda: threading.current_thread().daemon).result(timeout=5)
    finally:
        executor.shutdown()


def test_pooled_executor_never_exceeds_max_threads():
    executor = PooledThreadExecutor(name='pooled', max_workers=4)
    try:
        for _ in range(50):
            futs = []
            for _ in range(4):
                try:
                    futs.append(executor.submit(dummy_task, 0.001))
                except exceptions.ConcurrentWorkerExhaustedError:
                    pass
            concurrent.futures.wait(futs, timeout=5)
        _wait_running_zero(executor)
        assert executor._num_threads <= 4  # pylint: disable=protected-access
    finally:
        executor.shutdown()


def _gauge_value(name: str, executor_name: str) -> float:
    value = prometheus_client.REGISTRY.get_sample_value(name, {
        'pid': str(os.getpid()),
        'name': executor_name
    })
    assert value is not None
    return value


def test_executor_metrics(monkeypatch, executor_cls):
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)
    name = f'metrics_test_{executor_cls.__name__}'
    executor = executor_cls(name=name, max_workers=2)
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
        _wait_running_zero(executor)
        assert _gauge_value('sky_apiserver_threads_active', name) == 0
    finally:
        release_event.set()
        executor.shutdown()
