"""Test for sky.utils.timeline."""

import contextvars
import json
import os
import threading
from unittest import mock

import pytest

from sky.utils import timeline


@pytest.fixture(autouse=True)
def _reset_timeline_state():
    """Reset timeline module state between tests."""
    timeline._process_events.clear()
    # Reset context vars to defaults.
    token_events = timeline._context_events.set(None)
    token_rid = timeline._context_request_id.set(None)
    token_path = timeline._context_save_path.set(None)
    yield
    timeline._process_events.clear()
    timeline._context_events.reset(token_events)
    timeline._context_request_id.reset(token_rid)
    timeline._context_save_path.reset(token_path)


def test_save_timeline_process_global(tmp_path):
    """Events go to the process-global list and are saved correctly."""
    out = str(tmp_path / 'timeline.json')

    @timeline.event('test_save_timeline')
    def traced_fn():
        pass

    with mock.patch.dict(os.environ, {'SKYPILOT_TIMELINE_FILE_PATH': out}):
        traced_fn()
        assert len(timeline._process_events) == 2
        timeline.save_timeline()
        assert len(timeline._process_events) == 0
        data = json.loads(open(out).read())
        assert len(data['traceEvents']) == 2

    # With no file path set, events should not be recorded.
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop('SKYPILOT_TIMELINE_FILE_PATH', None)
        traced_fn()
        assert len(timeline._process_events) == 0


def test_request_scoped_isolation(tmp_path):
    """Events in different request contexts are isolated."""
    path_a = str(tmp_path / 'a.json')
    path_b = str(tmp_path / 'b.json')

    # Simulate two request contexts running in the same process.
    def run_in_context(request_id, save_path, event_name):
        ctx = contextvars.copy_context()

        def inner():
            timeline.init_request_timeline(request_id, save_path)
            with timeline.Event(event_name):
                pass
            timeline.save_timeline()

        ctx.run(inner)

    run_in_context('req-aaa', path_a, 'event_a')
    run_in_context('req-bbb', path_b, 'event_b')

    data_a = json.loads(open(path_a).read())
    data_b = json.loads(open(path_b).read())

    # Each file should only contain its own events.
    assert all(e['name'] == 'event_a' for e in data_a['traceEvents'])
    assert all(e['name'] == 'event_b' for e in data_b['traceEvents'])
    # Events should be tagged with their request_id.
    assert all(
        e.get('args', {}).get('request_id') == 'req-aaa'
        for e in data_a['traceEvents'])
    assert all(
        e.get('args', {}).get('request_id') == 'req-bbb'
        for e in data_b['traceEvents'])


def test_thread_safety(tmp_path):
    """Concurrent appends from multiple threads do not lose events."""
    out = str(tmp_path / 'threaded.json')
    n_threads = 10
    n_events_per_thread = 50

    with mock.patch.dict(os.environ, {'SKYPILOT_TIMELINE_FILE_PATH': out}):
        barrier = threading.Barrier(n_threads)

        def worker():
            barrier.wait()
            for i in range(n_events_per_thread):
                with timeline.Event(f'thread_event_{i}'):
                    pass

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each event produces 2 entries (begin + end).
        expected = n_threads * n_events_per_thread * 2
        assert len(timeline._process_events) == expected
        timeline.save_timeline()
        assert len(timeline._process_events) == 0

        data = json.loads(open(out).read())
        assert len(data['traceEvents']) == expected


def test_add_events():
    """add_events() appends external events to the current list."""
    with mock.patch.dict(os.environ,
                         {'SKYPILOT_TIMELINE_FILE_PATH': '/tmp/x.json'}):
        with timeline.Event('local'):
            pass
        assert len(timeline._process_events) == 2
        timeline.add_events([{'name': 'server_event', 'ph': 'X'}])
        assert len(timeline._process_events) == 3


def test_set_request_id():
    """set_request_id tags subsequent events with request_id."""
    with mock.patch.dict(os.environ,
                         {'SKYPILOT_TIMELINE_FILE_PATH': '/tmp/x.json'}):
        timeline.set_request_id('my-request-id')
        with timeline.Event('tagged'):
            pass
        events = timeline._process_events
        assert len(events) == 2
        for e in events:
            assert e['args']['request_id'] == 'my-request-id'


def test_concurrent_coroutine_requests_isolated(tmp_path):
    """Simulate two coroutine-executor requests running concurrently.

    This mirrors the actual server code path:
      _execute_request_coroutine
        -> to_thread_with_executor (contextvars.copy_context().run)
          -> override_request_env_and_config
            -> init_request_timeline
            -> func()  # actual work with @timeline.event calls
            -> save_timeline()

    Two requests must not see each other's events even when they overlap
    in the same thread pool.
    """
    path_a = str(tmp_path / 'a.json')
    path_b = str(tmp_path / 'b.json')
    n_events = 20

    # Barriers ensure both threads are actively recording at the same time.
    both_started = threading.Barrier(2)
    both_recorded = threading.Barrier(2)

    errors: list = []

    def simulate_request(request_id, save_path, event_prefix):
        """Runs inside copy_context().run() — just like the real executor."""
        try:
            timeline.init_request_timeline(request_id, save_path)
            both_started.wait(timeout=5)

            for i in range(n_events):
                with timeline.Event(f'{event_prefix}_{i}'):
                    pass

            both_recorded.wait(timeout=5)
            timeline.save_timeline()
        except Exception as e:  # pylint: disable=broad-except
            errors.append(e)

    def thread_target(request_id, save_path, event_prefix):
        # Each thread gets its own context copy — same as
        # context_utils.to_thread_with_executor.
        ctx = contextvars.copy_context()
        ctx.run(simulate_request, request_id, save_path, event_prefix)

    t_a = threading.Thread(target=thread_target,
                           args=('req-A', path_a, 'event_a'))
    t_b = threading.Thread(target=thread_target,
                           args=('req-B', path_b, 'event_b'))
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    assert not errors, f'Thread errors: {errors}'

    data_a = json.load(open(path_a))
    data_b = json.load(open(path_b))

    events_a = data_a['traceEvents']
    events_b = data_b['traceEvents']

    # Each request should have exactly its own events (begin + end per event).
    assert len(events_a) == n_events * 2
    assert len(events_b) == n_events * 2

    # No cross-contamination: every event in A's file must be from A.
    for e in events_a:
        assert e['name'].startswith('event_a'), (
            f'Request A file contains foreign event: {e["name"]}')
        assert e['args']['request_id'] == 'req-A', (
            f'Request A file has wrong request_id: {e["args"]}')

    for e in events_b:
        assert e['name'].startswith('event_b'), (
            f'Request B file contains foreign event: {e["name"]}')
        assert e['args']['request_id'] == 'req-B', (
            f'Request B file has wrong request_id: {e["args"]}')

    # Process-global list should be untouched.
    assert len(timeline._process_events) == 0


def test_disabled_tracing_no_events():
    """When tracing is disabled, events are not recorded."""
    # Ensure no env var and no context save path.
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop('SKYPILOT_TIMELINE_FILE_PATH', None)
        with timeline.Event('should_skip'):
            pass
        assert len(timeline._process_events) == 0
