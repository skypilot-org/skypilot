"""Unit tests for caching behavior in sky/server/common.py."""

import threading
import time
from unittest import mock

import pytest
import requests

import sky
from sky import exceptions
from sky.server import common
from sky.server import constants as server_constants
from sky.server.common import ApiServerInfo
from sky.server.common import ApiServerStatus


class TestGetApiServerStatusCache:
    """Tests for get_api_server_status() TTL cache behavior."""

    def setup_method(self):
        """Clear the cache before each test."""
        common.get_api_server_status.cache_clear()

    def teardown_method(self):
        """Clear the cache after each test."""
        common.get_api_server_status.cache_clear()

    @mock.patch('sky.server.common.set_api_cookie_jar')
    @mock.patch('sky.server.common.versions.check_compatibility_at_client')
    @mock.patch('sky.server.common.make_authenticated_request')
    def test_cache_hit_within_ttl(self, mock_request, mock_check_compat,
                                  mock_set_cookie):
        """Test that repeated calls within TTL return cached result."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.history = []
        mock_response.cookies = requests.cookies.RequestsCookieJar()
        mock_response.json.return_value = {
            'status': ApiServerStatus.HEALTHY.value,
            'api_version': server_constants.API_VERSION,
            'version': sky.__version__,
            'version_on_disk': sky.__version__,
            'commit': sky.__commit__,
            'user': {},
            'basic_auth_enabled': False,
        }
        mock_request.return_value = mock_response
        mock_check_compat.return_value = mock.Mock(error=None)

        # First call
        result1 = common.get_api_server_status()
        assert result1.status == ApiServerStatus.HEALTHY

        # Second call should use cache (no new request made)
        result2 = common.get_api_server_status()
        assert result2.status == ApiServerStatus.HEALTHY

        # Only one request should have been made (cached)
        assert mock_request.call_count == 1

    @mock.patch('sky.server.common.set_api_cookie_jar')
    @mock.patch('sky.server.common.versions.check_compatibility_at_client')
    @mock.patch('sky.server.common.make_authenticated_request')
    def test_cache_miss_after_ttl(self, mock_request, mock_check_compat,
                                  mock_set_cookie):
        """Test that cache expires after TTL."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.history = []
        mock_response.cookies = requests.cookies.RequestsCookieJar()
        mock_response.json.return_value = {
            'status': ApiServerStatus.HEALTHY.value,
            'api_version': server_constants.API_VERSION,
            'version': sky.__version__,
            'version_on_disk': sky.__version__,
            'commit': sky.__commit__,
            'user': {},
            'basic_auth_enabled': False,
        }
        mock_request.return_value = mock_response
        mock_check_compat.return_value = mock.Mock(error=None)

        # First call
        result1 = common.get_api_server_status()
        assert result1.status == ApiServerStatus.HEALTHY
        assert mock_request.call_count == 1

        # Manually clear cache to simulate TTL expiration
        common.get_api_server_status.cache_clear()

        # Next call should make a new request
        result2 = common.get_api_server_status()
        assert result2.status == ApiServerStatus.HEALTHY
        assert mock_request.call_count == 2

    @mock.patch('sky.server.common.set_api_cookie_jar')
    @mock.patch('sky.server.common.versions.check_compatibility_at_client')
    @mock.patch('sky.server.common.make_authenticated_request')
    def test_cache_clear_method(self, mock_request, mock_check_compat,
                                mock_set_cookie):
        """Test that cache_clear() properly clears the cache."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.history = []
        mock_response.cookies = requests.cookies.RequestsCookieJar()
        mock_response.json.return_value = {
            'status': ApiServerStatus.HEALTHY.value,
            'api_version': server_constants.API_VERSION,
            'version': sky.__version__,
            'version_on_disk': sky.__version__,
            'commit': sky.__commit__,
            'user': {},
            'basic_auth_enabled': False,
        }
        mock_request.return_value = mock_response
        mock_check_compat.return_value = mock.Mock(error=None)

        # First call
        common.get_api_server_status()
        assert mock_request.call_count == 1

        # Clear cache and call again
        common.get_api_server_status.cache_clear()
        common.get_api_server_status()
        assert mock_request.call_count == 2

    @mock.patch('sky.server.common.make_authenticated_request')
    def test_cache_per_endpoint(self, mock_request):
        """Test that different endpoints have separate cache entries."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.history = []
        mock_response.cookies = requests.cookies.RequestsCookieJar()
        mock_response.json.return_value = {
            'status': ApiServerStatus.HEALTHY.value,
            'api_version': server_constants.API_VERSION,
            'version': sky.__version__,
            'version_on_disk': sky.__version__,
            'commit': sky.__commit__,
            'user': {},
            'basic_auth_enabled': False,
        }
        mock_request.return_value = mock_response

        with mock.patch('sky.server.common.set_api_cookie_jar'), \
             mock.patch('sky.server.common.versions.check_compatibility_at_client',
                       return_value=mock.Mock(error=None)):
            # Call with different endpoints
            common.get_api_server_status('http://endpoint1:46580')
            common.get_api_server_status('http://endpoint2:46580')

            # Both should make separate requests
            assert mock_request.call_count == 2


