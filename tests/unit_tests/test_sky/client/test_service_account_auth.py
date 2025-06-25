"""Unit tests for service account authentication functions."""

import os
import tempfile
import unittest
from unittest import mock

from sky import skypilot_config
from sky.client import service_account_auth


class TestServiceAccountAuth(unittest.TestCase):
    """Test service account authentication functions."""

    def setUp(self):
        """Set up test fixtures."""
        # Clear environment variables
        if 'SKYPILOT_TOKEN' in os.environ:
            del os.environ['SKYPILOT_TOKEN']

    def tearDown(self):
        """Clean up test fixtures."""
        # Clear environment variables
        if 'SKYPILOT_TOKEN' in os.environ:
            del os.environ['SKYPILOT_TOKEN']

    def test_get_service_account_token_from_env(self):
        """Test getting service account token from environment variable."""
        test_token = 'sky_test_token_12345'
        os.environ['SKYPILOT_TOKEN'] = test_token
        
        result = service_account_auth.get_service_account_token()
        self.assertEqual(result, test_token)

    def test_get_service_account_token_from_env_invalid_format(self):
        """Test getting invalid token from environment variable raises error."""
        test_token = 'invalid_token_format'
        os.environ['SKYPILOT_TOKEN'] = test_token
        
        with self.assertRaises(ValueError) as context:
            service_account_auth.get_service_account_token()
        
        self.assertIn('Invalid service account token format', str(context.exception))
        self.assertIn('Token must start with "sky_"', str(context.exception))

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_token_from_config(self, mock_get_config):
        """Test getting service account token from config file."""
        test_token = 'sky_config_token_67890'
        
        # Mock config object
        mock_config = mock.MagicMock()
        mock_config.get_nested.return_value = test_token
        mock_get_config.return_value = mock_config
        
        result = service_account_auth.get_service_account_token()
        
        self.assertEqual(result, test_token)
        mock_config.get_nested.assert_called_once_with(
            ('api_server', 'service_account_token'), default_value=None
        )

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_token_from_config_invalid_format(self, mock_get_config):
        """Test getting invalid token from config file raises error."""
        test_token = 'invalid_config_token'
        
        # Mock config object
        mock_config = mock.MagicMock()
        mock_config.get_nested.return_value = test_token
        mock_get_config.return_value = mock_config
        
        with self.assertRaises(ValueError) as context:
            service_account_auth.get_service_account_token()
        
        self.assertIn('Invalid service account token format in config file', str(context.exception))
        self.assertIn('Token must start with "sky_"', str(context.exception))

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_token_env_priority(self, mock_get_config):
        """Test that environment variable takes priority over config file."""
        env_token = 'sky_env_token_12345'
        config_token = 'sky_config_token_67890'
        
        # Set environment variable
        os.environ['SKYPILOT_TOKEN'] = env_token
        
        # Mock config object
        mock_config = mock.MagicMock()
        mock_config.get_nested.return_value = config_token
        mock_get_config.return_value = mock_config
        
        result = service_account_auth.get_service_account_token()
        
        # Should return env token, not config token
        self.assertEqual(result, env_token)
        # Should not call config method when env var is present
        mock_config.get_nested.assert_not_called()

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_token_none_found(self, mock_get_config):
        """Test when no service account token is found."""
        # Mock config object returning None
        mock_config = mock.MagicMock()
        mock_config.get_nested.return_value = None
        mock_get_config.return_value = mock_config
        
        result = service_account_auth.get_service_account_token()
        self.assertIsNone(result)

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_token_config_exception(self, mock_get_config):
        """Test handling of config file exceptions."""
        # Mock config raising an exception
        mock_get_config.side_effect = Exception("Config error")
        
        result = service_account_auth.get_service_account_token()
        self.assertIsNone(result)

    def test_get_service_account_headers_with_token(self):
        """Test getting headers when token is available."""
        test_token = 'sky_test_token_12345'
        os.environ['SKYPILOT_TOKEN'] = test_token
        
        result = service_account_auth.get_service_account_headers()
        
        expected_headers = {
            'Authorization': f'Bearer {test_token}',
            'X-Service-Account-Auth': 'true'
        }
        self.assertEqual(result, expected_headers)

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_service_account_headers_no_token(self, mock_get_config):
        """Test getting headers when no token is available."""
        # Mock config returning None
        mock_config = mock.MagicMock()
        mock_config.get_nested.return_value = None
        mock_get_config.return_value = mock_config
        
        result = service_account_auth.get_service_account_headers()
        self.assertEqual(result, {})

    def test_should_use_service_account_path_with_token(self):
        """Test should_use_service_account_path returns True when token available."""
        test_token = 'sky_test_token_12345'
        os.environ['SKYPILOT_TOKEN'] = test_token
        
        result = service_account_auth.should_use_service_account_path()
        self.assertTrue(result)

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_should_use_service_account_path_no_token(self, mock_get_config):
        """Test should_use_service_account_path returns False when no token available."""
        # Mock config returning None
        mock_config = mock.MagicMock()
        mock_config.get_nested.return_value = None
        mock_get_config.return_value = mock_config
        
        result = service_account_auth.should_use_service_account_path()
        self.assertFalse(result)

    def test_get_api_url_with_service_account_path_with_token(self):
        """Test getting API URL with service account path when token available."""
        test_token = 'sky_test_token_12345'
        os.environ['SKYPILOT_TOKEN'] = test_token
        
        base_url = 'http://example.com'
        path = '/api/v1/status'
        
        result = service_account_auth.get_api_url_with_service_account_path(base_url, path)
        expected = 'http://example.com/sa/api/v1/status'
        self.assertEqual(result, expected)

    def test_get_api_url_with_service_account_path_with_token_leading_slash(self):
        """Test getting API URL with service account path, handling leading slash."""
        test_token = 'sky_test_token_12345'
        os.environ['SKYPILOT_TOKEN'] = test_token
        
        base_url = 'http://example.com'
        path = 'api/v1/status'  # No leading slash
        
        result = service_account_auth.get_api_url_with_service_account_path(base_url, path)
        expected = 'http://example.com/sa/api/v1/status'
        self.assertEqual(result, expected)

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_api_url_with_service_account_path_no_token(self, mock_get_config):
        """Test getting API URL without service account path when no token available."""
        # Mock config returning None
        mock_config = mock.MagicMock()
        mock_config.get_nested.return_value = None
        mock_get_config.return_value = mock_config
        
        base_url = 'http://example.com'
        path = '/api/v1/status'
        
        result = service_account_auth.get_api_url_with_service_account_path(base_url, path)
        expected = 'http://example.com/api/v1/status'
        self.assertEqual(result, expected)

    @mock.patch('sky.skypilot_config.get_user_config')
    def test_get_api_url_with_service_account_path_no_token_no_leading_slash(self, mock_get_config):
        """Test getting API URL without service account path, path without leading slash."""
        # Mock config returning None
        mock_config = mock.MagicMock()
        mock_config.get_nested.return_value = None
        mock_get_config.return_value = mock_config
        
        base_url = 'http://example.com'
        path = 'api/v1/status'  # No leading slash
        
        result = service_account_auth.get_api_url_with_service_account_path(base_url, path)
        expected = 'http://example.com/api/v1/status'
        self.assertEqual(result, expected)


if __name__ == '__main__':
    unittest.main()