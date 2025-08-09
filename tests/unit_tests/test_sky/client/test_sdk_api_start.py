"""Unit tests for the SkyPilot client SDK api_start function."""
from unittest import mock

from sky.client import sdk
from sky.server import common as server_common


@mock.patch('sky.client.sdk.server_common.check_server_healthy_or_start_fn')
@mock.patch('sky.skypilot_config.get_nested')
@mock.patch.dict('os.environ', {}, clear=True)
def test_api_start_with_remote_config_and_host_flag(mock_config, mock_start):
    """Test that api_start respects --host flag even when remote endpoint is configured.
    
    This tests the fix for issue #6463: 'sky api start --deploy does not honor host flag'
    """
    # Clear caches to ensure fresh test
    server_common.get_server_url.cache_clear()
    server_common.is_api_server_local.cache_clear()

    # Configure a remote endpoint in the config
    mock_config.return_value = 'http://remote-server.com:46580'

    # Test that api_start works with explicit host (the fix)
    # Should not raise ValueError about remote endpoint being configured
    sdk.api_start(host='0.0.0.0')

    # Verify that the server startup function was called with endpoint override
    mock_start.assert_called_once_with(False,
                                       '0.0.0.0',
                                       False,
                                       False,
                                       None,
                                       False,
                                       endpoint_override='http://0.0.0.0:46580')

    # Clean up caches
    server_common.get_server_url.cache_clear()
    server_common.is_api_server_local.cache_clear()


@mock.patch('sky.client.sdk.server_common.check_server_healthy_or_start_fn')
@mock.patch('sky.skypilot_config.get_nested')
@mock.patch.dict('os.environ', {}, clear=True)
def test_api_start_deploy_flag_with_remote_config(mock_config, mock_start):
    """Test that api_start --deploy works even when remote endpoint is configured."""
    # Clear caches to ensure fresh test
    server_common.get_server_url.cache_clear()
    server_common.is_api_server_local.cache_clear()

    # Configure a remote endpoint in the config
    mock_config.return_value = 'http://remote-server.com:46580'

    # Test deploy flag (which should set host to 0.0.0.0)
    sdk.api_start(deploy=True)

    # Verify that the server startup function was called with deploy=True and host='0.0.0.0' and endpoint override
    mock_start.assert_called_once_with(True,
                                       '0.0.0.0',
                                       False,
                                       False,
                                       None,
                                       False,
                                       endpoint_override='http://0.0.0.0:46580')

    # Clean up caches
    server_common.get_server_url.cache_clear()
    server_common.is_api_server_local.cache_clear()


@mock.patch('sky.client.sdk.server_common.check_server_healthy_or_start_fn')
@mock.patch('sky.client.sdk.server_common.get_server_url')
@mock.patch.dict('os.environ', {}, clear=True)
def test_api_start_default_behavior_unchanged(mock_get_server_url, mock_start):
    """Test that api_start default behavior is unchanged when no remote endpoint is configured."""
    # Clear caches to ensure fresh test
    server_common.get_server_url.cache_clear()
    server_common.is_api_server_local.cache_clear()

    # Mock get_server_url to return the same URL as the intended local URL
    # This simulates the case where no remote endpoint is configured
    mock_get_server_url.return_value = 'http://127.0.0.1:46580'

    # Test default behavior
    sdk.api_start()

    # Verify that the server startup function was called with endpoint override
    mock_start.assert_called_once_with(
        False,
        '127.0.0.1',
        False,
        False,
        None,
        False,
        endpoint_override='http://127.0.0.1:46580')

    # Clean up caches
    server_common.get_server_url.cache_clear()
    server_common.is_api_server_local.cache_clear()
