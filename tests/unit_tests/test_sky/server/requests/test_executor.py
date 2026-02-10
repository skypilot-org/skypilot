"""Unit tests for sky.server.requests.executor module."""
import asyncio
import concurrent.futures
import functools
import os
import queue as queue_lib
import time
from typing import List
from unittest import mock

import pytest

from sky import exceptions
from sky import skypilot_config
from sky.server import config as server_config
from sky.server import constants as server_constants
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import process
from sky.server.requests import requests as requests_lib
from sky.skylet import constants
from sky.utils import context_utils


@pytest.fixture()
def isolated_database(tmp_path):
    """Create an isolated DB and logs directory per-test."""
    temp_db_path = tmp_path / 'requests.db'
    temp_log_path = tmp_path / 'logs'
    temp_log_path.mkdir()

    with mock.patch('sky.server.constants.API_SERVER_REQUEST_DB_PATH',
                    str(temp_db_path)):
        with mock.patch('sky.server.requests.requests.REQUEST_LOG_PATH_PREFIX',
                        str(temp_log_path)):
            requests_lib._DB = None
            yield
            requests_lib._DB = None


@pytest.fixture()
def mock_skypilot_config(tmp_path):
    config_content = """
aws:
  labels:
    key: value
  vpc_name: skypilot-vpc
"""
    config_file = tmp_path / 'config.yaml'
    config_file.write_text(config_content)

    with mock.patch('sky.skypilot_config._GLOBAL_CONFIG_PATH',
                    str(config_file)):
        skypilot_config.reload_config()
        yield str(config_file)
        # Reload config again to restore original state
        skypilot_config.reload_config()


@pytest.fixture()
def mock_global_user_state():
    mock_user = mock.Mock()
    mock_user.id = 'test-user-id'
    mock_user.name = 'test-user'

    def mock_add_or_update_user(user,
                                allow_duplicate_name=True,
                                return_user=False):
        if return_user:
            return True, mock_user
        return True

    with mock.patch('sky.global_user_state.add_or_update_user',
                    side_effect=mock_add_or_update_user), \
         mock.patch('sky.global_user_state.get_user') as mock_get_user:
        mock_get_user.return_value = mock_user
        yield


@pytest.fixture()
def hijacked_sys_attrs():
    """Hijack os.environ and sys.stdout/stderr to be context-aware."""
    # Save original values
    original_environ = os.environ
    original_stdout = __import__('sys').stdout
    original_stderr = __import__('sys').stderr

    # Hijack
    context_utils.hijack_sys_attrs()

    yield

    # Restore (for test isolation)
    import sys
    setattr(os, 'environ', original_environ)
    setattr(sys, 'stdout', original_stdout)
    setattr(sys, 'stderr', original_stderr)


def dummy_entrypoint(*args, **kwargs):
    """Dummy entrypoint function for testing."""
    time.sleep(2)
    return 'success'


@pytest.mark.asyncio
async def test_execute_request_coroutine_ctx_cancelled_on_cancellation(
        isolated_database):
    """Test that context is always cancelled when execute_request_coroutine
    is cancelled."""
    # Create a mock request
    request = requests_lib.Request(
        request_id='test-request-id',
        name='test-request-name',
        status=requests_lib.RequestStatus.PENDING,
        created_at=time.time(),
        user_id='test-user-id',
        entrypoint=dummy_entrypoint,
        request_body=payloads.RequestBody(),
    )
    await requests_lib.create_if_not_exists_async(request)

    # Mock the context and its methods
    mock_ctx = mock.Mock()
    mock_ctx.is_canceled.return_value = False

    with mock.patch('sky.utils.context.initialize'), \
         mock.patch('sky.utils.context.get', return_value=mock_ctx):

        task = executor.execute_request_in_coroutine(request)

        await asyncio.sleep(0.1)
        task.cancel()
        await task.task
        # Verify the context is actually cancelled
        mock_ctx.cancel.assert_called()


CALLED_FLAG = [False]


def dummy_entrypoint(called_flag):
    CALLED_FLAG[0] = True
    return 'ok'


