"""Tests for service account authentication client module."""

import os
import unittest.mock as mock

from sky.client import service_account_auth


class TestServiceAccountAuth:
    """Test cases for service account authentication."""

    @mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'sky_test_token'})
    def test_get_service_account_token_from_env(self):
        """Test getting service account token from environment variable."""
        token = service_account_auth.get_service_account_token()
        assert token == 'sky_test_token'

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_token_from_config(self, mock_get_config):
        """Test getting service account token from config file."""
        mock_config = mock.Mock()
        mock_config.get_nested.return_value = 'sky_config_token'
        mock_get_config.return_value = mock_config

        token = service_account_auth.get_service_account_token()
        assert token == 'sky_config_token'

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_token_none(self, mock_get_config):
        """Test getting service account token when none available."""
        mock_config = mock.Mock()
        mock_config.get_nested.return_value = None
        mock_get_config.return_value = mock_config

        token = service_account_auth.get_service_account_token()
        assert token is None

    @mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'invalid_token'})
    def test_get_service_account_token_invalid_format_env(self):
        """Test invalid token format from environment variable."""
        try:
            service_account_auth.get_service_account_token()
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert 'Invalid service account token format' in str(e)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_token_invalid_format_config(self, mock_get_config):
        """Test invalid token format from config file."""
        mock_config = mock.Mock()
        mock_config.get_nested.return_value = 'invalid_token'
        mock_get_config.return_value = mock_config

        try:
            service_account_auth.get_service_account_token()
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert 'Invalid service account token format in config file' in str(e)

    @mock.patch('sky.client.service_account_auth.get_service_account_token')
    def test_get_service_account_headers_with_token(self, mock_get_token):
        """Test getting headers when token is available."""
        mock_get_token.return_value = 'sky_test_token'
        
        headers = service_account_auth.get_service_account_headers()
        assert headers == {
            'Authorization': 'Bearer sky_test_token',
        }

    @mock.patch('sky.client.service_account_auth.get_service_account_token')
    def test_get_service_account_headers_no_token(self, mock_get_token):
        """Test getting headers when no token is available."""
        mock_get_token.return_value = None
        
        headers = service_account_auth.get_service_account_headers()
        assert headers == {}

    def test_get_api_url(self):
        """Test getting API URL (same for all authentication types)."""
        # Test with path starting with slash
        url = service_account_auth.get_api_url(
            'https://api.example.com', '/api/v1/status')
        assert url == 'https://api.example.com/api/v1/status'

        # Test with path not starting with slash
        url = service_account_auth.get_api_url(
            'https://api.example.com', 'api/v1/status')
        assert url == 'https://api.example.com/api/v1/status'

        # Test with base URL ending with slash
        url = service_account_auth.get_api_url(
            'https://api.example.com/', 'api/v1/status')
        assert url == 'https://api.example.com//api/v1/status'

    @mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'sky_test_token'})
    def test_env_variable_priority(self):
        """Test that environment variable takes priority over config."""
        with mock.patch('sky.skypilot_config.get_user_config') as mock_get_config:
            mock_config = mock.Mock()
            mock_config.get_nested.return_value = 'sky_config_token'
            mock_get_config.return_value = mock_config

            token = service_account_auth.get_service_account_token()
            # Should get env token, not config token
            assert token == 'sky_test_token'