class TestHandleNon200ServerStatus:
    """Tests for _handle_non_200_server_status()."""

    def test_handles_401_unauthorized(self):
        """Test that 401 response returns NEEDS_AUTH status."""
        mock_response = mock.Mock()
        mock_response.status_code = 401

        result = common._handle_non_200_server_status(mock_response)

        assert result.status == ApiServerStatus.NEEDS_AUTH

    def test_handles_400_version_mismatch(self):
        """Test that 400 with version_mismatch error is handled."""
        mock_response = mock.Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            'error': ApiServerStatus.VERSION_MISMATCH.value,
            'message': 'Version mismatch detected'
        }

        result = common._handle_non_200_server_status(mock_response)

        assert result.status == ApiServerStatus.VERSION_MISMATCH
        assert result.error == 'Version mismatch detected'

    def test_handles_400_without_version_mismatch(self):
        """Test that 400 without version_mismatch returns UNHEALTHY."""
        mock_response = mock.Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = {'error': 'other_error'}

        result = common._handle_non_200_server_status(mock_response)

        assert result.status == ApiServerStatus.UNHEALTHY

    def test_handles_400_json_decode_error(self):
        """Test that 400 with JSON decode error returns UNHEALTHY."""
        mock_response = mock.Mock()
        mock_response.status_code = 400
        mock_response.json.side_effect = requests.JSONDecodeError(
            'test', 'test', 0)

        result = common._handle_non_200_server_status(mock_response)

        assert result.status == ApiServerStatus.UNHEALTHY

    def test_handles_500_internal_error(self):
        """Test that 500 response returns UNHEALTHY status."""
        mock_response = mock.Mock()
        mock_response.status_code = 500

        result = common._handle_non_200_server_status(mock_response)

        assert result.status == ApiServerStatus.UNHEALTHY


class TestGetApiServerStatusRetry:
    """Tests for get_api_server_status() retry behavior on timeout."""

    def setup_method(self):
        common.get_api_server_status.cache_clear()

    def teardown_method(self):
        common.get_api_server_status.cache_clear()

    @mock.patch('sky.server.common.make_authenticated_request')
    def test_retries_on_timeout_up_to_limit(self, mock_request):
        """Test that timeouts are retried up to RETRY_COUNT_ON_TIMEOUT."""
        mock_request.side_effect = requests.exceptions.Timeout('Timeout')

        result = common.get_api_server_status()

        assert result.status == ApiServerStatus.UNHEALTHY
        # Should retry RETRY_COUNT_ON_TIMEOUT times
        assert mock_request.call_count == common.RETRY_COUNT_ON_TIMEOUT

    @mock.patch('sky.server.common.set_api_cookie_jar')
    @mock.patch('sky.server.common.versions.check_compatibility_at_client')
    @mock.patch('sky.server.common.make_authenticated_request')
    def test_succeeds_after_retry(self, mock_request, mock_check_compat,
                                  mock_set_cookie):
        """Test that success after retry returns healthy status."""
        # First call times out, second succeeds
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.history = []
        mock_response.cookies = requests.cookies.RequestsCookieJar()
        mock_response.json.return_value = {
            'status': ApiServerStatus.HEALTHY.value,
            'api_version': server_constants.API_VERSION,
            'version': sky.__version__,
            'version_on_disk': sky.__version__,
            'commit': sky.__commit__,
            'user': {},
            'basic_auth_enabled': False,
        }
        mock_request.side_effect = [
            requests.exceptions.Timeout('Timeout'), mock_response
        ]
        mock_check_compat.return_value = mock.Mock(error=None)

        result = common.get_api_server_status()

        assert result.status == ApiServerStatus.HEALTHY
        assert mock_request.call_count == 2

    @mock.patch('sky.server.common.make_authenticated_request')
    def test_connection_error_returns_unhealthy(self, mock_request):
        """Test that connection error returns UNHEALTHY status immediately."""
        mock_request.side_effect = requests.exceptions.ConnectionError(
            'Connection refused')

        result = common.get_api_server_status()

        assert result.status == ApiServerStatus.UNHEALTHY
        # Connection error should not retry
        assert mock_request.call_count == 1