@pytest.mark.asyncio
async def test_api_cancel_race_condition(isolated_database):
    """Cancel before execution: wrapper must no-op and not run entrypoint."""
    CALLED_FLAG[0] = False
    req = requests_lib.Request(request_id='race-cancel-before',
                               name='test-request',
                               entrypoint=dummy_entrypoint,
                               request_body=payloads.RequestBody(),
                               status=requests_lib.RequestStatus.PENDING,
                               created_at=0.0,
                               user_id='test-user')

    assert await requests_lib.create_if_not_exists_async(req) is True

    # Cancel the request before the executor starts.
    cancelled = await requests_lib.kill_request_async('race-cancel-before')
    assert cancelled is True

    # Execute wrapper should detect CANCELLED and return immediately.
    executor._request_execution_wrapper('race-cancel-before',
                                        ignore_return_value=False)

    # Verify entrypoint was not invoked and status remains CANCELLED.
    assert CALLED_FLAG[0] is False
    updated = requests_lib.get_request('race-cancel-before')
    assert updated is not None
    assert updated.status == requests_lib.RequestStatus.CANCELLED


def _test_isolation_worker_fn(expected_env_a: str, expected_env_b: str,
                              expected_labels: dict, **kwargs):
    """Worker that verifies it sees the correct env vars and config overrides."""
    # TEST_VAR_A: Should see the overridden value (not main process value)
    assert os.environ.get('TEST_VAR_A') == expected_env_a, (
        f"Expected TEST_VAR_A={expected_env_a}, got {os.environ.get('TEST_VAR_A')}"
    )

    # TEST_VAR_B: Should see the new value set by this request
    assert os.environ.get('TEST_VAR_B') == expected_env_b, (
        f"Expected TEST_VAR_B={expected_env_b}, got {os.environ.get('TEST_VAR_B')}"
    )

    # Assert config override works - should see the overridden value, not the base.
    actual_labels = skypilot_config.get_nested(('aws', 'labels'),
                                               default_value={})
    assert actual_labels == expected_labels, (
        f"Expected labels={expected_labels}, got {actual_labels}")

    # Assert base config from mock_skypilot_config is still there.
    assert skypilot_config.get_nested(('aws', 'vpc_name'),
                                      default_value=None) == 'skypilot-vpc'

    return


def _subprocess_initializer(db_path: str, log_path: str, config_path: str):
    """Initializer for subprocess workers to set up the same mocks as the main test process."""
    from sky import skypilot_config
    from sky.server import constants as server_constants
    from sky.server.requests import requests as requests_lib

    server_constants.API_SERVER_REQUEST_DB_PATH = db_path
    requests_lib.REQUEST_LOG_PATH_PREFIX = log_path
    requests_lib._DB = None
    skypilot_config._GLOBAL_CONFIG_PATH = config_path


class TestIsolationBody(payloads.RequestBody):
    """Request body for isolation tests with expected values."""
    expected_env_a: str
    expected_env_b: str
    expected_labels: dict


