"""Unit tests for sky/client/sdk_async.py."""
import asyncio
from unittest import mock

import pytest

from sky import exceptions
from sky.client import sdk_async
from sky.server import common as server_common
from sky.server.requests import requests as requests_lib
from sky.utils import common as common_utils


@pytest.fixture
def mock_get():
    """Mock the get() function to return a mock response."""
    async def mock_get_async(*args, **kwargs):
        return mock_get.return_value

    with mock.patch('sky.client.sdk_async.get', side_effect=mock_get_async) as mock_get:
        yield mock_get


@pytest.fixture
def mock_sdk():
    """Mock the sdk functions to return a mock request_id."""
    with mock.patch('sky.client.sdk_async.sdk') as mock_sdk:
        yield mock_sdk


@pytest.mark.asyncio
async def test_check(mock_get, mock_sdk):
    """Test check() function."""
    mock_sdk.check.return_value = 'test-request-id'
    mock_get.return_value = {'aws': ['us-west-1'], 'gcp': ['us-central1']}

    result = await sdk_async.check(('aws', 'gcp'), True)
    assert result == {'aws': ['us-west-1'], 'gcp': ['us-central1']}
    mock_sdk.check.assert_called_once_with(('aws', 'gcp'), True, None)
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_enabled_clouds(mock_get, mock_sdk):
    """Test enabled_clouds() function."""
    mock_sdk.enabled_clouds.return_value = 'test-request-id'
    mock_get.return_value = ['aws', 'gcp']

    result = await sdk_async.enabled_clouds(expand=True)
    assert result == ['aws', 'gcp']
    mock_sdk.enabled_clouds.assert_called_once_with(None, True)
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_list_accelerators(mock_get, mock_sdk):
    """Test list_accelerators() function."""
    mock_sdk.list_accelerators.return_value = 'test-request-id'
    mock_get.return_value = {'aws': ['p3.2xlarge'], 'gcp': ['n1-standard-4']}

    result = await sdk_async.list_accelerators(
        gpus_only=True,
        name_filter='p3',
        region_filter='us-west-1',
        quantity_filter=1,
        clouds=['aws'],
        all_regions=True,
        require_price=True,
        case_sensitive=True)
    assert result == {'aws': ['p3.2xlarge'], 'gcp': ['n1-standard-4']}
    mock_sdk.list_accelerators.assert_called_once_with(
        True, 'p3', 'us-west-1', 1, ['aws'], True, True, True)
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_list_accelerator_counts(mock_get, mock_sdk):
    """Test list_accelerator_counts() function."""
    mock_sdk.list_accelerator_counts.return_value = 'test-request-id'
    mock_get.return_value = {'aws': [1, 2, 4], 'gcp': [1, 2, 4, 8]}

    result = await sdk_async.list_accelerator_counts(
        gpus_only=True,
        name_filter='p3',
        region_filter='us-west-1',
        quantity_filter=1,
        clouds=['aws'])
    assert result == {'aws': [1, 2, 4], 'gcp': [1, 2, 4, 8]}
    mock_sdk.list_accelerator_counts.assert_called_once_with(
        True, 'p3', 'us-west-1', 1, ['aws'])
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_workspaces(mock_get, mock_sdk):
    """Test workspaces() function."""
    mock_sdk.workspaces.return_value = 'test-request-id'
    mock_get.return_value = {'workspace1': {'status': 'active'}}

    result = await sdk_async.workspaces()
    assert result == {'workspace1': {'status': 'active'}}
    mock_sdk.workspaces.assert_called_once()
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_status(mock_get, mock_sdk):
    """Test status() function."""
    mock_sdk.status.return_value = 'test-request-id'
    mock_get.return_value = [{'name': 'test-cluster', 'status': 'UP'}]

    result = await sdk_async.status(
        cluster_names=['test-cluster'],
        refresh=common_utils.StatusRefreshMode.FORCE,
        all_users=True)
    assert result == [{'name': 'test-cluster', 'status': 'UP'}]
    mock_sdk.status.assert_called_once_with(
        ['test-cluster'], common_utils.StatusRefreshMode.FORCE, True)
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_endpoints(mock_get, mock_sdk):
    """Test endpoints() function."""
    mock_sdk.endpoints.return_value = 'test-request-id'
    mock_get.return_value = {8080: 'http://1.2.3.4:8080'}

    result = await sdk_async.endpoints('test-cluster', 8080)
    assert result == {8080: 'http://1.2.3.4:8080'}
    mock_sdk.endpoints.assert_called_once_with('test-cluster', 8080)
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_storage_ls(mock_get, mock_sdk):
    """Test storage_ls() function."""
    mock_sdk.storage_ls.return_value = 'test-request-id'
    mock_get.return_value = [{'name': 'test-storage', 'status': 'READY'}]

    result = await sdk_async.storage_ls()
    assert result == [{'name': 'test-storage', 'status': 'READY'}]
    mock_sdk.storage_ls.assert_called_once()
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_storage_delete(mock_get, mock_sdk):
    """Test storage_delete() function."""
    mock_sdk.storage_delete.return_value = 'test-request-id'
    mock_get.return_value = None

    result = await sdk_async.storage_delete('test-storage')
    assert result is None
    mock_sdk.storage_delete.assert_called_once_with('test-storage')
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_local_up(mock_get, mock_sdk):
    """Test local_up() function."""
    mock_sdk.local_up.return_value = 'test-request-id'
    mock_get.return_value = None

    result = await sdk_async.local_up(
        gpus=True,
        ips=['1.2.3.4'],
        ssh_user='ubuntu',
        ssh_key='/path/to/key',
        cleanup=True,
        context_name='test-context',
        password='test-password')
    assert result is None
    mock_sdk.local_up.assert_called_once_with(
        True, ['1.2.3.4'], 'ubuntu', '/path/to/key', True, 'test-context',
        'test-password')
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_local_down(mock_get, mock_sdk):
    """Test local_down() function."""
    mock_sdk.local_down.return_value = 'test-request-id'
    mock_get.return_value = None

    result = await sdk_async.local_down()
    assert result is None
    mock_sdk.local_down.assert_called_once()
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_ssh_down(mock_get, mock_sdk):
    """Test ssh_down() function."""
    mock_sdk.ssh_down.return_value = 'test-request-id'
    mock_get.return_value = None

    result = await sdk_async.ssh_down('test-infra')
    assert result is None
    mock_sdk.ssh_down.assert_called_once_with('test-infra')
    mock_get.assert_called_once_with('test-request-id')


