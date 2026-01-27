"""Tests for Bearer token middleware."""

import os
import unittest.mock as mock

import fastapi
import pytest

from sky.server.server import BearerTokenMiddleware
from sky.skylet import constants


class TestBearerTokenMiddleware:
    """Test cases for Bearer token middleware."""

    @pytest.fixture
    def middleware(self):
        """Create a Bearer token middleware instance."""
        return BearerTokenMiddleware(app=mock.Mock())

    @pytest.fixture
    def base_mock_request(self):
        """Create a basic mock request with auth_user initialized to None."""
        request = mock.Mock(spec=fastapi.Request)
        request.headers = {}
        request.state = mock.Mock()
        request.state.auth_user = None
        return request

    @pytest.fixture
    def mock_call_next(self):
        """Create a mock call_next function."""

        async def call_next(request):
            return fastapi.responses.JSONResponse({"message": "success"})

        return call_next

    @pytest.mark.asyncio
    async def test_no_authorization_header_bypass(self, middleware,
                                                  base_mock_request,
                                                  mock_call_next):
        """Test that requests without Authorization header bypass the middleware."""
        # No Authorization header (default from fixture)
        response = await middleware.dispatch(base_mock_request, mock_call_next)

        # Should call next middleware without processing
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_non_bearer_authorization_bypass(self, middleware,
                                                   base_mock_request,
                                                   mock_call_next):
        """Test that non-Bearer authorization headers bypass the middleware."""
        base_mock_request.headers = {
            'authorization': 'Basic dXNlcjpwYXNz'
        }  # Basic auth

        response = await middleware.dispatch(base_mock_request, mock_call_next)

        # Should call next middleware without processing
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_service_accounts_disabled(self, middleware,
                                             base_mock_request, mock_call_next):
        """Test middleware when service accounts are disabled."""
        base_mock_request.headers = {'authorization': 'Bearer sky_test_token'}

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'false'}):
            response = await middleware.dispatch(base_mock_request,
                                                 mock_call_next)

            # Should return 401 when service accounts are disabled and
            # a SkyPilot token is provided
            assert response.status_code == 401
            assert "Service account authentication disabled" in response.body.decode(
            )

    @pytest.mark.asyncio
    async def test_non_skypilot_bearer_token_bypass(self, middleware,
                                                    base_mock_request,
                                                    mock_call_next):
        """Test that non-SkyPilot Bearer tokens bypass the middleware."""
        base_mock_request.headers = {
            'authorization': 'Bearer oauth_token_123'
        }  # Not sky_ prefix

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
            response = await middleware.dispatch(base_mock_request,
                                                 mock_call_next)

            # Should call next middleware without processing
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_invalid_service_account_token(self, middleware,
                                                 base_mock_request,
                                                 mock_call_next):
        """Test middleware with invalid service account token."""
        base_mock_request.headers = {
            'authorization': 'Bearer sky_invalid_token'
        }

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as mock_token_service:

            mock_token_service.verify_token.return_value = None

            response = await middleware.dispatch(base_mock_request,
                                                 mock_call_next)

            assert response.status_code == 401
            assert "Invalid or expired service account token" in response.body.decode(
            )

    @pytest.mark.asyncio
    async def test_valid_service_account_token_success(self, middleware,
                                                       base_mock_request,
                                                       mock_call_next):
        """Test middleware with valid service account token."""
        base_mock_request.headers = {'authorization': 'Bearer sky_valid_token'}

        mock_payload = {
            'sub': 'sa-123456',  # service account user ID
            'name': 'test-service-account',
            'token_id': 'token_123'
        }

        mock_user_info = mock.Mock()
        mock_user_info.name = 'test-service-account'

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as mock_token_service, \
                mock.patch('sky.global_user_state.get_user') as mock_get_user, \
                mock.patch('sky.global_user_state.update_service_account_token_last_used') as mock_update_last_used:

            mock_token_service.verify_token.return_value = mock_payload
            mock_get_user.return_value = mock_user_info

            response = await middleware.dispatch(base_mock_request,
                                                 mock_call_next)

            assert response.status_code == 200
            # Verify user was set in request state
            assert base_mock_request.state.auth_user.id == 'sa-123456'
            assert base_mock_request.state.auth_user.name == 'test-service-account'
            # Verify token last used was updated
            mock_update_last_used.assert_called_once_with('token_123')

    @pytest.mark.asyncio
    async def test_missing_user_id_in_token(self, middleware, base_mock_request,
                                            mock_call_next):
        """Test middleware when token payload is missing user_id."""
        base_mock_request.headers = {
            'authorization': 'Bearer sky_invalid_payload_token'
        }

        mock_payload = {
            # Missing 'sub' (user_id)
            'name': 'test-service-account',
            'token_id': 'token_123'
        }

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as mock_token_service:

            mock_token_service.verify_token.return_value = mock_payload

            response = await middleware.dispatch(base_mock_request,
                                                 mock_call_next)

            assert response.status_code == 401
            assert "Invalid token payload" in response.body.decode()

    @pytest.mark.asyncio
    async def test_missing_token_id_in_token(self, middleware,
                                             base_mock_request, mock_call_next):
        """Test middleware when token payload is missing token_id."""
        base_mock_request.headers = {
            'authorization': 'Bearer sky_invalid_payload_token'
        }

        mock_payload = {
            'sub': 'sa-123456',
            'name': 'test-service-account',
            # Missing 'token_id'
        }

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as mock_token_service:

            mock_token_service.verify_token.return_value = mock_payload

            response = await middleware.dispatch(base_mock_request,
                                                 mock_call_next)

            assert response.status_code == 401
            assert "Invalid token payload" in response.body.decode()

    @pytest.mark.asyncio
    async def test_user_no_longer_exists(self, middleware, base_mock_request,
                                         mock_call_next):
        """Test middleware when service account user no longer exists."""
        base_mock_request.headers = {'authorization': 'Bearer sky_valid_token'}

        mock_payload = {
            'sub': 'sa-deleted-user',
            'name': 'deleted-service-account',
            'token_id': 'token_123'
        }

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as mock_token_service, \
                mock.patch('sky.global_user_state.get_user') as mock_get_user:

            mock_token_service.verify_token.return_value = mock_payload
            mock_get_user.return_value = None  # User no longer exists

            response = await middleware.dispatch(base_mock_request,
                                                 mock_call_next)

            assert response.status_code == 401
            assert "Service account user no longer exists" in response.body.decode(
            )

    @pytest.mark.asyncio
    async def test_update_last_used_failure_not_fatal(self, middleware,
                                                      base_mock_request,
                                                      mock_call_next):
        """Test that failure to update last used timestamp doesn't fail authentication."""
        base_mock_request.headers = {'authorization': 'Bearer sky_valid_token'}

        mock_payload = {
            'sub': 'sa-123456',
            'name': 'test-service-account',
            'token_id': 'token_123'
        }

        mock_user_info = mock.Mock()
        mock_user_info.name = 'test-service-account'

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as mock_token_service, \
                mock.patch('sky.global_user_state.get_user') as mock_get_user, \
                mock.patch('sky.global_user_state.update_service_account_token_last_used') as mock_update_last_used:

            mock_token_service.verify_token.return_value = mock_payload
            mock_get_user.return_value = mock_user_info
            mock_update_last_used.side_effect = Exception("Database error")

            response = await middleware.dispatch(base_mock_request,
                                                 mock_call_next)

            # Should still succeed despite update failure
            assert response.status_code == 200
            assert base_mock_request.state.auth_user.id == 'sa-123456'

    @pytest.mark.asyncio
    async def test_token_verification_exception(self, middleware,
                                                base_mock_request,
                                                mock_call_next):
        """Test middleware when token verification raises an exception."""
        base_mock_request.headers = {
            'authorization': 'Bearer sky_problematic_token'
        }

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as mock_token_service:

            mock_token_service.verify_token.side_effect = Exception(
                "Token verification failed")

            response = await middleware.dispatch(base_mock_request,
                                                 mock_call_next)

            assert response.status_code == 401
            assert "Service account authentication failed" in response.body.decode(
            )

    @pytest.mark.asyncio
    async def test_case_insensitive_bearer_check(self, middleware,
                                                 base_mock_request,
                                                 mock_call_next):
        """Test that Bearer token check is case insensitive."""
        base_mock_request.headers = {
            'authorization': 'bearer sky_test_token'
        }  # lowercase

        mock_payload = {
            'sub': 'sa-123456',
            'name': 'test-service-account',
            'token_id': 'token_123'
        }

        mock_user_info = mock.Mock()
        mock_user_info.name = 'test-service-account'

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as mock_token_service, \
                mock.patch('sky.global_user_state.get_user') as mock_get_user, \
                mock.patch('sky.global_user_state.update_service_account_token_last_used'):

            mock_token_service.verify_token.return_value = mock_payload
            mock_get_user.return_value = mock_user_info

            response = await middleware.dispatch(base_mock_request,
                                                 mock_call_next)

            assert response.status_code == 200
            assert base_mock_request.state.auth_user.id == 'sa-123456'

    @pytest.mark.asyncio
    async def test_already_authenticated_user_bypass(self, middleware,
                                                     base_mock_request,
                                                     mock_call_next):
        """Test that middleware bypasses when user is already authenticated.

        This ensures consistency with other auth middlewares (OAuth2Proxy,
        AuthProxy, BasicAuth) - when a previous middleware has authenticated
        the user, subsequent middlewares should pass through.
        """
        # Request has a Bearer token header
        base_mock_request.headers = {'authorization': 'Bearer sky_some_token'}
        # But user is already authenticated by a previous middleware
        mock_user = mock.Mock()
        mock_user.id = 'user-123'
        mock_user.name = 'basic-auth-user'
        base_mock_request.state.auth_user = mock_user

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as mock_token_service:

            # Middleware should bypass without calling token_service
            response = await middleware.dispatch(base_mock_request,
                                                 mock_call_next)

            # Should pass through successfully
            assert response.status_code == 200
            # User should remain the same (not overwritten)
            assert base_mock_request.state.auth_user.id == 'user-123'
            assert base_mock_request.state.auth_user.name == 'basic-auth-user'
            # Token service should NOT be called
            mock_token_service.verify_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_bearer_auth_then_basic_auth_middleware(
            self, middleware, base_mock_request, mock_call_next):
        """Test that BasicAuthMiddleware respects user authenticated by BearerTokenMiddleware.

        This test simulates the middleware chain: BearerTokenMiddleware -> BasicAuthMiddleware.
        When BearerTokenMiddleware successfully authenticates a service account,
        BasicAuthMiddleware should pass through without attempting to re-authenticate.
        """
        base_mock_request.headers = {'authorization': 'Bearer sky_valid_token'}
        # Mock request.url.path for BasicAuthMiddleware
        base_mock_request.url = mock.Mock()
        base_mock_request.url.path = '/api/some_endpoint'

        mock_payload = {
            'sub': 'sa-123456',
            'name': 'test-service-account',
            'token_id': 'token_123'
        }

        mock_user_info = mock.Mock()
        mock_user_info.id = 'sa-123456'
        mock_user_info.name = 'test-service-account'

        # Mock BasicAuthMiddleware
        from sky.server.server import BasicAuthMiddleware
        basic_auth_middleware = BasicAuthMiddleware(app=mock.Mock())

        # Create a call_next that simulates BasicAuthMiddleware being next in chain
        async def bearer_then_basic_call_next(request):
            # This simulates BasicAuthMiddleware being called after BearerTokenMiddleware
            return await basic_auth_middleware.dispatch(request, mock_call_next)

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as mock_token_service, \
                mock.patch('sky.global_user_state.get_user') as mock_get_user, \
                mock.patch('sky.global_user_state.update_service_account_token_last_used'), \
                mock.patch('sky.jobs.utils.is_consolidation_mode', return_value=False):

            mock_token_service.verify_token.return_value = mock_payload
            mock_get_user.return_value = mock_user_info

            # First BearerTokenMiddleware authenticates
            response = await middleware.dispatch(base_mock_request,
                                                 bearer_then_basic_call_next)

            # Should succeed through both middlewares
            assert response.status_code == 200
            # User should be the service account user from Bearer auth
            assert base_mock_request.state.auth_user.id == 'sa-123456'
            assert base_mock_request.state.auth_user.name == 'test-service-account'
