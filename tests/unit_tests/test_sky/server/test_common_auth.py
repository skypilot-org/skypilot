"""Unit tests for authenticated request functionality in server/common.py."""

from unittest import mock

import pytest
import requests

from sky.server import common as server_common


class TestMakeAuthenticatedRequest:
    """Test cases for make_authenticated_request function."""

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch(
        'sky.client.service_account_auth.get_api_url_with_service_account_path')
    @mock.patch('sky.server.rest.request')
    def test_make_authenticated_request_with_service_account(
            self, mock_rest_request, mock_get_url, mock_get_headers,
            mock_get_server_url):
        """Test authenticated request with service account token."""
        mock_get_server_url.return_value = 'https://api.example.com'
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token',
            'X-Service-Account-Auth': 'true'
        }
        mock_get_url.return_value = 'https://api.example.com/sa/api/v1/status'
        mock_response = mock.Mock()
        mock_rest_request.return_value = mock_response

        result = server_common.make_authenticated_request(
            'GET', '/api/v1/status')

        assert result == mock_response
        mock_rest_request.assert_called_once_with(
            'GET',
            'https://api.example.com/sa/api/v1/status',
            headers={
                'Authorization': 'Bearer sky_test_token',
                'X-Service-Account-Auth': 'true'
            })

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.rest.request')
    def test_make_authenticated_request_with_cookies(self, mock_rest_request,
                                                     mock_get_cookies,
                                                     mock_get_headers,
                                                     mock_get_server_url):
        """Test authenticated request with cookie authentication."""
        mock_get_server_url.return_value = 'https://api.example.com'
        mock_get_headers.return_value = {}  # No service account token
        mock_cookies = requests.cookies.RequestsCookieJar()
        mock_get_cookies.return_value = mock_cookies
        mock_response = mock.Mock()
        mock_rest_request.return_value = mock_response

        result = server_common.make_authenticated_request(
            'POST', '/api/v1/launch')

        assert result == mock_response
        mock_rest_request.assert_called_once_with(
            'POST',
            'https://api.example.com/api/v1/launch',
            headers={},
            cookies=mock_cookies)

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch(
        'sky.client.service_account_auth.get_api_url_with_service_account_path')
    @mock.patch('sky.server.rest.request')
    def test_make_authenticated_request_custom_server_url(
            self, mock_rest_request, mock_get_url, mock_get_headers,
            mock_get_server_url):
        """Test authenticated request with custom server URL."""
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token',
            'X-Service-Account-Auth': 'true'
        }
        mock_get_url.return_value = 'https://custom.api.com/sa/api/v1/status'
        mock_response = mock.Mock()
        mock_rest_request.return_value = mock_response

        result = server_common.make_authenticated_request(
            'GET', '/api/v1/status', server_url='https://custom.api.com')

        assert result == mock_response
        # Should not call get_server_url when custom server_url is provided
        mock_get_server_url.assert_not_called()
        mock_get_url.assert_called_once_with('https://custom.api.com',
                                             '/api/v1/status')

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.rest.request')
    def test_make_authenticated_request_with_existing_headers(
            self, mock_rest_request, mock_get_headers, mock_get_server_url):
        """Test authenticated request merging with existing headers."""
        mock_get_server_url.return_value = 'https://api.example.com'
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token'
        }
        mock_response = mock.Mock()
        mock_rest_request.return_value = mock_response

        existing_headers = {'Content-Type': 'application/json'}

        result = server_common.make_authenticated_request(
            'POST',
            '/api/v1/launch',
            headers=existing_headers,
            json={'cluster_name': 'test'})

        assert result == mock_response
        # Headers should be merged
        expected_headers = {
            'Authorization': 'Bearer sky_test_token',
            'Content-Type': 'application/json'
        }
        mock_rest_request.assert_called_once()
        call_args = mock_rest_request.call_args
        assert call_args[1]['headers'] == expected_headers
        assert call_args[1]['json'] == {'cluster_name': 'test'}

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.rest.request_without_retry')
    def test_make_authenticated_request_without_retry(
            self, mock_rest_request_no_retry, mock_get_headers,
            mock_get_server_url):
        """Test authenticated request without retry."""
        mock_get_server_url.return_value = 'https://api.example.com'
        mock_get_headers.return_value = {}
        mock_response = mock.Mock()
        mock_rest_request_no_retry.return_value = mock_response

        result = server_common.make_authenticated_request('GET',
                                                          '/api/v1/status',
                                                          retry=False)

        assert result == mock_response
        mock_rest_request_no_retry.assert_called_once()
        # Should only allow GET requests without retry
        assert mock_rest_request_no_retry.call_args[0][0] == 'GET'

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    def test_make_authenticated_request_non_get_without_retry_assertion(
            self, mock_get_headers, mock_get_server_url):
        """Test that non-GET requests without retry raise assertion error."""
        mock_get_server_url.return_value = 'https://api.example.com'
        mock_get_headers.return_value = {}

        with pytest.raises(AssertionError,
                           match='Only GET requests can be done without retry'):
            server_common.make_authenticated_request('POST',
                                                     '/api/v1/launch',
                                                     retry=False)

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.rest.request')
    def test_make_authenticated_request_path_variations(self, mock_rest_request,
                                                        mock_get_headers,
                                                        mock_get_server_url):
        """Test authenticated request with various path formats."""
        mock_get_server_url.return_value = 'https://api.example.com'
        mock_get_headers.return_value = {}
        mock_response = mock.Mock()
        mock_rest_request.return_value = mock_response

        # Test path with leading slash
        server_common.make_authenticated_request('GET', '/api/v1/status')
        mock_rest_request.assert_called_with(
            'GET',
            'https://api.example.com/api/v1/status',
            headers={},
            cookies=mock.ANY)

        # Test path without leading slash
        mock_rest_request.reset_mock()
        server_common.make_authenticated_request('GET', 'api/v1/status')
        mock_rest_request.assert_called_with(
            'GET',
            'https://api.example.com/api/v1/status',
            headers={},
            cookies=mock.ANY)

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch(
        'sky.client.service_account_auth.get_api_url_with_service_account_path')
    @mock.patch('sky.server.rest.request')
    def test_make_authenticated_request_service_account_overrides_cookies(
            self, mock_rest_request, mock_get_url, mock_get_headers,
            mock_get_server_url):
        """Test that service account auth takes priority over cookies."""
        mock_get_server_url.return_value = 'https://api.example.com'
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token'
        }
        mock_get_url.return_value = 'https://api.example.com/sa/api/v1/status'
        mock_response = mock.Mock()
        mock_rest_request.return_value = mock_response

        # Pass cookies parameter, but it should be ignored when using service account
        custom_cookies = requests.cookies.RequestsCookieJar()

        result = server_common.make_authenticated_request(
            'GET', '/api/v1/status', cookies=custom_cookies)

        assert result == mock_response
        # Should use service account path and not include cookies
        mock_rest_request.assert_called_once_with(
            'GET',
            'https://api.example.com/sa/api/v1/status',
            headers={'Authorization': 'Bearer sky_test_token'},
            cookies=custom_cookies)

    @mock.patch('sky.server.common.get_server_url')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.server.rest.request')
    def test_make_authenticated_request_no_cookies_param_uses_default(
            self, mock_rest_request, mock_get_cookies, mock_get_headers,
            mock_get_server_url):
        """Test that default cookies are used when no cookies param provided."""
        mock_get_server_url.return_value = 'https://api.example.com'
        mock_get_headers.return_value = {}  # No service account token
        mock_default_cookies = requests.cookies.RequestsCookieJar()
        mock_get_cookies.return_value = mock_default_cookies
        mock_response = mock.Mock()
        mock_rest_request.return_value = mock_response

        result = server_common.make_authenticated_request(
            'GET', '/api/v1/status')

        assert result == mock_response
        mock_rest_request.assert_called_once_with(
            'GET',
            'https://api.example.com/api/v1/status',
            headers={},
            cookies=mock_default_cookies)
