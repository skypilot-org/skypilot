"""Unit tests for the SkyPilot API server common module."""
import sys
import unittest
from unittest import mock

import pytest

from sky import exceptions
from sky.server import common
from sky.server import constants as server_constants
from sky.server.common import ApiServerInfo
from sky.server.common import ApiServerStatus


class TestCheckServerHealthy(unittest.TestCase):
    """Tests for check_server_healthy function."""

    @mock.patch('sky.server.common.get_api_server_status')
    def test_healthy_server(self, mock_get_status):
        """Test when server is healthy."""
        mock_get_status.return_value = ApiServerInfo(
            status=ApiServerStatus.HEALTHY,
            api_version=server_constants.API_VERSION)

        # Should not raise any exception
        common.check_server_healthy()

    @mock.patch('sky.server.common.get_api_server_status')
    def test_unhealthy_server(self, mock_get_status):
        """Test when server is unhealthy."""
        mock_get_status.return_value = ApiServerInfo(
            status=ApiServerStatus.UNHEALTHY, api_version=None)

        with pytest.raises(exceptions.ApiServerConnectionError):
            common.check_server_healthy()

    @mock.patch('sky.server.common.get_api_server_status')
    @mock.patch('sky.server.common.is_api_server_local')
    def test_local_server_older(self, mock_is_local, mock_get_status):
        """Test when local server version is older than client."""
        mock_is_local.return_value = True
        mock_get_status.return_value = ApiServerInfo(
            status=ApiServerStatus.VERSION_MISMATCH,
            api_version='0')  # Always older thant client version

        with pytest.raises(RuntimeError) as exc_info:
            common.check_server_healthy()

        # Correct error message
        assert 'SkyPilot API server is too old' in str(exc_info.value)
        # Should hint user to restart local API server
        assert 'sky api stop; sky api start' in str(exc_info.value)

    @mock.patch('sky.server.common.get_api_server_status')
    @mock.patch('sky.server.common.is_api_server_local')
    def test_remote_server_older(self, mock_is_local, mock_get_status):
        """Test when remote server version is older than client."""
        mock_is_local.return_value = False
        mock_get_status.return_value = ApiServerInfo(
            status=ApiServerStatus.VERSION_MISMATCH,
            api_version='0')  # Always older thant client version

        with pytest.raises(RuntimeError) as exc_info:
            common.check_server_healthy()

        # Correct error message
        assert 'SkyPilot API server is too old' in str(exc_info.value)
        # Should hint user to upgrade remote server
        assert 'Please refer to the following link to upgrade your server' in str(
            exc_info.value)

    @mock.patch('sky.server.common.get_api_server_status')
    @mock.patch('sky.server.common.is_api_server_local')
    def test_client_older(self, mock_is_local, mock_get_status):
        """Test when client version is older than server."""
        mock_is_local.return_value = False
        mock_get_status.return_value = ApiServerInfo(
            status=ApiServerStatus.VERSION_MISMATCH,
            api_version=str(
                sys.maxsize))  # Guaranteed to be newer than client version

        with pytest.raises(RuntimeError) as exc_info:
            common.check_server_healthy()

        # Correct error message
        assert 'Your SkyPilot client is too old' in str(exc_info.value)
        # Should hint user to upgrade client
        assert 'Please refer to the following link to upgrade your client' in str(
            exc_info.value)
