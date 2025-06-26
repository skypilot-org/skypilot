"""Unit tests for service account authentication functionality."""

import os
from unittest import mock

import pytest

from sky.client import service_account_auth


class TestServiceAccountAuth:
    """Test cases for service account authentication functions."""

    def test_get_service_account_token_from_env(self):
        """Test getting service account token from environment variable."""
        with mock.patch.dict(os.environ,
                             {'SKYPILOT_TOKEN': 'sky_env_token_123'}):
            token = service_account_auth.get_service_account_token()
            assert token == 'sky_env_token_123'

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_token_from_config(self, mock_get_config):
        """Test getting service account token from config file."""
        mock_config = mock.Mock()
        mock_config.get_nested.return_value = 'sky_config_token_456'
        mock_get_config.return_value = mock_config

        token = service_account_auth.get_service_account_token()
        assert token == 'sky_config_token_456'
        mock_config.get_nested.assert_called_once_with(
            ('api_server', 'service_account_token'), default_value=None)

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_token_none(self, mock_get_config):
        """Test when no service account token is available."""
        mock_config = mock.Mock()
        mock_config.get_nested.return_value = None
        mock_get_config.return_value = mock_config

        token = service_account_auth.get_service_account_token()
        assert token is None

    def test_get_service_account_token_invalid_format_env(self):
        """Test getting invalid format service account token from env."""
        with mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'invalid_token'}):
            with pytest.raises(ValueError,
                               match='Invalid service account token format'):
                service_account_auth.get_service_account_token()

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_token_invalid_format_config(
            self, mock_get_config):
        """Test getting invalid format service account token from config."""
        # Ensure no environment variable interferes with the test
        with mock.patch.dict(os.environ, {}, clear=True):
            mock_config = mock.Mock()
            mock_config.get_nested.return_value = 'invalid_token'
            mock_get_config.return_value = mock_config

            with pytest.raises(
                    ValueError,
                    match='Invalid service account token format in config file'
            ):
                service_account_auth.get_service_account_token()

    def test_get_service_account_headers_with_token(self):
        """Test getting service account headers when token is available."""
        with mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'sky_test_token'}):
            headers = service_account_auth.get_service_account_headers()
            assert headers == {
                'Authorization': 'Bearer sky_test_token',
                'X-Service-Account-Auth': 'true'
            }

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_headers_no_token(self, mock_get_config):
        """Test getting service account headers when no token is available."""
        mock_config = mock.Mock()
        mock_config.get_nested.return_value = None
        mock_get_config.return_value = mock_config

        headers = service_account_auth.get_service_account_headers()
        assert headers == {}

    def test_should_use_service_account_path_with_token(self):
        """Test service account path usage detection when token is available."""
        with mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'sky_test_token'}):
            assert service_account_auth.should_use_service_account_path(
            ) is True

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_should_use_service_account_path_no_token(self, mock_get_config):
        """Test service account path usage detection when no token."""
        mock_config = mock.Mock()
        mock_config.get_nested.return_value = None
        mock_get_config.return_value = mock_config

        assert service_account_auth.should_use_service_account_path() is False

    def test_get_api_url_with_service_account_path(self):
        """Test API URL generation with service account path."""
        with mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'sky_test_token'}):
            url = service_account_auth.get_api_url_with_service_account_path(
                'https://api.example.com', '/api/v1/status')
            assert url == 'https://api.example.com/sa/api/v1/status'

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_api_url_without_service_account_path(self, mock_get_config):
        """Test API URL generation without service account path."""
        mock_config = mock.Mock()
        mock_config.get_nested.return_value = None
        mock_get_config.return_value = mock_config

        url = service_account_auth.get_api_url_with_service_account_path(
            'https://api.example.com', '/api/v1/status')
        assert url == 'https://api.example.com/api/v1/status'

    def test_get_api_url_with_absolute_path(self):
        """Test API URL generation with absolute path."""
        with mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'sky_test_token'}):
            url = service_account_auth.get_api_url_with_service_account_path(
                'https://api.example.com', 'api/v1/status')
            assert url == 'https://api.example.com/sa/api/v1/status'

    def test_env_token_priority_over_config(self):
        """Test that environment token takes priority over config token."""
        with mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'sky_env_token'}):
            with mock.patch(
                    'sky.skypilot_config.get_user_config') as mock_get_config:
                mock_config = mock.Mock()
                mock_config.get_nested.return_value = 'sky_config_token'
                mock_get_config.return_value = mock_config

                token = service_account_auth.get_service_account_token()
                assert token == 'sky_env_token'
                # Config should not be accessed when env var is present
                mock_get_config.assert_not_called()