class TestMakeAuthenticatedRequest:
    """Tests for make_authenticated_request()."""

    @mock.patch('sky.server.common.rest.request')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_server_url')
    def test_uses_service_account_headers(self, mock_server_url,
                                          mock_sa_headers, mock_cookie_jar,
                                          mock_request):
        """Test that service account headers are used."""
        mock_server_url.return_value = 'http://test:46580'
        mock_sa_headers.return_value = {'Authorization': 'Bearer token123'}
        mock_cookie_jar.return_value = requests.cookies.RequestsCookieJar()
        mock_response = mock.Mock()
        mock_request.return_value = mock_response

        result = common.make_authenticated_request('GET', '/api/test')

        assert result == mock_response
        # Should not use cookie jar when Bearer token is present
        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args[1]
        assert 'cookies' not in call_kwargs

    @mock.patch('sky.server.common.rest.request')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_server_url')
    def test_falls_back_to_cookie_auth(self, mock_server_url, mock_sa_headers,
                                       mock_cookie_jar, mock_request):
        """Test that cookie auth is used when no service account."""
        mock_server_url.return_value = 'http://test:46580'
        mock_sa_headers.return_value = {}  # No service account
        cookie_jar = requests.cookies.RequestsCookieJar()
        mock_cookie_jar.return_value = cookie_jar
        mock_response = mock.Mock()
        mock_request.return_value = mock_response

        result = common.make_authenticated_request('GET', '/api/test')

        assert result == mock_response
        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args[1]
        assert 'cookies' in call_kwargs

    @mock.patch('sky.server.common.rest.request_without_retry')
    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers')
    @mock.patch('sky.server.common.get_server_url')
    def test_no_retry_when_specified(self, mock_server_url, mock_sa_headers,
                                     mock_cookie_jar, mock_request):
        """Test that retry=False uses request_without_retry."""
        mock_server_url.return_value = 'http://test:46580'
        mock_sa_headers.return_value = {}
        mock_cookie_jar.return_value = requests.cookies.RequestsCookieJar()
        mock_response = mock.Mock()
        mock_request.return_value = mock_response

        result = common.make_authenticated_request('GET',
                                                   '/api/test',
                                                   retry=False)

        assert result == mock_response
        mock_request.assert_called_once()


