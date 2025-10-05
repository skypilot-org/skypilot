"""Tests for OAuth2 proxy middleware."""

import asyncio
import http
import os
import unittest.mock as mock

import aiohttp
import fastapi
import pytest
from starlette.datastructures import Headers

from sky.server.auth.oauth2_proxy import OAuth2ProxyMiddleware


class TestOAuth2ProxyMiddleware:
    """Test cases for OAuth2 proxy middleware."""

    @pytest.fixture
    def middleware_enabled(self):
        """Create an enabled OAuth2 proxy middleware instance."""
        with mock.patch.dict(
                os.environ,
            {
                'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': 'true',
                'SKYPILOT_AUTH_OAUTH2_PROXY_BASE_URL': 'http://oauth2-proxy:4180'
            }):
            return OAuth2ProxyMiddleware(app=mock.Mock())

    @pytest.fixture
    def middleware_disabled(self):
        """Create a disabled OAuth2 proxy middleware instance."""
        with mock.patch.dict(os.environ,
                             {'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': 'false'},
                             clear=True):
            return OAuth2ProxyMiddleware(app=mock.Mock())

    @pytest.fixture
    def mock_request(self):
        """Create a mock request object."""
        request = mock.Mock(spec=fastapi.Request)
        request.url = mock.Mock()
        request.url.path = '/api/v1/status'
        request.url.query = 'param=value'
        request.url.__str__ = mock.Mock(
            return_value='http://localhost:8080/api/v1/status?param=value')
        request.base_url = 'http://localhost:8080/'
        request.method = 'POST'
        request.headers = {
            'content-length': '501',
            'content-type': 'application/json',
            'host': 'localhost:8080',
            'accept-encoding': 'gzip, deflate',
            'user-agent': 'python-requests/2.32.3',
        }
        request.cookies = {'session': 'test_session'}
        request.state = mock.Mock()
        request.state.auth_user = None
        request.state.request_id = 'test-request-123'
        # Mock body and json methods for authn.override_user_info_in_request_body
        request.body = mock.AsyncMock(return_value=b'{"task": "test"}')
        request.json = mock.AsyncMock(return_value={'task': 'test'})
        return request

    @pytest.fixture
    def mock_call_next(self):
        """Create a mock call_next function."""

        async def call_next(request):
            return fastapi.responses.JSONResponse({'message': 'success'})

        return call_next

    @pytest.fixture
    def mock_auth_response_accepted(self):
        """Create a mock successful auth response."""
        response = mock.Mock(spec=aiohttp.ClientResponse)
        response.status = http.HTTPStatus.ACCEPTED
        response.headers = {'X-Auth-Request-Email': 'test@example.com'}
        return response

    @pytest.fixture
    def mock_auth_response_unauthorized(self):
        """Create a mock unauthorized auth response."""
        response = mock.Mock(spec=aiohttp.ClientResponse)
        response.status = http.HTTPStatus.UNAUTHORIZED
        return response

    @pytest.mark.asyncio
    async def test_disabled_middleware_bypasses(self, middleware_disabled,
                                                mock_request, mock_call_next):
        """Test that disabled middleware bypasses authentication."""
        response = await middleware_disabled.dispatch(mock_request,
                                                      mock_call_next)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_oauth2_path_forwarding(self, middleware_enabled):
        """Test that /oauth2/* paths are forwarded to proxy."""
        request = mock.Mock(spec=fastapi.Request)
        request.url.path = '/oauth2/start'
        request.method = 'GET'
        request.headers = {}
        request.cookies = {}
        request.query_params = {}

        mock_body = b'test body'
        request.body = mock.AsyncMock(return_value=mock_body)

        # Mock aiohttp session and response
        mock_response = mock.Mock()
        mock_response.status = 302
        mock_response.headers = {'Location': '/oauth2/callback'}
        mock_response.cookies = {}
        mock_response.read = mock.AsyncMock(return_value=b'redirect')

        # Create proper context manager mock
        mock_response_ctx = mock.AsyncMock()
        mock_response_ctx.__aenter__.return_value = mock_response
        mock_response_ctx.__aexit__.return_value = None

        with mock.patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = mock.Mock()
            mock_session.request.return_value = mock_response_ctx
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            response = await middleware_enabled.forward_to_oauth2_proxy(request)

            # Verify request was made to proxy
            mock_session.request.assert_called_once_with(
                method='GET',
                url='http://oauth2-proxy:4180/oauth2/start',
                headers={},
                data=mock_body,
                cookies={},
                params={},
                allow_redirects=False,
            )
            assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_authenticate_strips_content_headers(self, middleware_enabled,
                                                       mock_request,
                                                       mock_call_next):
        """Test that content-length and content-type headers are stripped
        during auth check."""
        mock_request.state.auth_user = None

        # Mock aiohttp session and response
        mock_response = mock.Mock()
        mock_response.status = http.HTTPStatus.ACCEPTED
        mock_response.headers = {'X-Auth-Request-Email': 'test@example.com'}

        # Create proper context manager mocks
        mock_response_ctx = mock.AsyncMock()
        mock_response_ctx.__aenter__.return_value = mock_response
        mock_response_ctx.__aexit__.return_value = None

        with mock.patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = mock.Mock()
            mock_session.request.return_value = mock_response_ctx
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            response = await middleware_enabled.authenticate(
                mock_request, mock_call_next)

            # Get the actual call arguments
            call_args = mock_session.request.call_args
            headers = call_args.kwargs['headers']

            # Verify problematic headers were stripped
            assert 'content-length' not in headers
            assert 'content-type' not in headers

            # Verify other headers are preserved
            assert headers['user-agent'] == 'python-requests/2.32.3'
            assert headers['host'] == 'localhost:8080'

            # Verify X-Forwarded-Uri was added
            assert 'X-Forwarded-Uri' in headers

            # Verify method is GET for auth check
            assert call_args.kwargs['method'] == 'GET'

            # Verify no body is sent for auth check
            assert 'data' not in call_args.kwargs

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_authenticate_timeout_handling(self, middleware_enabled,
                                                 mock_request, mock_call_next):
        """Test that timeouts are properly handled."""
        mock_request.state.auth_user = None

        with mock.patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = mock.Mock()
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None
            # Simulate timeout
            mock_session.request.side_effect = asyncio.TimeoutError()

            response = await middleware_enabled.authenticate(
                mock_request, mock_call_next)

            assert response.status_code == http.HTTPStatus.BAD_GATEWAY
            assert ('oauth2-proxy service unavailable'
                    in response.body.decode())

    @pytest.mark.asyncio
    async def test_authenticate_unauthorized_redirect(self, middleware_enabled,
                                                      mock_request,
                                                      mock_call_next):
        """Test redirect behavior for unauthorized requests."""
        mock_request.state.auth_user = None
        mock_request.url.path = '/api/v1/jobs'  # Not health endpoint

        mock_response = mock.Mock()
        mock_response.status = http.HTTPStatus.UNAUTHORIZED

        # Create proper context manager mocks
        mock_response_ctx = mock.AsyncMock()
        mock_response_ctx.__aenter__.return_value = mock_response
        mock_response_ctx.__aexit__.return_value = None

        with mock.patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = mock.Mock()
            mock_session.request.return_value = mock_response_ctx
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            response = await middleware_enabled.authenticate(
                mock_request, mock_call_next)

            assert response.status_code == 307  # RedirectResponse
            assert 'oauth2/start' in response.headers['location']

    @pytest.mark.asyncio
    async def test_authenticate_health_endpoint_bypass(self, middleware_enabled,
                                                       mock_call_next):
        """Test that health endpoints bypass auth when unauthorized."""
        request = mock.Mock(spec=fastapi.Request)
        request.url = mock.Mock()
        request.url.path = '/api/health'
        request.url.__str__ = mock.Mock(
            return_value='http://localhost:8080/api/health')
        request.state = mock.Mock()
        request.state.auth_user = None
        request.headers = {}
        request.cookies = {}

        mock_response = mock.Mock()
        mock_response.status = http.HTTPStatus.UNAUTHORIZED

        # Create proper context manager mocks
        mock_response_ctx = mock.AsyncMock()
        mock_response_ctx.__aenter__.return_value = mock_response
        mock_response_ctx.__aexit__.return_value = None

        with mock.patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = mock.Mock()
            mock_session.request.return_value = mock_response_ctx
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            response = await middleware_enabled.authenticate(
                request, mock_call_next)

            # Should allow through with anonymous_user flag
            assert hasattr(request.state, 'anonymous_user')
            assert request.state.anonymous_user is True
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_authenticate_already_authenticated_skip(
            self, middleware_enabled, mock_request, mock_call_next):
        """Test that already authenticated requests skip auth check."""
        # Set user as already authenticated
        mock_request.state.auth_user = mock.Mock()

        response = await middleware_enabled.authenticate(
            mock_request, mock_call_next)

        # Should call next directly without making auth request
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_auth_user_extraction(self, middleware_enabled):
        """Test user info extraction from auth response headers."""
        response = mock.Mock()
        response.headers = {'X-Auth-Request-Email': 'user@example.com'}

        user = middleware_enabled.get_auth_user(response)

        assert user is not None
        assert user.name == 'user@example.com'
        assert len(user.id) == 8  # USER_HASH_LENGTH

    def test_get_auth_user_no_email_header(self, middleware_enabled):
        """Test user extraction when no email header is present."""
        response = mock.Mock()
        response.headers = {}

        user = middleware_enabled.get_auth_user(response)

        assert user is None

    @pytest.mark.asyncio
    async def test_authenticate_missing_user_info_error(self,
                                                        middleware_enabled,
                                                        mock_request,
                                                        mock_call_next):
        """Test error when proxy returns 202 but no user info."""
        mock_request.state.auth_user = None

        mock_response = mock.Mock()
        mock_response.status = http.HTTPStatus.ACCEPTED
        mock_response.headers = {}  # No X-Auth-Request-Email

        # Create proper context manager mocks
        mock_response_ctx = mock.AsyncMock()
        mock_response_ctx.__aenter__.return_value = mock_response
        mock_response_ctx.__aexit__.return_value = None

        with mock.patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = mock.Mock()
            mock_session.request.return_value = mock_response_ctx
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            response = await middleware_enabled.authenticate(
                mock_request, mock_call_next)

            assert (
                response.status_code == http.HTTPStatus.INTERNAL_SERVER_ERROR)
            response_data = response.body.decode()
            assert 'oauth2-proxy is enabled but did not' in response_data
            assert 'return user info' in response_data

    def test_middleware_initialization_missing_base_url(self):
        """Test that middleware raises error when enabled but no base URL."""
        with mock.patch.dict(os.environ,
                             {'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': 'true'},
                             clear=True):
            with pytest.raises(ValueError,
                               match='OAuth2 Proxy is enabled but base_url is '
                               'not set'):
                OAuth2ProxyMiddleware(app=mock.Mock())

    @pytest.mark.asyncio
    async def test_forward_to_oauth2_proxy_connection_error(
            self, middleware_enabled):
        """Test error handling in forward_to_oauth2_proxy."""
        request = mock.Mock(spec=fastapi.Request)
        request.url.path = '/oauth2/start'
        request.method = 'GET'
        request.headers = {}
        request.cookies = {}
        request.query_params = {}
        request.body = mock.AsyncMock(return_value=b'')

        with mock.patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = mock.Mock()
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None
            mock_session.request.side_effect = aiohttp.ClientError(
                'Connection failed')

            response = await middleware_enabled.forward_to_oauth2_proxy(request)

            assert response.status_code == http.HTTPStatus.BAD_GATEWAY
            assert ('oauth2-proxy service unavailable'
                    in response.body.decode())


