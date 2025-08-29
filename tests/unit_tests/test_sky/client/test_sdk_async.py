"""Unit tests for sky/client/sdk_async.py."""
import asyncio
from unittest import mock

import pytest

from sky import exceptions
from sky.client import sdk_async
from sky.schemas.api import responses
from sky.utils import common as common_utils


@pytest.fixture
def mock_get():
    """Mock the get() function to return a mock response."""

    async def mock_get_async(*args, **kwargs):
        return mock_get.return_value

    with mock.patch('sky.client.sdk_async.get',
                    side_effect=mock_get_async) as mock_get:
        yield mock_get


@pytest.fixture
def mock_stream_and_get():
    """Mock the stream_and_get() function to return a mock response."""

    async def mock_stream_and_get_async(*args, **kwargs):
        return mock_stream_and_get.return_value

    with mock.patch(
            'sky.client.sdk_async.stream_and_get',
            side_effect=mock_stream_and_get_async) as mock_stream_and_get:
        yield mock_stream_and_get


@pytest.fixture
def mock_to_thread():
    """Mock context_utils.to_thread to run synchronously."""

    async def mock_to_thread_func(func, *args, **kwargs):
        return func(*args, **kwargs)

    with mock.patch('sky.utils.context_utils.to_thread',
                    side_effect=mock_to_thread_func):
        yield


@pytest.fixture
def mock_sdk_functions():
    """Mock the underlying SDK functions."""
    with mock.patch('sky.client.sdk.check') as mock_check, \
         mock.patch('sky.client.sdk.enabled_clouds') as mock_enabled_clouds, \
         mock.patch('sky.client.sdk.list_accelerators') as mock_list_accelerators, \
         mock.patch('sky.client.sdk.list_accelerator_counts') as mock_list_accelerator_counts, \
         mock.patch('sky.client.sdk.workspaces') as mock_workspaces, \
         mock.patch('sky.client.sdk.status') as mock_status, \
         mock.patch('sky.client.sdk.endpoints') as mock_endpoints, \
         mock.patch('sky.client.sdk.storage_ls') as mock_storage_ls, \
         mock.patch('sky.client.sdk.storage_delete') as mock_storage_delete, \
         mock.patch('sky.client.sdk.local_up') as mock_local_up, \
         mock.patch('sky.client.sdk.local_down') as mock_local_down, \
         mock.patch('sky.client.sdk.ssh_down') as mock_ssh_down, \
         mock.patch('sky.client.sdk.api_cancel') as mock_api_cancel, \
         mock.patch('sky.client.sdk.api_info') as mock_api_info, \
         mock.patch('sky.client.sdk.api_stop') as mock_api_stop, \
         mock.patch('sky.client.sdk.api_server_logs') as mock_api_server_logs, \
         mock.patch('sky.client.sdk.api_login') as mock_api_login:

        yield {
            'check': mock_check,
            'enabled_clouds': mock_enabled_clouds,
            'list_accelerators': mock_list_accelerators,
            'list_accelerator_counts': mock_list_accelerator_counts,
            'workspaces': mock_workspaces,
            'status': mock_status,
            'endpoints': mock_endpoints,
            'storage_ls': mock_storage_ls,
            'storage_delete': mock_storage_delete,
            'local_up': mock_local_up,
            'local_down': mock_local_down,
            'ssh_down': mock_ssh_down,
            'api_cancel': mock_api_cancel,
            'api_info': mock_api_info,
            'api_stop': mock_api_stop,
            'api_server_logs': mock_api_server_logs,
            'api_login': mock_api_login,
        }


@pytest.mark.asyncio
async def test_check_with_stream(mock_stream_and_get, mock_to_thread,
                                 mock_sdk_functions):
    """Test check() function with stream_logs=True (default)."""
    # Mock the underlying SDK function
    mock_sdk_functions['check'].return_value = 'test-request-id'

    # Mock the stream_and_get result
    expected_result = {'aws': ['us-west-1'], 'gcp': ['us-central1']}
    mock_stream_and_get.return_value = expected_result

    result = await sdk_async.check(('aws', 'gcp'), True)
    assert result == expected_result
    mock_sdk_functions['check'].assert_called_once_with(('aws', 'gcp'), True,
                                                        None)
    # The function should be called with request_id and the default StreamConfig parameters
    # Based on the error: stream_and_get('test-request-id', None, None, True, None)
    mock_stream_and_get.assert_called_once_with('test-request-id', None, None,
                                                True, None)