class TestGetRequestId:
    """Tests for get_request_id()."""

    def test_returns_request_id_from_header(self):
        """Test that request ID is returned from X-Skypilot-Request-ID."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {'X-Skypilot-Request-ID': 'test-id-123'}

        result = common.get_request_id(mock_response)

        assert result == 'test-id-123'

    def test_falls_back_to_x_request_id(self):
        """Test that X-Request-ID is used as fallback."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {'X-Request-ID': 'fallback-id-456'}

        result = common.get_request_id(mock_response)

        assert result == 'fallback-id-456'

    def test_raises_on_missing_request_id(self):
        """Test that RuntimeError is raised when no request ID."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.text = 'No request ID'

        with pytest.raises(RuntimeError, match='Failed to get request ID'):
            common.get_request_id(mock_response)

    def test_raises_on_http_error(self):
        """Test that HTTP errors are raised."""
        mock_response = mock.Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            'Server error')

        with pytest.raises(requests.HTTPError):
            common.get_request_id(mock_response)


class TestGetStreamRequestId:
    """Tests for get_stream_request_id()."""

    def test_returns_stream_request_id(self):
        """Test that stream request ID is returned from header."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {
            server_constants.STREAM_REQUEST_HEADER: 'stream-id-789'
        }

        result = common.get_stream_request_id(mock_response)

        assert result == 'stream-id-789'

    def test_returns_none_when_no_header(self):
        """Test that None is returned when no stream header."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {}

        result = common.get_stream_request_id(mock_response)

        assert result is None


class TestCheckServerHealthy:
    """Tests for check_server_healthy()."""

    def setup_method(self):
        common.get_api_server_status.cache_clear()
        # Reset the global hint flag
        common.hinted_for_server_install_version_mismatch = False

    def teardown_method(self):
        common.get_api_server_status.cache_clear()
        common.hinted_for_server_install_version_mismatch = False

    @mock.patch('sky.server.common.get_api_server_status')
    def test_returns_healthy_status(self, mock_get_status):
        """Test that healthy status is returned."""
        mock_get_status.return_value = ApiServerInfo(
            status=ApiServerStatus.HEALTHY,
            api_version=server_constants.API_VERSION,
            version=sky.__version__,
            version_on_disk=sky.__version__,
            commit=sky.__commit__)

        status, info = common.check_server_healthy()

        assert status == ApiServerStatus.HEALTHY
        assert info.status == ApiServerStatus.HEALTHY

    @mock.patch('sky.server.common.get_api_server_status')
    def test_returns_needs_auth_status(self, mock_get_status):
        """Test that needs_auth status is returned."""
        mock_get_status.return_value = ApiServerInfo(
            status=ApiServerStatus.NEEDS_AUTH)

        status, info = common.check_server_healthy()

        assert status == ApiServerStatus.NEEDS_AUTH

    @mock.patch('sky.server.common.get_api_server_status')
    def test_raises_on_version_mismatch(self, mock_get_status):
        """Test that version mismatch raises exception."""
        mock_get_status.return_value = ApiServerInfo(
            status=ApiServerStatus.VERSION_MISMATCH,
            error='Version mismatch error')

        with pytest.raises(exceptions.APIVersionMismatchError):
            common.check_server_healthy()

    @mock.patch('sky.server.common.get_api_server_status')
    def test_raises_on_unhealthy(self, mock_get_status):
        """Test that unhealthy status raises exception."""
        mock_get_status.return_value = ApiServerInfo(
            status=ApiServerStatus.UNHEALTHY)

        with pytest.raises(exceptions.ApiServerConnectionError):
            common.check_server_healthy()

    @mock.patch('sky.server.common.logger')
    @mock.patch('sky.server.common.get_api_server_status')
    def test_warns_on_version_on_disk_mismatch(self, mock_get_status,
                                               mock_logger):
        """Test warning when version differs from version_on_disk."""
        mock_get_status.return_value = ApiServerInfo(
            status=ApiServerStatus.HEALTHY,
            api_version=server_constants.API_VERSION,
            version='1.0.0',
            version_on_disk='1.0.1',  # Different from version
            commit=sky.__commit__)

        common.check_server_healthy()

        # Should have logged a warning
        mock_logger.warning.assert_called()


class TestIsApiServerLocal:
    """Tests for is_api_server_local()."""

    def setup_method(self):
        common.is_api_server_local.cache_clear()
        common.get_server_url.cache_clear()

    def teardown_method(self):
        common.is_api_server_local.cache_clear()
        common.get_server_url.cache_clear()

    @mock.patch('sky.server.common.get_server_url')
    def test_returns_true_for_localhost(self, mock_get_url):
        """Test that localhost is recognized as local."""
        mock_get_url.return_value = 'http://127.0.0.1:46580'

        result = common.is_api_server_local()

        assert result is True

    @mock.patch('sky.server.common.get_server_url')
    def test_returns_false_for_remote(self, mock_get_url):
        """Test that remote URLs are not recognized as local."""
        mock_get_url.return_value = 'http://remote-server.example.com:46580'

        result = common.is_api_server_local()

        assert result is False

    def test_accepts_explicit_endpoint(self):
        """Test with explicit endpoint parameter."""
        # Local endpoint
        result = common.is_api_server_local('http://127.0.0.1:46580')
        assert result is True

        # Remote endpoint
        common.is_api_server_local.cache_clear()
        result = common.is_api_server_local('http://remote:46580')
        assert result is False


class TestApiServerInfo:
    """Tests for ApiServerInfo dataclass."""

    def test_creates_with_minimal_fields(self):
        """Test creation with only required status field."""
        info = ApiServerInfo(status=ApiServerStatus.HEALTHY)

        assert info.status == ApiServerStatus.HEALTHY
        assert info.api_version is None
        assert info.version is None
        assert info.error is None

    def test_creates_with_all_fields(self):
        """Test creation with all fields."""
        info = ApiServerInfo(status=ApiServerStatus.HEALTHY,
                             api_version='5',
                             version='1.0.0',
                             version_on_disk='1.0.0',
                             commit='abc123',
                             user={'id': 'test-user'},
                             basic_auth_enabled=True,
                             error=None)

        assert info.status == ApiServerStatus.HEALTHY
        assert info.api_version == '5'
        assert info.version == '1.0.0'
        assert info.version_on_disk == '1.0.0'
        assert info.commit == 'abc123'
        assert info.user == {'id': 'test-user'}
        assert info.basic_auth_enabled is True
        assert info.error is None


class TestApiServerStatus:
    """Tests for ApiServerStatus enum."""

    def test_enum_values(self):
        """Test that enum has expected values."""
        assert ApiServerStatus.HEALTHY.value == 'healthy'
        assert ApiServerStatus.UNHEALTHY.value == 'unhealthy'
        assert ApiServerStatus.VERSION_MISMATCH.value == 'version_mismatch'
        assert ApiServerStatus.NEEDS_AUTH.value == 'needs_auth'