@pytest.mark.parametrize('execution_mode', ['coroutine', 'process_executor'])
@pytest.mark.asyncio
async def test_execute_with_isolated_env_and_config(isolated_database,
                                                    mock_global_user_state,
                                                    mock_skypilot_config,
                                                    hijacked_sys_attrs,
                                                    execution_mode, tmp_path):
    """Test that multiple concurrent requests see independent env vars and config.

    Args:
        execution_mode: Either 'coroutine' (for execute_request_in_coroutine)
                       or 'process_executor' (for _request_execution_wrapper).
    """
    proc_executor = None
    if execution_mode == 'process_executor':
        # Get the paths that were set up by the fixtures, as we need to re-apply
        # the same patches in the subprocess created by the BurstableExecutor.
        db_path = server_constants.API_SERVER_REQUEST_DB_PATH
        log_path_prefix = requests_lib.REQUEST_LOG_PATH_PREFIX

        proc_executor = process.BurstableExecutor(
            garanteed_workers=5,
            burst_workers=0,
            initializer=_subprocess_initializer,
            initargs=(db_path, log_path_prefix, mock_skypilot_config))

    # Set TEST_VAR_A in main process that workers will override
    os.environ['TEST_VAR_A'] = 'init'

    # Capture main process state before spawning any workers to verify no leakage.
    env_before = dict(os.environ)
    config_before = skypilot_config.to_dict()

    async def spawn_request(request_id: str, env_a: str, env_b: str):
        """Spawn a request with env and config overrides."""
        expected_labels = {
            'key': 'value',  # From mock_skypilot_config
            'request_id': request_id,  # From override_skypilot_config
        }
        request_body = TestIsolationBody(
            env_vars={
                'TEST_VAR_A': env_a,  # Override env var from main process
                'TEST_VAR_B': env_b,  # New env var
                constants.USER_ID_ENV_VAR: f'user-{request_id}',
                constants.USER_ENV_VAR: f'user-{request_id}',
            },
            override_skypilot_config={
                'aws': {
                    'labels': {
                        'request_id': request_id,
                    }
                }
            },
            # Expected values to assert from inside the worker's context.
            expected_env_a=env_a,
            expected_env_b=env_b,
            expected_labels=expected_labels)

        request = await executor.prepare_request_async(
            request_id=request_id,
            request_name='test.isolation',
            request_body=request_body,
            func=_test_isolation_worker_fn,
            schedule_type=requests_lib.ScheduleType.SHORT)

        if execution_mode == 'coroutine':
            task = executor.execute_request_in_coroutine(request)
            await task.task
        else:
            # Submit to shared executor like production code
            fut = proc_executor.submit_until_success(
                executor._request_execution_wrapper, request_id, False)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, fut.result)

        completed_request = requests_lib.get_request(request_id)
        assert completed_request is not None
        if completed_request.status != requests_lib.RequestStatus.SUCCEEDED:
            error_info = completed_request.get_error()
            error_msg = f"Request {request_id} failed (mode={execution_mode}), status={completed_request.status}"
            if error_info:
                error_msg += f"\nError type: {error_info['type']}\nError message: {error_info['message']}\nError traceback:\n{error_info.get('traceback', 'N/A')}"
            else:
                error_msg += "\nNo error info available"
            pytest.fail(error_msg)
        return completed_request.get_return_value()

    # Spawn 5 concurrent requests with different env vars
    tasks = [
        spawn_request('1', 'a', 'one'),
        spawn_request('2', 'b', 'two'),
        spawn_request('3', 'c', 'three'),
        spawn_request('4', 'd', 'four'),
        spawn_request('5', 'e', 'five'),
    ]

    try:
        results = await asyncio.gather(*tasks)
        assert len(results) == 5

        # Verify main process state is unchanged.
        env_after = dict(os.environ)
        config_after = skypilot_config.to_dict()

        assert env_after == env_before, (
            "Environment leaked into main process! "
            f"Added: {set(env_after.keys()) - set(env_before.keys())}, "
            f"Removed: {set(env_before.keys()) - set(env_after.keys())}, "
            f"Changed: {[k for k in env_before if env_before.get(k) != env_after.get(k)]}"
        )

        assert config_after == config_before, (
            "Config leaked into main process! "
            f"Before: {config_before}, After: {config_after}")
    finally:
        # Shutdown the executor if we created one
        if proc_executor is not None:
            proc_executor.shutdown()
        os.environ.pop('TEST_VAR_A', None)


FAKE_FD_START = 100000


def _get_saved_fd_close_count(close_calls: List[int], created_fds: set) -> int:
    """Get the number of close() calls for file descriptors we created."""
    return sum(1 for fd in close_calls if fd in created_fds)


def _assert_no_double_close(close_calls: List[int], created_fds: set) -> None:
    """Verify that no file descriptor was closed more than once."""
    from collections import Counter
    our_close_calls = [fd for fd in close_calls if fd in created_fds]
    close_counts = Counter(our_close_calls)
    duplicates = {fd: count for fd, count in close_counts.items() if count > 1}
    assert not duplicates, (
        f'File descriptors closed multiple times (double-close bug!): '
        f'{duplicates}. Our FD close calls: {our_close_calls}, '
        f'All close calls: {close_calls}')


