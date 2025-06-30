"""Tests for service account token functionality."""

import os
import unittest.mock as mock

from sky.client import service_account_auth
from sky.skylet import constants


class TestServiceAccountTokens:
    """Test cases for service account token operations."""

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    def test_token_authentication_headers(self):
        """Test service account token authentication via headers."""
        headers = service_account_auth.get_service_account_headers()
        assert headers == {
            'Authorization': 'Bearer sky_test_token',
        }

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_no_token_no_headers(self, mock_get_nested):
        """Test no headers when no token is available."""
        mock_get_nested.return_value = None

        headers = service_account_auth.get_service_account_headers()
        assert headers == {}

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    def test_config_integration(self):
        """Test service account integration with configuration."""
        # Environment variable should take precedence
        token = service_account_auth._get_service_account_token()
        assert token == 'sky_test_token'

        # Headers should be properly formatted
        headers = service_account_auth.get_service_account_headers()
        assert 'Authorization' in headers
        assert headers['Authorization'].startswith('Bearer ')

    @mock.patch.dict(
        os.environ,
        {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_valid_token'})
    def test_headers_generation(self):
        """Test proper header generation with valid token."""
        headers = service_account_auth.get_service_account_headers()
        assert headers == {'Authorization': 'Bearer sky_valid_token'}

    @mock.patch.dict(os.environ,
                     {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'invalid_token'})
    def test_invalid_token_validation(self):
        """Test that invalid tokens are properly rejected."""
        try:
            service_account_auth.get_service_account_headers()
            assert False, "Should have raised ValueError for invalid token"
        except ValueError as e:
            assert 'Invalid service account token format' in str(e)

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    def test_authentication_flow(self):
        """Test the complete authentication flow."""
        # Get token
        token = service_account_auth._get_service_account_token()
        assert token == 'sky_test_token'

        # Generate headers
        headers = service_account_auth.get_service_account_headers()
        assert headers['Authorization'] == 'Bearer sky_test_token'