@pytest.mark.asyncio
async def test_check_no_stream(mock_get, mock_to_thread, mock_sdk_functions):
    """Test check() function with stream_logs=False."""
    # Mock the underlying SDK function
    mock_sdk_functions['check'].return_value = 'test-request-id'

    # Mock the get result
    expected_result = {'aws': ['us-west-1'], 'gcp': ['us-central1']}
    mock_get.return_value = expected_result

    result = await sdk_async.check(('aws', 'gcp'), True, stream_logs=None)
    assert result == expected_result
    mock_sdk_functions['check'].assert_called_once_with(('aws', 'gcp'), True,
                                                        None)
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_enabled_clouds(mock_stream_and_get, mock_to_thread,
                              mock_sdk_functions):
    """Test enabled_clouds() function."""
    mock_sdk_functions['enabled_clouds'].return_value = 'test-request-id'

    expected_result = ['aws', 'gcp']
    mock_stream_and_get.return_value = expected_result

    result = await sdk_async.enabled_clouds(expand=True)
    assert result == expected_result
    mock_sdk_functions['enabled_clouds'].assert_called_once_with(None, True)
    # The function should be called with request_id and the default StreamConfig parameters
    # Based on the error: stream_and_get('test-request-id', None, None, True, None)
    mock_stream_and_get.assert_called_once_with('test-request-id', None, None,
                                                True, None)


@pytest.mark.asyncio
async def test_list_accelerators(mock_stream_and_get, mock_to_thread,
                                 mock_sdk_functions):
    """Test list_accelerators() function."""
    mock_sdk_functions['list_accelerators'].return_value = 'test-request-id'

    expected_result = {'aws': ['p3.2xlarge'], 'gcp': ['n1-standard-4']}
    mock_stream_and_get.return_value = expected_result

    result = await sdk_async.list_accelerators(gpus_only=True,
                                               name_filter='p3',
                                               region_filter='us-west-1',
                                               quantity_filter=1,
                                               clouds=['aws'],
                                               all_regions=True,
                                               require_price=True,
                                               case_sensitive=True)
    assert result == expected_result
    mock_sdk_functions['list_accelerators'].assert_called_once_with(
        True, 'p3', 'us-west-1', 1, ['aws'], True, True, True)
    # The function should be called with request_id and the default StreamConfig parameters
    # Based on the error: stream_and_get('test-request-id', None, None, True, None)
    mock_stream_and_get.assert_called_once_with('test-request-id', None, None,
                                                True, None)


@pytest.mark.asyncio
async def test_status(mock_stream_and_get, mock_to_thread, mock_sdk_functions):
    """Test status() function."""
    mock_sdk_functions['status'].return_value = 'test-request-id'

    expected_result = [{'name': 'test-cluster', 'status': 'UP'}]
    mock_stream_and_get.return_value = expected_result

    result = await sdk_async.status(
        cluster_names=['test-cluster'],
        refresh=common_utils.StatusRefreshMode.FORCE,
        all_users=True)
    assert result == expected_result
    mock_sdk_functions['status'].assert_called_once_with(
        ['test-cluster'],
        common_utils.StatusRefreshMode.FORCE,
        True,
        include_credentials=False)
    # The function should be called with request_id and the default StreamConfig parameters
    # Based on the error: stream_and_get('test-request-id', None, None, True, None)
    mock_stream_and_get.assert_called_once_with('test-request-id', None, None,
                                                True, None)


