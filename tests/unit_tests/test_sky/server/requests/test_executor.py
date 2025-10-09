"""Unit tests for sky.server.requests.executor module."""
import asyncio
import os
import time
from unittest import mock

import pytest

from sky import skypilot_config
from sky.server.requests import executor
from sky.server.requests import payloads
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
def test_config_file(tmp_path):
    config_content = """
jobs:
  controller:
    consolidation_mode: true
    resources:
      cpus: 2+
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
    with mock.patch('sky.global_user_state.add_or_update_user'), \
         mock.patch('sky.global_user_state.get_user') as mock_get_user:
        mock_user = mock.Mock()
        mock_user.id = 'test-user-id'
        mock_user.name = 'test-user'
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
    requests_lib.create_if_not_exists(request)

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


def test_api_cancel_race_condition(isolated_database):
    """Cancel before execution: wrapper must no-op and not run entrypoint."""
    CALLED_FLAG[0] = False
    req = requests_lib.Request(request_id='race-cancel-before',
                               name='test-request',
                               entrypoint=dummy_entrypoint,
                               request_body=payloads.RequestBody(),
                               status=requests_lib.RequestStatus.PENDING,
                               created_at=0.0,
                               user_id='test-user')

    assert requests_lib.create_if_not_exists(req) is True

    # Cancel the request before the executor starts.
    cancelled = requests_lib.kill_requests(['race-cancel-before'])
    assert cancelled == ['race-cancel-before']

    # Execute wrapper should detect CANCELLED and return immediately.
    executor._request_execution_wrapper('race-cancel-before',
                                        ignore_return_value=False)

    # Verify entrypoint was not invoked and status remains CANCELLED.
    assert CALLED_FLAG[0] is False
    updated = requests_lib.get_request('race-cancel-before')
    assert updated is not None
    assert updated.status == requests_lib.RequestStatus.CANCELLED


def worker_fn(expected_env_a: str, expected_env_b: str, expected_cpus: str,
              expected_memory: str):
    """Worker that verifies it sees the correct env vars and config overrides."""
    # Assert env vars are correct
    assert os.environ.get('TEST_VAR_A') == expected_env_a, (
        f"Expected TEST_VAR_A={expected_env_a}, got {os.environ.get('TEST_VAR_A')}"
    )
    assert os.environ.get('TEST_VAR_B') == expected_env_b, (
        f"Expected TEST_VAR_B={expected_env_b}, got {os.environ.get('TEST_VAR_B')}"
    )

    # Assert config override works - should see the overridden value, not the base
    actual_cpus = skypilot_config.get_nested(
        ('jobs', 'controller', 'resources', 'cpus'), default_value=None)
    assert actual_cpus == expected_cpus, (
        f"Expected cpus={expected_cpus}, got {actual_cpus}")
    actual_memory = skypilot_config.get_nested(
        ('jobs', 'controller', 'resources', 'memory'), default_value=None)
    assert actual_memory == expected_memory, (
        f"Expected memory={expected_memory}, got {actual_memory}")

    # Assert base config from test_config_file is still loaded (consolidation_mode: true)
    assert skypilot_config.get_nested(
        ('jobs', 'controller', 'consolidation_mode'),
        default_value=False) is True

    return


@pytest.mark.asyncio
async def test_execute_with_env_override_thread_isolation(
        isolated_database, mock_global_user_state, test_config_file,
        hijacked_sys_attrs):
    """Test that multiple concurrent threads see independent env vars.

    This test verifies the core isolation guarantee: when multiple coroutines
    spawn worker threads via _execute_with_config_override, each thread should
    see its own independent environment variables with no cross-contamination.
    """

    async def spawn_request(request_id: str, env_a: str, env_b: str):
        """Spawn a request with specific env overrides."""
        request_body = payloads.RequestBody(
            env_vars={
                'TEST_VAR_A': env_a,
                'TEST_VAR_B': env_b,
                constants.USER_ID_ENV_VAR: f'user-{request_id}',
                constants.USER_ENV_VAR: f'user-{request_id}',
            },
            override_skypilot_config={
                'jobs': {
                    'controller': {
                        'resources': {
                            'cpus': f'{request_id}+',
                            'memory': f'{request_id}x',
                        }
                    }
                }
            })

        request = executor.prepare_request(
            request_id=request_id,
            request_name='test.isolation',
            request_body=request_body,
            func=worker_fn,
            schedule_type=requests_lib.ScheduleType.SHORT)
        request.entrypoint = lambda: worker_fn(expected_env_a=env_a,
                                               expected_env_b=env_b,
                                               expected_cpus=f'{request_id}+',
                                               expected_memory=f'{request_id}x')

        task = executor.execute_request_in_coroutine(request)
        await task.task

        completed_request = requests_lib.get_request(request_id)
        assert completed_request is not None
        assert completed_request.status == requests_lib.RequestStatus.SUCCEEDED
        return completed_request.get_return_value()

    # Spawn 5 concurrent requests with different env vars
    tasks = [
        spawn_request('1', 'a', 'one'),
        spawn_request('2', 'b', 'two'),
        spawn_request('3', 'c', 'three'),
        spawn_request('4', 'd', 'four'),
        spawn_request('5', 'e', 'five'),
    ]

    results = await asyncio.gather(*tasks)
    assert len(results) == 5