class TestOAuth2ProxyMiddlewareLoopback:
    """Test cases for OAuth2 proxy middleware with loopback detection."""

    @pytest.fixture
    def middleware_enabled(self):
        """Create an enabled OAuth2 proxy middleware instance."""
        with mock.patch.dict(
                os.environ,
            {
                'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED': 'true',
                'SKYPILOT_AUTH_OAUTH2_PROXY_BASE_URL': 'http://oauth2-proxy:4180'
            }):
            return OAuth2ProxyMiddleware(app=mock.Mock())

    @pytest.fixture
    def mock_call_next(self):
        """Create a mock call_next function."""

        async def call_next(request):
            return fastapi.responses.JSONResponse({'message': 'success'})

        return call_next

    @pytest.fixture
    def loopback_request(self):
        """Create a mock loopback request."""
        request = mock.Mock(spec=fastapi.Request)
        request.url = mock.Mock()
        request.url.path = '/api/health'
        request.client = mock.Mock()
        request.client.host = '127.0.0.1'
        request.headers = Headers({})
        request.cookies = {}
        request.query_params = {}
        request.state = mock.Mock()
        request.state.auth_user = None
        request.body = mock.AsyncMock(return_value=b'{}')
        request.json = mock.AsyncMock(return_value={})
        return request

    @pytest.mark.asyncio
    async def test_loopback_bypass_enabled(self, middleware_enabled,
                                           loopback_request, mock_call_next):
        """Test that loopback requests bypass OAuth2 when consolidation mode is enabled."""
        with mock.patch('sky.jobs.utils.is_consolidation_mode',
                        return_value=True):
            response = await middleware_enabled.authenticate(
                loopback_request, mock_call_next)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_loopback_bypass_with_proxy_headers(self, middleware_enabled,
                                                      loopback_request,
                                                      mock_call_next):
        """Test that loopback requests with proxy headers do NOT bypass (security)."""
        request = loopback_request
        request.url.path = '/api/status'
        request.headers = Headers({'X-Forwarded-For': '203.0.113.1'})

        # Mock the oauth2 proxy call to return unauthorized
        mock_response = mock.Mock()
        mock_response.status = http.HTTPStatus.UNAUTHORIZED

        mock_response_ctx = mock.AsyncMock()
        mock_response_ctx.__aenter__.return_value = mock_response
        mock_response_ctx.__aexit__.return_value = None

        with mock.patch('sky.jobs.utils.is_consolidation_mode', return_value=True), \
             mock.patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = mock.Mock()
            mock_session.request.return_value = mock_response_ctx
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            response = await middleware_enabled.authenticate(
                request, mock_call_next)

            # Should NOT bypass due to proxy headers - proceed to normal OAuth2 flow
            assert response.status_code == http.HTTPStatus.TEMPORARY_REDIRECT.value

    @pytest.mark.asyncio
    async def test_loopback_disabled_in_non_consolidation_mode(
            self, middleware_enabled, loopback_request, mock_call_next):
        """Test that loopback bypass is disabled when not in consolidation mode."""
        # Create loopback request
        request = loopback_request
        request.url.path = '/api/status'

        # Mock OAuth2 to return unauthorized
        mock_response = mock.Mock()
        mock_response.status = http.HTTPStatus.UNAUTHORIZED

        mock_response_ctx = mock.AsyncMock()
        mock_response_ctx.__aenter__.return_value = mock_response
        mock_response_ctx.__aexit__.return_value = None

        with mock.patch('sky.jobs.utils.is_consolidation_mode', return_value=False), \
             mock.patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = mock.Mock()
            mock_session.request.return_value = mock_response_ctx
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            response = await middleware_enabled.authenticate(
                request, mock_call_next)

            # Should NOT bypass when consolidation mode is disabled - uses normal OAuth2 flow
            assert response.status_code == http.HTTPStatus.TEMPORARY_REDIRECT.value