@pytest.mark.asyncio
async def test_endpoints(mock_stream_and_get, mock_to_thread,
                         mock_sdk_functions):
    """Test endpoints() function."""
    mock_sdk_functions['endpoints'].return_value = 'test-request-id'

    expected_result = {8080: 'http://1.2.3.4:8080'}
    mock_stream_and_get.return_value = expected_result

    result = await sdk_async.endpoints('test-cluster', 8080)
    assert result == expected_result
    mock_sdk_functions['endpoints'].assert_called_once_with(
        'test-cluster', 8080)
    # The function should be called with request_id and the default StreamConfig parameters
    # Based on the error: stream_and_get('test-request-id', None, None, True, None)
    mock_stream_and_get.assert_called_once_with('test-request-id', None, None,
                                                True, None)


@pytest.mark.asyncio
async def test_storage_ls(mock_stream_and_get, mock_to_thread,
                          mock_sdk_functions):
    """Test storage_ls() function."""
    mock_sdk_functions['storage_ls'].return_value = 'test-request-id'

    expected_result = [{'name': 'test-storage', 'status': 'READY'}]
    mock_stream_and_get.return_value = expected_result

    result = await sdk_async.storage_ls()
    assert result == expected_result
    mock_sdk_functions['storage_ls'].assert_called_once()
    # The function should be called with request_id and the default StreamConfig parameters
    # Based on the error: stream_and_get('test-request-id', None, None, True, None)
    mock_stream_and_get.assert_called_once_with('test-request-id', None, None,
                                                True, None)


@pytest.mark.asyncio
async def test_error_propagation(mock_stream_and_get, mock_to_thread,
                                 mock_sdk_functions):
    """Test that errors from stream_and_get are properly propagated."""
    mock_sdk_functions['check'].return_value = 'test-request-id'

    # Mock stream_and_get to raise an exception
    mock_stream_and_get.side_effect = ValueError('Test error')

    with pytest.raises(ValueError, match='Test error'):
        await sdk_async.check(('aws', 'gcp'), True)


@pytest.mark.asyncio
async def test_get_error_propagation(mock_get, mock_to_thread,
                                     mock_sdk_functions):
    """Test that errors from get are properly propagated."""
    mock_sdk_functions['check'].return_value = 'test-request-id'

    # Mock get to raise an exception
    mock_get.side_effect = RuntimeError(
        'Failed to get request test-request-id: 404 Not Found')

    with pytest.raises(RuntimeError,
                       match='Failed to get request test-request-id'):
        await sdk_async.check(('aws', 'gcp'), True, stream_logs=None)


# Test async functions that use to_thread but don't stream
@pytest.mark.asyncio
async def test_api_info(mock_to_thread, mock_sdk_functions):
    """Test api_info() function."""
    return_value = {
        'status': 'healthy',
        'api_version': '1.0.0',
        'version': '1.0.0',
        'version_on_disk': '1.0.0',
        'commit': '1234567890',
        'basic_auth_enabled': False,
        'user': None,
    }
    expected_result = responses.APIHealthResponse(**return_value)
    mock_sdk_functions['api_info'].return_value = expected_result

    result = await sdk_async.api_info()
    assert result == expected_result
    mock_sdk_functions['api_info'].assert_called_once()


@pytest.mark.asyncio
async def test_api_stop(mock_to_thread, mock_sdk_functions):
    """Test api_stop() function."""
    mock_sdk_functions['api_stop'].return_value = None

    result = await sdk_async.api_stop()
    assert result is None
    mock_sdk_functions['api_stop'].assert_called_once()


@pytest.mark.asyncio
async def test_api_server_logs(mock_to_thread, mock_sdk_functions):
    """Test api_server_logs() function."""
    mock_sdk_functions['api_server_logs'].return_value = None

    result = await sdk_async.api_server_logs(follow=True, tail=100)
    assert result is None
    mock_sdk_functions['api_server_logs'].assert_called_once_with(True, 100)


@pytest.mark.asyncio
async def test_api_login(mock_to_thread, mock_sdk_functions):
    """Test api_login() function."""
    mock_sdk_functions['api_login'].return_value = None

    result = await sdk_async.api_login('http://test-endpoint', get_token=True)
    assert result is None
    mock_sdk_functions['api_login'].assert_called_once_with(
        'http://test-endpoint', True)
