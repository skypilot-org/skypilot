"""Unit tests for server common authenticated request functionality."""

import unittest
from unittest import mock

from sky.client import service_account_auth
from sky.server import common as server_common


class TestMakeAuthenticatedRequest(unittest.TestCase):
    """Test make_authenticated_request function."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock the requests module
        self.mock_requests = mock.patch('sky.server.common.requests').start()
        self.mock_request = self.mock_requests.request
        
        # Mock server URL
        self.mock_get_server_url = mock.patch.object(
            server_common, 'get_server_url'
        ).start()
        self.mock_get_server_url.return_value = 'http://test-server.com'
        
        # Mock cookie functions
        self.mock_get_cookie_jar = mock.patch.object(
            server_common, 'get_api_cookie_jar'
        ).start()
        self.mock_get_cookie_jar.return_value = {'session': 'test-cookie'}

    def tearDown(self):
        """Clean up test fixtures."""
        mock.patch.stopall()

    @mock.patch.object(service_account_auth, 'get_service_account_headers')
    @mock.patch.object(service_account_auth, 'get_api_url_with_service_account_path')
    def test_make_authenticated_request_with_service_account(self, mock_get_url, mock_get_headers):
        """Test authenticated request with service account token."""
        # Mock service account authentication
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token',
            'X-Service-Account-Auth': 'true'
        }
        mock_get_url.return_value = 'http://test-server.com/sa/api/test'
        
        # Make request
        server_common.make_authenticated_request('GET', '/api/test')
        
        # Verify service account path and headers were used
        mock_get_headers.assert_called_once()
        mock_get_url.assert_called_once_with('http://test-server.com', '/api/test')
        
        # Verify request was made with correct parameters
        self.mock_request.assert_called_once_with(
            'GET', 
            'http://test-server.com/sa/api/test',
            headers={
                'Authorization': 'Bearer sky_test_token',
                'X-Service-Account-Auth': 'true'
            }
        )

    @mock.patch.object(service_account_auth, 'get_service_account_headers')
    def test_make_authenticated_request_with_cookies(self, mock_get_headers):
        """Test authenticated request with cookie-based authentication."""
        # Mock no service account token
        mock_get_headers.return_value = {}
        
        # Make request
        server_common.make_authenticated_request('POST', '/api/test')
        
        # Verify cookie authentication was used
        self.mock_request.assert_called_once_with(
            'POST',
            'http://test-server.com/api/test',
            headers={},
            cookies={'session': 'test-cookie'}
        )

    @mock.patch.object(service_account_auth, 'get_service_account_headers')
    def test_make_authenticated_request_custom_server_url(self, mock_get_headers):
        """Test authenticated request with custom server URL."""
        mock_get_headers.return_value = {}
        custom_url = 'http://custom-server.com'
        
        # Make request with custom server URL
        server_common.make_authenticated_request('GET', '/api/test', server_url=custom_url)
        
        # Verify custom URL was used
        self.mock_request.assert_called_once_with(
            'GET',
            'http://custom-server.com/api/test',
            headers={},
            cookies={'session': 'test-cookie'}
        )
        
        # Verify get_server_url was not called when custom URL provided
        self.mock_get_server_url.assert_not_called()

    @mock.patch.object(service_account_auth, 'get_service_account_headers')
    def test_make_authenticated_request_merge_headers(self, mock_get_headers):
        """Test that existing headers are merged with auth headers."""
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token',
            'X-Service-Account-Auth': 'true'
        }
        
        # Make request with existing headers
        existing_headers = {'Content-Type': 'application/json', 'X-Custom': 'value'}
        server_common.make_authenticated_request(
            'POST', '/api/test', headers=existing_headers
        )
        
        # Verify headers were merged
        expected_headers = {
            'Content-Type': 'application/json',
            'X-Custom': 'value',
            'Authorization': 'Bearer sky_test_token',
            'X-Service-Account-Auth': 'true'
        }
        
        call_args = self.mock_request.call_args
        self.assertEqual(call_args[1]['headers'], expected_headers)

    @mock.patch.object(service_account_auth, 'get_service_account_headers')
    def test_make_authenticated_request_header_override(self, mock_get_headers):
        """Test that service account headers can override existing headers."""
        mock_get_headers.return_value = {
            'Authorization': 'Bearer sky_test_token',
            'Content-Type': 'application/jwt'  # This should override existing
        }
        
        # Make request with conflicting headers
        existing_headers = {'Content-Type': 'application/json'}
        server_common.make_authenticated_request(
            'POST', '/api/test', headers=existing_headers
        )
        
        # Verify service account headers took precedence
        expected_headers = {
            'Authorization': 'Bearer sky_test_token',
            'Content-Type': 'application/jwt'  # Service account header wins
        }
        
        call_args = self.mock_request.call_args
        self.assertEqual(call_args[1]['headers'], expected_headers)

    @mock.patch.object(service_account_auth, 'get_service_account_headers')
    def test_make_authenticated_request_preserve_cookies(self, mock_get_headers):
        """Test that existing cookies are preserved when not using service account."""
        mock_get_headers.return_value = {}
        
        # Make request with existing cookies
        existing_cookies = {'custom': 'cookie-value'}
        server_common.make_authenticated_request(
            'GET', '/api/test', cookies=existing_cookies
        )
        
        # Verify existing cookies were used instead of API cookie jar
        self.mock_request.assert_called_once_with(
            'GET',
            'http://test-server.com/api/test',
            headers={},
            cookies=existing_cookies
        )
        
        # Verify API cookie jar was not called
        self.mock_get_cookie_jar.assert_not_called()

    @mock.patch.object(service_account_auth, 'get_service_account_headers')
    def test_make_authenticated_request_additional_kwargs(self, mock_get_headers):
        """Test that additional kwargs are passed through to requests."""
        mock_get_headers.return_value = {}
        
        # Make request with additional kwargs
        server_common.make_authenticated_request(
            'POST', '/api/test',
            timeout=30,
            json={'key': 'value'},
            verify=False
        )
        
        # Verify all kwargs were passed through
        self.mock_request.assert_called_once_with(
            'POST',
            'http://test-server.com/api/test',
            headers={},
            cookies={'session': 'test-cookie'},
            timeout=30,
            json={'key': 'value'},
            verify=False
        )

    @mock.patch.object(service_account_auth, 'get_service_account_headers')
    def test_make_authenticated_request_path_handling(self, mock_get_headers):
        """Test proper handling of paths with and without leading slash."""
        mock_get_headers.return_value = {}
        
        # Test path without leading slash
        server_common.make_authenticated_request('GET', 'api/test')
        first_call = self.mock_request.call_args
        
        # Test path with leading slash
        server_common.make_authenticated_request('GET', '/api/test')
        second_call = self.mock_request.call_args
        
        # Both should result in the same URL
        self.assertEqual(first_call[0][1], 'http://test-server.com/api/test')
        self.assertEqual(second_call[0][1], 'http://test-server.com/api/test')

    @mock.patch.object(service_account_auth, 'get_service_account_headers')
    @mock.patch.object(service_account_auth, 'get_api_url_with_service_account_path')
    def test_make_authenticated_request_service_account_url_construction(
        self, mock_get_url, mock_get_headers
    ):
        """Test URL construction for service account requests."""
        # Mock service account authentication
        mock_get_headers.return_value = {'Authorization': 'Bearer sky_test_token'}
        mock_get_url.return_value = 'http://test-server.com/sa/api/test'
        
        # Make request
        server_common.make_authenticated_request('GET', '/api/test')
        
        # Verify service account URL construction was called correctly
        mock_get_url.assert_called_once_with('http://test-server.com', '/api/test')

    def test_make_authenticated_request_return_value(self):
        """Test that the function returns the response from requests."""
        # Mock response
        mock_response = mock.MagicMock()
        self.mock_request.return_value = mock_response
        
        with mock.patch.object(service_account_auth, 'get_service_account_headers') as mock_get_headers:
            mock_get_headers.return_value = {}
            
            # Make request
            result = server_common.make_authenticated_request('GET', '/api/test')
            
            # Verify response is returned
            self.assertEqual(result, mock_response)


if __name__ == '__main__':
    unittest.main()