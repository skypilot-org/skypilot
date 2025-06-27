"""Unit tests for server common authentication functionality."""

import unittest.mock as mock

from sky.server import common


class TestServerCommonAuth:
    """Test cases for server common authentication functions."""

    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.client.service_account_auth.get_api_url')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_with_service_account(
            self, mock_request, mock_get_cookie_jar, mock_get_url,
            mock_get_headers):
        """Test authenticated request with service account token."""
        # Mock service account authentication
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token',
        }
        mock_get_url.return_value = 'https://api.example.com/api/v1/status'

        # Call the function
        result = common.make_authenticated_request('GET', '/api/v1/status')

        # Verify the correct URL and headers were used
        mock_get_url.assert_called_once_with('http://127.0.0.1:46580',
                                             '/api/v1/status')
        mock_request.assert_called_once_with(
            'GET',
            'https://api.example.com/api/v1/status',
            headers={'Authorization': 'Bearer sky_test_token'})

        # Verify cookie jar was not used (service account doesn't need cookies)
        mock_get_cookie_jar.assert_not_called()

        assert result == mock_request.return_value

    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_without_service_account(
            self, mock_request, mock_get_cookie_jar, mock_get_headers):
        """Test authenticated request without service account token."""
        # Mock no service account token
        mock_get_headers.return_value = {}
        mock_get_cookie_jar.return_value = 'mock_cookies'

        # Call the function
        with mock.patch(
                'sky.client.service_account_auth.get_api_url') as mock_get_url:
            mock_get_url.return_value = 'http://127.0.0.1:46580/api/v1/status'
            result = common.make_authenticated_request('GET', '/api/v1/status')

        # Verify cookie authentication was used
        mock_get_cookie_jar.assert_called_once()
        mock_request.assert_called_once_with(
            'GET',
            'http://127.0.0.1:46580/api/v1/status',
            headers={},
            cookies='mock_cookies')

        assert result == mock_request.return_value

    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_with_custom_headers(
            self, mock_request, mock_get_cookie_jar, mock_get_headers):
        """Test authenticated request with custom headers merged."""
        # Mock service account authentication
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token',
        }

        # Call with custom headers
        custom_headers = {'X-Custom-Header': 'custom_value'}
        with mock.patch(
                'sky.client.service_account_auth.get_api_url') as mock_get_url:
            mock_get_url.return_value = 'http://127.0.0.1:46580/api/v1/status'
            result = common.make_authenticated_request('GET',
                                                       '/api/v1/status',
                                                       headers=custom_headers)

        # Verify headers were merged
        expected_headers = {
            'Authorization': 'Bearer sky_test_token',
            'X-Custom-Header': 'custom_value',
        }
        mock_request.assert_called_once_with(
            'GET',
            'http://127.0.0.1:46580/api/v1/status',
            headers=expected_headers)

        assert result == mock_request.return_value

    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request_without_retry')
    def test_make_authenticated_request_without_retry(self, mock_request,
                                                      mock_get_cookie_jar,
                                                      mock_get_headers):
        """Test authenticated request without retry."""
        # Mock no service account token
        mock_get_headers.return_value = {}

        # Call without retry
        with mock.patch(
                'sky.client.service_account_auth.get_api_url') as mock_get_url:
            mock_get_url.return_value = 'http://127.0.0.1:46580/api/v1/status'
            result = common.make_authenticated_request('GET',
                                                       '/api/v1/status',
                                                       retry=False)

        # Verify request_without_retry was used
        mock_request.assert_called_once()
        assert result == mock_request.return_value

    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_with_existing_cookies(
            self, mock_request, mock_get_cookie_jar, mock_get_headers):
        """Test authenticated request with existing cookies provided."""
        # Mock no service account token
        mock_get_headers.return_value = {}

        # Call with existing cookies
        existing_cookies = 'existing_cookies'
        with mock.patch(
                'sky.client.service_account_auth.get_api_url') as mock_get_url:
            mock_get_url.return_value = 'http://127.0.0.1:46580/api/v1/status'
            result = common.make_authenticated_request('GET',
                                                       '/api/v1/status',
                                                       cookies=existing_cookies)

        # Verify existing cookies were used, not cookie jar
        mock_get_cookie_jar.assert_not_called()
        mock_request.assert_called_once_with(
            'GET',
            'http://127.0.0.1:46580/api/v1/status',
            headers={},
            cookies=existing_cookies)

        assert result == mock_request.return_value

    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_custom_server_url(
            self, mock_request, mock_get_cookie_jar, mock_get_headers):
        """Test authenticated request with custom server URL."""
        # Mock service account authentication
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token',
        }

        custom_server_url = 'https://custom.api.com'
        with mock.patch(
                'sky.client.service_account_auth.get_api_url') as mock_get_url:
            mock_get_url.return_value = 'https://custom.api.com/api/v1/status'
            result = common.make_authenticated_request(
                'GET', '/api/v1/status', server_url=custom_server_url)

        # Verify custom server URL was used
        mock_get_url.assert_called_once_with(custom_server_url,
                                             '/api/v1/status')
        mock_request.assert_called_once_with(
            'GET',
            'https://custom.api.com/api/v1/status',
            headers={'Authorization': 'Bearer sky_test_token'})

        assert result == mock_request.return_value

    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.client.service_account_auth.get_api_url')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_service_account_with_mixed_auth(
            self, mock_request, mock_get_cookie_jar, mock_get_url,
            mock_get_headers):
        """Test service account request doesn't use cookies even when available."""
        # Mock service account authentication
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token',
        }
        mock_get_url.return_value = 'https://api.example.com/api/v1/status'
        mock_get_cookie_jar.return_value = 'mock_cookies'

        # Call the function
        result = common.make_authenticated_request('GET', '/api/v1/status')

        # Verify service account auth was used, not cookies
        mock_get_url.assert_called_once_with('http://127.0.0.1:46580',
                                             '/api/v1/status')
        mock_request.assert_called_once_with(
            'GET',
            'https://api.example.com/api/v1/status',
            headers={'Authorization': 'Bearer sky_test_token'})

        # Cookie jar should not be called since Authorization header is present
        mock_get_cookie_jar.assert_not_called()

        assert result == mock_request.return_value
