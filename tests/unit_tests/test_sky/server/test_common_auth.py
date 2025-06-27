"""Unit tests for server common authentication functionality."""

import unittest.mock as mock

from sky.server import common


class TestServerCommonAuth:
    """Test cases for server common authentication functions."""

    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_with_service_account(
            self, mock_request, mock_get_cookie_jar, mock_get_headers):
        """Test authenticated request with service account token."""
        # Mock service account headers
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token'
        }

        # Mock the request
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        # Make the request
        response = common.make_authenticated_request(
            'GET', '/api/v1/status', server_url='https://api.example.com')

        # Verify URL construction (inline now, no separate function)
        expected_url = 'https://api.example.com/api/v1/status'
        mock_request.assert_called_once_with(
            'GET',
            expected_url,
            headers={'Authorization': 'Bearer sky_test_token'})

        # Should not call cookie jar since Bearer token is present
        mock_get_cookie_jar.assert_not_called()

        assert response == mock_response

    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_with_cookies(self, mock_request,
                                                     mock_get_cookie_jar,
                                                     mock_get_headers):
        """Test authenticated request with cookie authentication."""
        # Mock no service account headers
        mock_get_headers.return_value = {}

        # Mock cookie jar
        mock_cookies = mock.Mock()
        mock_get_cookie_jar.return_value = mock_cookies

        # Mock the request
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        # Make the request
        response = common.make_authenticated_request(
            'GET', 'api/v1/status', server_url='https://api.example.com')

        # Verify URL construction for path without leading slash
        expected_url = 'https://api.example.com/api/v1/status'
        mock_request.assert_called_once_with('GET',
                                             expected_url,
                                             headers={},
                                             cookies=mock_cookies)

        # Should call cookie jar since no Bearer token
        mock_get_cookie_jar.assert_called_once()

        assert response == mock_response

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_without_service_account(
            self, mock_request, mock_get_cookie_jar, mock_get_headers,
            mock_get_server_url):
        """Test authenticated request without service account token."""
        # Mock server URL
        mock_get_server_url.return_value = 'http://127.0.0.1:46580'

        # Mock no service account token
        mock_get_headers.return_value = {}
        mock_cookies = mock.Mock()
        mock_get_cookie_jar.return_value = mock_cookies

        # Mock response
        mock_response = mock.Mock()
        mock_request.return_value = mock_response

        # Make request
        result = common.make_authenticated_request('GET', '/api/v1/status')

        # Verify cookie authentication was used
        mock_get_cookie_jar.assert_called_once()
        expected_url = 'http://127.0.0.1:46580/api/v1/status'
        mock_request.assert_called_once_with('GET',
                                             expected_url,
                                             headers={},
                                             cookies=mock_cookies)

        assert result == mock_response

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_with_custom_headers(
            self, mock_request, mock_get_cookie_jar, mock_get_headers,
            mock_get_server_url):
        """Test authenticated request with custom headers merged."""
        # Mock server URL
        mock_get_server_url.return_value = 'http://127.0.0.1:46580'

        # Mock service account authentication
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token',
        }

        # Mock response
        mock_response = mock.Mock()
        mock_request.return_value = mock_response

        # Call with custom headers
        custom_headers = {'X-Custom-Header': 'custom_value'}
        result = common.make_authenticated_request('GET',
                                                   '/api/v1/status',
                                                   headers=custom_headers)

        # Verify headers were merged
        expected_headers = {
            'Authorization': 'Bearer sky_test_token',
            'X-Custom-Header': 'custom_value',
        }
        expected_url = 'http://127.0.0.1:46580/api/v1/status'
        mock_request.assert_called_once_with('GET',
                                             expected_url,
                                             headers=expected_headers)

        assert result == mock_response

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request_without_retry')
    def test_make_authenticated_request_without_retry(self, mock_request,
                                                      mock_get_cookie_jar,
                                                      mock_get_headers,
                                                      mock_get_server_url):
        """Test authenticated request without retry."""
        # Mock server URL
        mock_get_server_url.return_value = 'http://127.0.0.1:46580'

        # Mock no service account token
        mock_get_headers.return_value = {}
        mock_cookies = mock.Mock()
        mock_get_cookie_jar.return_value = mock_cookies

        # Mock response
        mock_response = mock.Mock()
        mock_request.return_value = mock_response

        # Call without retry
        result = common.make_authenticated_request('GET',
                                                   '/api/v1/status',
                                                   retry=False)

        # Verify request_without_retry was used
        expected_url = 'http://127.0.0.1:46580/api/v1/status'
        mock_request.assert_called_once_with('GET',
                                             expected_url,
                                             headers={},
                                             cookies=mock_cookies)
        assert result == mock_response

    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_with_existing_cookies(
            self, mock_request, mock_get_cookie_jar, mock_get_headers):
        """Test authenticated request with existing cookies provided."""
        # Mock no service account token
        mock_get_headers.return_value = {}

        # Mock response
        mock_response = mock.Mock()
        mock_request.return_value = mock_response

        # Call with existing cookies
        existing_cookies = mock.Mock()
        result = common.make_authenticated_request(
            'GET',
            '/api/v1/status',
            server_url='http://127.0.0.1:46580',
            cookies=existing_cookies)

        # Verify existing cookies were used, not cookie jar
        mock_get_cookie_jar.assert_not_called()
        expected_url = 'http://127.0.0.1:46580/api/v1/status'
        mock_request.assert_called_once_with('GET',
                                             expected_url,
                                             headers={},
                                             cookies=existing_cookies)

        assert result == mock_response

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

        # Mock response
        mock_response = mock.Mock()
        mock_request.return_value = mock_response

        custom_server_url = 'https://custom.api.com'
        result = common.make_authenticated_request('GET',
                                                   '/api/v1/status',
                                                   server_url=custom_server_url)

        # Verify custom server URL was used in inline URL construction
        expected_url = 'https://custom.api.com/api/v1/status'
        mock_request.assert_called_once_with(
            'GET',
            expected_url,
            headers={'Authorization': 'Bearer sky_test_token'})

        assert result == mock_response

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.common.rest.request')
    def test_make_authenticated_request_service_account_with_mixed_auth(
            self, mock_request, mock_get_cookie_jar, mock_get_headers,
            mock_get_server_url):
        """Test service account request doesn't use cookies even when available."""
        # Mock server URL
        mock_get_server_url.return_value = 'http://127.0.0.1:46580'

        # Mock service account authentication
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token',
        }
        mock_cookies = mock.Mock()
        mock_get_cookie_jar.return_value = mock_cookies

        # Mock response
        mock_response = mock.Mock()
        mock_request.return_value = mock_response

        # Call the function
        result = common.make_authenticated_request('GET', '/api/v1/status')

        # Verify service account auth was used, not cookies
        expected_url = 'http://127.0.0.1:46580/api/v1/status'
        mock_request.assert_called_once_with(
            'GET',
            expected_url,
            headers={'Authorization': 'Bearer sky_test_token'})

        # Cookie jar should not be called since Authorization header is present
        mock_get_cookie_jar.assert_not_called()

        assert result == mock_response
