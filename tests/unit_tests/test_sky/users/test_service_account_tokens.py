"""Tests for service account token functionality."""

import os
import unittest.mock as mock

from sky.client import service_account_auth


class TestServiceAccountTokens:
    """Test cases for service account token operations."""

    @mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'sky_test_token'})
    def test_token_authentication_headers(self):
        """Test service account token authentication via headers."""
        headers = service_account_auth.get_service_account_headers()
        assert headers == {
            'Authorization': 'Bearer sky_test_token',
        }

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_user_config')
    def test_no_token_no_headers(self, mock_get_config):
        """Test no headers when no token is available."""
        mock_config = mock.Mock()
        mock_config.get_nested.return_value = None
        mock_get_config.return_value = mock_config

        headers = service_account_auth.get_service_account_headers()
        assert headers == {}

    def test_api_url_generation(self):
        """Test API URL generation is consistent regardless of auth type."""
        # Test consistent URL generation
        url = service_account_auth.get_api_url(
            'http://server.com', 'api/status')
        assert url == 'http://server.com/api/status'

        # Test with leading slash in path
        url = service_account_auth.get_api_url(
            'http://server.com', '/api/status')
        assert url == 'http://server.com/api/status'

        # Test with base URL ending in slash
        url = service_account_auth.get_api_url(
            'http://server.com/', 'api/status')
        assert url == 'http://server.com//api/status'

    @mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'sky_valid_token'})
    def test_token_validation_success(self):
        """Test successful token validation."""
        token = service_account_auth.get_service_account_token()
        assert token == 'sky_valid_token'
        assert token.startswith('sky_')

    @mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'invalid_token'})
    def test_token_validation_failure(self):
        """Test token validation failure for invalid format."""
        try:
            service_account_auth.get_service_account_token()
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert 'Invalid service account token format' in str(e)

    @mock.patch.dict(os.environ, {'SKYPILOT_TOKEN': 'sky_test_token'})
    def test_bearer_token_format(self):
        """Test Bearer token format in Authorization header."""
        headers = service_account_auth.get_service_account_headers()
        auth_header = headers.get('Authorization')
        assert auth_header is not None
        assert auth_header.startswith('Bearer sky_')
        assert auth_header == 'Bearer sky_test_token'