@pytest.fixture
def mock_fd_operations(isolated_database, monkeypatch):
    """Fixture that mocks all file descriptor operations for testing."""
    import os
    import pathlib
    import sys

    dup_calls = []
    dup2_calls = []
    close_calls = []
    created_fds = set()
    next_fake_fd = FAKE_FD_START

    def mock_dup(fd):
        """Mock os.dup() - saves fd and returns a unique fake fd."""
        nonlocal next_fake_fd
        dup_calls.append(fd)
        fake_fd = next_fake_fd
        created_fds.add(fake_fd)
        next_fake_fd += 1
        return fake_fd

    def mock_dup2(fd, fd2):
        """Mock os.dup2() - just records the call."""
        dup2_calls.append((fd, fd2))

    def mock_close(fd):
        """Mock os.close() - records which fds are being closed."""
        close_calls.append(fd)

    monkeypatch.setattr(os, 'dup', mock_dup)
    monkeypatch.setattr(os, 'dup2', mock_dup2)
    monkeypatch.setattr(os, 'close', mock_close)

    monkeypatch.setattr(sys.stdout, 'fileno', lambda: 1)
    monkeypatch.setattr(sys.stderr, 'fileno', lambda: 2)

    # Mock pathlib.Path.open() to return a mock file object with fileno()
    # This is needed because the executor opens a log file and redirects stdout/stderr to it.
    class MockFile:

        def __init__(self):
            self.closed = False

        def fileno(self):
            # Random large fd to not conflict with our mock fds.
            return 999

        def write(self, data):
            pass

        def flush(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

    monkeypatch.setattr(pathlib.Path, 'open',
                        lambda self, *args, **kwargs: MockFile())

    monkeypatch.setattr(
        'sky.server.requests.executor.subprocess_utils.kill_children_processes',
        lambda: None)

    return {
        'dup_calls': dup_calls,
        'dup2_calls': dup2_calls,
        'close_calls': close_calls,
        'created_fds': created_fds,
        'dup_count': lambda: len(dup_calls),
        'close_count': lambda: len(close_calls),
    }


def _success_entrypoint():
    return 'success'


def _retryable_error_entrypoint():
    from sky import exceptions
    raise exceptions.ExecutionRetryableError('Simulated retryable error',
                                             hint='This is a test',
                                             retry_wait_seconds=0)


def _general_error_entrypoint():
    raise ValueError('Simulated error')


def _keyboard_interrupt_entrypoint():
    raise KeyboardInterrupt()


def _dummy_entrypoint_for_retry_test():
    """Dummy entrypoint for retry test that can be pickled."""
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize('test_case', [
    pytest.param(
        {
            'request_id': 'test-success',
            'entrypoint': _success_entrypoint,
            'expected_exception': None,
        },
        id='success'),
    pytest.param(
        {
            'request_id': 'test-retryable',
            'entrypoint': _retryable_error_entrypoint,
            'expected_exception': exceptions.ExecutionRetryableError,
        },
        id='retryable_error'),
    pytest.param(
        {
            'request_id': 'test-error',
            'entrypoint': _general_error_entrypoint,
            'expected_exception': None,
        },
        id='general_exception'),
    pytest.param(
        {
            'request_id': 'test-interrupt',
            'entrypoint': _keyboard_interrupt_entrypoint,
            'expected_exception': None,
        },
        id='keyboard_interrupt'),
])
async def test_stdout_stderr_restoration(mock_fd_operations, test_case):
    """Test stdout and stderr fd handling across different execution paths."""
    req = requests_lib.Request(request_id=test_case['request_id'],
                               name='test',
                               entrypoint=test_case['entrypoint'],
                               request_body=payloads.RequestBody(),
                               status=requests_lib.RequestStatus.PENDING,
                               created_at=0.0,
                               user_id='test-user')
    await requests_lib.create_if_not_exists_async(req)

    if test_case['expected_exception'] is not None:
        with pytest.raises(test_case['expected_exception']):
            executor._request_execution_wrapper(test_case['request_id'],
                                                ignore_return_value=False)
    else:
        executor._request_execution_wrapper(test_case['request_id'],
                                            ignore_return_value=False)

    # Verify file descriptors were saved (dup called for stdout + stderr)
    assert mock_fd_operations['dup_count']() == 2, (
        f'Expected os.dup() to be called exactly twice (stdout + stderr), '
        f'but got {mock_fd_operations["dup_count"]()} calls')

    # Verify file descriptors were closed exactly once
    assert _get_saved_fd_close_count(
        mock_fd_operations['close_calls'],
        mock_fd_operations['created_fds']) == 2, (
            f'Expected os.close() to be called exactly twice, '
            f'but got {mock_fd_operations["close_count"]()} calls. '
            f'Close calls: {mock_fd_operations["close_calls"]}')

    # Verify no double-close
    _assert_no_double_close(mock_fd_operations['close_calls'],
                            mock_fd_operations['created_fds'])


@pytest.mark.asyncio
async def test_request_worker_retry_execution_retryable_error(
        isolated_database, monkeypatch):
    """Test that RequestWorker retries requests when ExecutionRetryableError is raised."""
    # Create a request in the database
    request_id = 'test-retry-request'
    request = requests_lib.Request(
        request_id=request_id,
        name='test-request',
        entrypoint=
        _dummy_entrypoint_for_retry_test,  # Won't be called in this test
        request_body=payloads.RequestBody(),
        status=requests_lib.RequestStatus.RUNNING,
        created_at=time.time(),
        user_id='test-user',
    )
    await requests_lib.create_if_not_exists_async(request)

    # Create a mock queue that tracks puts
    queue_items = []
    mock_queue = queue_lib.Queue()

    class MockRequestQueue:

        def __init__(self, queue):
            self.queue = queue

        def get(self):
            try:
                return self.queue.get(block=False)
            except queue_lib.Empty:
                return None

        def put(self, item):
            queue_items.append(item)
            self.queue.put(item)

    request_queue = MockRequestQueue(mock_queue)

    # Mock _get_queue to return our mock queue
    def mock_get_queue(schedule_type):
        return request_queue

    monkeypatch.setattr(executor, '_get_queue', mock_get_queue)

    # Mock time.sleep to track calls (but still sleep for very short waits)
    sleep_calls = []

    def mock_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr('time.sleep', mock_sleep)

    # Create a mock executor that tracks submit_until_success calls
    submit_calls = []

    class MockExecutor:

        def submit_until_success(self, fn, *args, **kwargs):
            submit_calls.append((fn, args, kwargs))
            # Return a future that immediately completes (does nothing)
            fut = concurrent.futures.Future()
            fut.set_result(None)
            return fut

    mock_executor = MockExecutor()

    # Create a RequestWorker
    worker = executor.RequestWorker(
        schedule_type=requests_lib.ScheduleType.LONG,
        config=server_config.WorkerConfig(garanteed_parallelism=1,
                                          burstable_parallelism=0,
                                          num_db_connections_per_worker=0))

    # Create a future that raises ExecutionRetryableError
    retryable_error = exceptions.ExecutionRetryableError(
        'Failed to provision all possible launchable resources.',
        hint='Retry after 30s',
        retry_wait_seconds=30)
    fut = concurrent.futures.Future()
    fut.set_exception(retryable_error)

    # Create request_element tuple
    request_element = (request_id, False, True
                      )  # (request_id, ignore_return_value, retryable)

    # Call handle_task_result - this should catch the exception and reschedule
    worker.handle_task_result(fut, request_element)

    # Verify the request was put back on the queue
    assert queue_items == [
        request_element
    ], (f'Expected {request_element} to be put on queue, got {queue_items[0]}')

    # Verify time.sleep was called with the retry wait time (first call should be 30)
    assert sleep_calls == [
        30
    ], (f'Expected first time.sleep call to be 30 seconds, got {sleep_calls[0]}'
       )

    # Verify the request status was reset to PENDING
    updated_request = requests_lib.get_request(request_id, fields=['status'])
    assert updated_request is not None
    assert updated_request.status == requests_lib.RequestStatus.PENDING, (
        f'Expected request status to be PENDING, got {updated_request.status}')

    # Call process_request - it should pick up the request from the queue
    # and call submit_until_success
    worker.process_request(mock_executor, request_queue)

    # Verify submit_until_success was called
    assert len(submit_calls) == 1, (
        f'Expected submit_until_success to be called once, got {len(submit_calls)} calls'
    )
