"""Tests for service account authentication client module."""

import os
import unittest.mock as mock

import pytest

from sky.client import service_account_auth
from sky.skylet import constants


class TestServiceAccountAuth:
    """Test cases for service account authentication."""

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    def test_get_service_account_token_from_env(self):
        """Test getting service account token from environment variable."""
        token = service_account_auth._get_service_account_token()
        assert token == 'sky_test_token'

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_get_service_account_token_from_config(self, mock_get_nested):
        """Test getting service account token from config file."""
        mock_get_nested.return_value = 'sky_config_token'

        token = service_account_auth._get_service_account_token()
        assert token == 'sky_config_token'

        # Verify the correct config path is used
        mock_get_nested.assert_called_once_with(
            ('api_server', 'service_account_token'), default_value=None)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_no_service_account_token(self, mock_get_nested):
        """Test no token returned when none available."""
        mock_get_nested.return_value = None

        token = service_account_auth._get_service_account_token()
        assert token is None

    @mock.patch.dict(os.environ,
                     {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'invalid_token'})
    def test_invalid_token_format_env(self):
        """Test validation of token format from environment."""
        with pytest.raises(ValueError) as exc_info:
            service_account_auth._get_service_account_token()

        assert 'Invalid service account token format' in str(exc_info.value)
        assert 'Token must start with "sky_"' in str(exc_info.value)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_invalid_token_format_config(self, mock_get_nested):
        """Test validation of token format from config."""
        mock_get_nested.return_value = 'invalid_token'

        with pytest.raises(ValueError) as exc_info:
            service_account_auth._get_service_account_token()

        assert 'Invalid service account token format in config' in str(
            exc_info.value)
        assert 'Token must start with "sky_"' in str(exc_info.value)

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    def test_get_service_account_headers_with_token(self):
        """Test getting headers when token is available."""
        headers = service_account_auth.get_service_account_headers()
        assert headers == {'Authorization': 'Bearer sky_test_token'}

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_get_service_account_headers_no_token(self, mock_get_nested):
        """Test getting headers when no token is available."""
        mock_get_nested.return_value = None

        headers = service_account_auth.get_service_account_headers()
        assert headers == {}

    @mock.patch.dict(
        os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_test_token'})
    @mock.patch('sky.skypilot_config.get_nested')
    def test_env_variable_priority(self, mock_get_nested):
        """Test that environment variable takes priority over config."""
        mock_get_nested.return_value = 'sky_config_token'

        token = service_account_auth._get_service_account_token()
        # Should get env token, not config token
        assert token == 'sky_test_token'

        # Config should not be called when env var is present
        mock_get_nested.assert_not_called()

    @mock.patch.dict(
        os.environ,
        {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'sky_valid_token'})
    def test_headers_with_valid_env_token(self):
        """Test headers generation with valid environment token."""
        headers = service_account_auth.get_service_account_headers()
        assert headers == {'Authorization': 'Bearer sky_valid_token'}

    @mock.patch.dict(os.environ,
                     {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: 'invalid_token'})
    def test_headers_with_invalid_env_token(self):
        """Test headers generation fails with invalid environment token."""
        with pytest.raises(ValueError) as exc_info:
            service_account_auth.get_service_account_headers()

        assert 'Invalid service account token format' in str(exc_info.value)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_headers_with_invalid_config_token(self, mock_get_nested):
        """Test headers generation fails with invalid config token."""
        mock_get_nested.return_value = 'bad_token_format'

        with pytest.raises(ValueError) as exc_info:
            service_account_auth.get_service_account_headers()

        assert 'Invalid service account token format in config' in str(
            exc_info.value)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_empty_token_from_config(self, mock_get_nested):
        """Test empty/None token from config returns no headers."""
        mock_get_nested.return_value = None

        headers = service_account_auth.get_service_account_headers()
        assert headers == {}

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.skypilot_config.get_nested')
    def test_valid_config_token(self, mock_get_nested):
        """Test valid token from config works correctly."""
        mock_get_nested.return_value = 'sky_valid_config_token'

        headers = service_account_auth.get_service_account_headers()
        assert headers == {'Authorization': 'Bearer sky_valid_config_token'}

    @mock.patch.dict(os.environ, {constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR: ''})
    def test_empty_env_token_falls_back_to_config(self):
        """Test empty environment token falls back to config."""
        with mock.patch('sky.skypilot_config.get_nested') as mock_get_nested:
            mock_get_nested.return_value = 'sky_config_fallback'

            token = service_account_auth._get_service_account_token()
            assert token == 'sky_config_fallback'

            # Should check config since env token is empty
            mock_get_nested.assert_called_once_with(
                ('api_server', 'service_account_token'), default_value=None)