@pytest.mark.asyncio
async def test_api_cancel(mock_get, mock_sdk):
    """Test api_cancel() function."""
    mock_sdk.api_cancel.return_value = 'test-request-id'
    mock_get.return_value = ['test-request-id']

    result = await sdk_async.api_cancel(
        request_ids=['test-request-id'],
        all_users=True,
        silent=True)
    assert result == ['test-request-id']
    mock_sdk.api_cancel.assert_called_once_with(
        ['test-request-id'], True, True)
    mock_get.assert_called_once_with('test-request-id')


def test_api_info(mock_sdk):
    """Test api_info() function."""
    mock_sdk.api_info.return_value = {
        'status': 'healthy',
        'version': '1.0.0'
    }

    result = sdk_async.api_info()
    assert result == {'status': 'healthy', 'version': '1.0.0'}
    mock_sdk.api_info.assert_called_once()


def test_api_stop(mock_sdk):
    """Test api_stop() function."""
    mock_sdk.api_stop.return_value = None

    result = sdk_async.api_stop()
    assert result is None
    mock_sdk.api_stop.assert_called_once()


def test_api_server_logs(mock_sdk):
    """Test api_server_logs() function."""
    mock_sdk.api_server_logs.return_value = None

    result = sdk_async.api_server_logs(follow=True, tail=100)
    assert result is None
    mock_sdk.api_server_logs.assert_called_once_with(True, 100)


def test_api_login(mock_sdk):
    """Test api_login() function."""
    mock_sdk.api_login.return_value = None

    result = sdk_async.api_login('http://test-endpoint', get_token=True)
    assert result is None
    mock_sdk.api_login.assert_called_once_with('http://test-endpoint', True) 