"""Unit tests for the SkyPilot API server common module."""
import sys
from unittest import mock

import pytest

import sky
from sky import exceptions
from sky import skypilot_config
from sky.server import common
from sky.server import constants as server_constants
from sky.server.common import ApiServerInfo
from sky.server.common import ApiServerStatus


@mock.patch('sky.server.common.get_api_server_status')
def test_healthy_server(mock_get_status):
    """Test when server is healthy."""
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.HEALTHY,
        api_version=server_constants.API_VERSION,
        version=sky.__version__,
        commit=sky.__commit__)

    # Should not raise any exception
    common.check_server_healthy()


@mock.patch('sky.server.common.get_api_server_status')
def test_unhealthy_server(mock_get_status):
    """Test when server is unhealthy."""
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.UNHEALTHY)

    with pytest.raises(exceptions.ApiServerConnectionError):
        common.check_server_healthy()


@mock.patch('sky.server.common.get_api_server_status')
@mock.patch('sky.server.common.is_api_server_local')
def test_local_client_server_mismatch(mock_is_local, mock_get_status):
    """Test when local client and server version mismatch."""
    mock_is_local.return_value = True

    def expect_error_with_hints():
        with pytest.raises(RuntimeError) as exc_info:
            common.check_server_healthy()

        # Correct error message
        assert 'Client and local API server version mismatch' in str(
            exc_info.value)
        # Should hint user to restart local API server
        assert 'sky api stop; sky api start' in str(exc_info.value)

    # Test when client is newer than server
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.VERSION_MISMATCH,
        api_version='0',  # Always older than client version
        version=sky.__version__,
        commit=sky.__commit__)
    expect_error_with_hints()

    # Test when client is older than server
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.VERSION_MISMATCH,
        api_version=str(sys.maxsize),  # Always newer than client version
        version=sky.__version__,
        commit=sky.__commit__)
    expect_error_with_hints()

    # Test when server version format is unknown, i.e.
    # a newer version with unknown format
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.VERSION_MISMATCH,
        api_version='unknown',
        version=sky.__version__,
        commit=sky.__commit__)
    expect_error_with_hints()


@mock.patch('sky.server.common.get_api_server_status')
@mock.patch('sky.server.common.is_api_server_local')
def test_remote_server_older(mock_is_local, mock_get_status):
    """Test when remote server version is older than client."""
    mock_is_local.return_value = False
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.VERSION_MISMATCH,
        api_version='0',
        version='1.0.0-dev20250415',
        commit='abc123')

    with pytest.raises(RuntimeError) as exc_info:
        common.check_server_healthy()

    # Correct error message
    assert 'SkyPilot API server is too old' in str(exc_info.value)
    # Should hint user to upgrade remote server
    assert 'Contact your administrator to upgrade the remote API server' in str(
        exc_info.value)
    assert 'or downgrade your local client with' in str(exc_info.value)


@mock.patch('sky.server.common.get_api_server_status')
@mock.patch('sky.server.common.is_api_server_local')
def test_client_older(mock_is_local, mock_get_status):
    """Test when client version is older than server."""
    mock_is_local.return_value = False
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.VERSION_MISMATCH,
        api_version=str(sys.maxsize),
        version='1.0.0-dev20250415',
        commit='abc123')

    with pytest.raises(RuntimeError) as exc_info:
        common.check_server_healthy()

    # Correct error message
    assert 'Your SkyPilot client is too old' in str(exc_info.value)
    # Should hint user to upgrade client
    assert 'Upgrade your client with' in str(exc_info.value)


def test_get_version_info_hint():
    """Test the version info hint."""
    # Test dev version
    server_info = ApiServerInfo(status=ApiServerStatus.VERSION_MISMATCH,
                                api_version='1',
                                version='1.0.0-dev0',
                                commit='abc123')
    with mock.patch('sky.__version__', '1.0.0-dev0'), \
         mock.patch('sky.__commit__', 'def456'):
        hint = common._get_version_info_hint(server_info)
        assert 'client version: v1.0.0-dev0 with commit def456' in hint
        assert 'server version: v1.0.0-dev0 with commit abc123' in hint

    # Test stable version
    server_info = ApiServerInfo(status=ApiServerStatus.VERSION_MISMATCH,
                                api_version='1',
                                version='1.0.0',
                                commit='abc123')
    with mock.patch('sky.__version__', '1.1.0'):
        hint = common._get_version_info_hint(server_info)
        assert 'client version: v1.1.0' in hint
        assert 'server version: v1.0.0' in hint


def test_install_server_version_command():
    """Test the install server version command."""
    # Test dev version
    server_info = ApiServerInfo(status=ApiServerStatus.VERSION_MISMATCH,
                                api_version='1',
                                version='1.0.0-dev0',
                                commit='abc123')
    cmd = common._install_server_version_command(server_info)
    assert cmd == 'pip install git+https://github.com/skypilot-org/skypilot@abc123'

    # Test nightly version
    server_info = ApiServerInfo(status=ApiServerStatus.VERSION_MISMATCH,
                                api_version='1',
                                version='1.0.0-dev20250415',
                                commit='abc123')
    cmd = common._install_server_version_command(server_info)
    assert cmd == 'pip install -U "skypilot-nightly==1.0.0-dev20250415"'

    # Test stable version
    server_info = ApiServerInfo(status=ApiServerStatus.VERSION_MISMATCH,
                                api_version='1',
                                version='1.0.0',
                                commit='abc123')
    cmd = common._install_server_version_command(server_info)
    assert cmd == 'pip install -U "skypilot==1.0.0"'


@pytest.fixture
def mock_all_dependencies():
    """Mock all dependencies used in reload_for_new_request."""
    with mock.patch('sky.utils.common_utils.set_client_status') as mock_status, \
         mock.patch('sky.usage.usage_lib.messages.reset') as mock_reset, \
         mock.patch('sky.sky_logging.reload_logger') as mock_logger:
        yield {
            'set_status': mock_status,
            'reset_messages': mock_reset,
            'reload_logger': mock_logger
        }


def test_reload_config_for_new_request(mock_all_dependencies, tmp_path,
                                       monkeypatch):
    """Test basic functionality with all parameters provided."""
    config_path = tmp_path / 'config.yaml'
    config_path.write_text('''
allowed_clouds:
  - aws
''')

    # Set env var to point to the temp config
    monkeypatch.setenv('SKYPILOT_CONFIG', str(config_path))
    common.reload_for_new_request(
        client_entrypoint='test_entry',
        client_command='test_cmd',
        using_remote_api_server=False,
    )
    assert skypilot_config.get_nested(keys=('allowed_clouds',),
                                      default_value=None) == ['aws']
    config_path.write_text('''
allowed_clouds:
  - gcp
''')
    common.reload_for_new_request(
        client_entrypoint='test_entry',
        client_command='test_cmd',
        using_remote_api_server=False,
    )
    assert skypilot_config.get_nested(keys=('allowed_clouds',),
                                      default_value=None) == ['gcp']
