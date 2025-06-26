"""Unit tests for Bearer token middleware functionality."""

import os
from unittest import mock

import fastapi
import fastapi.responses
import pytest
import starlette.middleware.base

from sky import models
from sky.server.server import BearerTokenMiddleware
from sky.skylet import constants


@pytest.fixture(autouse=True)
def enable_service_accounts():
    """Enable service accounts for all tests in this module."""
    with mock.patch.dict(os.environ,
                         {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
        yield


class TestBearerTokenMiddleware:
    """Test cases for Bearer token middleware."""

    @pytest.fixture
    def middleware(self):
        """Create a Bearer token middleware instance."""
        return BearerTokenMiddleware(app=mock.Mock())

    @pytest.fixture
    def mock_request(self):
        """Create a mock request object."""
        request = mock.Mock(spec=fastapi.Request)
        request.url.path = '/sa/api/v1/status'
        request.headers = {}
        request.state = mock.Mock()
        request.scope = {'path': '/sa/api/v1/status'}
        return request

    @pytest.fixture
    def mock_call_next(self):
        """Create a mock call_next function."""

        async def call_next(request):
            return fastapi.responses.JSONResponse({"message": "success"})

        return call_next

    @pytest.mark.asyncio
    async def test_non_service_account_path_bypass(self, middleware,
                                                   mock_call_next):
        """Test that non-/sa/ paths bypass the middleware."""
        request = mock.Mock(spec=fastapi.Request)
        request.url.path = '/api/v1/status'  # No /sa/ prefix

        response = await middleware.dispatch(request, mock_call_next)

        # Should call next middleware without processing
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_service_accounts_disabled(self, middleware, mock_request,
                                             mock_call_next):
        """Test middleware when service accounts are disabled."""
        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'false'}):
            response = await middleware.dispatch(mock_request, mock_call_next)

            assert response.status_code == 401
            assert "Service account authentication disabled" in response.body.decode(
            )

    @pytest.mark.asyncio
    async def test_missing_authorization_header(self, middleware, mock_request,
                                                mock_call_next):
        """Test middleware with missing Authorization header."""
        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
            response = await middleware.dispatch(mock_request, mock_call_next)

            assert response.status_code == 401
            assert "Bearer token required" in response.body.decode()

    @pytest.mark.asyncio
    async def test_invalid_authorization_header_format(self, middleware,
                                                       mock_request,
                                                       mock_call_next):
        """Test middleware with invalid Authorization header format."""
        mock_request.headers = {'authorization': 'Basic username:password'}

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
            response = await middleware.dispatch(mock_request, mock_call_next)

            assert response.status_code == 401
            assert "Bearer token required" in response.body.decode()

    @pytest.mark.asyncio
    async def test_invalid_token_format(self, middleware, mock_request,
                                        mock_call_next):
        """Test middleware with invalid token format (not starting with sky_)."""
        mock_request.headers = {'authorization': 'Bearer invalid_token_format'}

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
            response = await middleware.dispatch(mock_request, mock_call_next)

            assert response.status_code == 401
            assert "Invalid service account token format" in response.body.decode(
            )

    @pytest.mark.asyncio
    async def test_token_verification_failure(self, middleware, mock_request,
                                              mock_call_next):
        """Test middleware when token verification fails."""
        mock_request.headers = {'authorization': 'Bearer sky_invalid_token'}

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
            with mock.patch('sky.users.token_service.token_service'
                           ) as mock_token_service:
                mock_token_service.verify_token.return_value = None

                response = await middleware.dispatch(mock_request,
                                                     mock_call_next)

                assert response.status_code == 401
                assert "Invalid or expired service account token" in response.body.decode(
                )

    @pytest.mark.asyncio
    async def test_missing_user_id_in_payload(self, middleware, mock_request,
                                              mock_call_next):
        """Test middleware when token payload is missing user_id."""
        mock_request.headers = {'authorization': 'Bearer sky_valid_token'}

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
            with mock.patch('sky.users.token_service.token_service'
                           ) as mock_token_service:
                mock_token_service.verify_token.return_value = {
                    'token_id': 'token123'
                    # Missing 'sub' (user_id)
                }

                response = await middleware.dispatch(mock_request,
                                                     mock_call_next)

                assert response.status_code == 401
                assert "Invalid token payload" in response.body.decode()

    @pytest.mark.asyncio
    async def test_user_not_exists(self, middleware, mock_request,
                                   mock_call_next):
        """Test middleware when service account user no longer exists."""
        mock_request.headers = {'authorization': 'Bearer sky_valid_token'}

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
            with mock.patch('sky.users.token_service.token_service'
                           ) as mock_token_service:
                with mock.patch(
                        'sky.global_user_state.get_user') as mock_get_user:
                    mock_token_service.verify_token.return_value = {
                        'sub': 'sa123',
                        'token_id': 'token123'
                    }
                    mock_get_user.return_value = None

                    response = await middleware.dispatch(
                        mock_request, mock_call_next)

                    assert response.status_code == 401
                    assert "Service account user no longer exists" in response.body.decode(
                    )

    @pytest.mark.asyncio
    async def test_successful_authentication(self, middleware, mock_request,
                                             mock_call_next):
        """Test successful service account authentication."""
        mock_request.headers = {'authorization': 'Bearer sky_valid_token'}

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
            with mock.patch('sky.users.token_service.token_service'
                           ) as mock_token_service:
                with mock.patch(
                        'sky.global_user_state.get_user') as mock_get_user:
                    with mock.patch(
                            'sky.global_user_state.update_service_account_token_last_used'
                    ) as mock_update:
                        with mock.patch(
                                'sky.server.server._override_user_info_in_request_body'
                        ) as mock_override:
                            mock_token_service.verify_token.return_value = {
                                'sub': 'sa123',
                                'token_id': 'token123',
                                'name': 'test-service-account'
                            }
                            mock_get_user.return_value = models.User(
                                id='sa123', name='test-service-account')
                            mock_override.return_value = None

                            response = await middleware.dispatch(
                                mock_request, mock_call_next)

                            assert response.status_code == 200
                            assert mock_request.state.auth_user.id == 'sa123'
                            assert mock_request.state.auth_user.name == 'test-service-account'
                            # Path should be stripped of /sa prefix
                            assert mock_request.scope[
                                'path'] == '/api/v1/status'
                            mock_update.assert_called_once_with('token123')

    @pytest.mark.asyncio
    async def test_last_used_update_failure_non_blocking(
            self, middleware, mock_request, mock_call_next):
        """Test that failure to update last_used timestamp doesn't block authentication."""
        mock_request.headers = {'authorization': 'Bearer sky_valid_token'}

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
            with mock.patch('sky.users.token_service.token_service'
                           ) as mock_token_service:
                with mock.patch(
                        'sky.global_user_state.get_user') as mock_get_user:
                    with mock.patch(
                            'sky.global_user_state.update_service_account_token_last_used'
                    ) as mock_update:
                        with mock.patch(
                                'sky.server.server._override_user_info_in_request_body'
                        ) as mock_override:
                            mock_token_service.verify_token.return_value = {
                                'sub': 'sa123',
                                'token_id': 'token123'
                            }
                            mock_get_user.return_value = models.User(
                                id='sa123', name='test-service-account')
                            mock_update.side_effect = Exception(
                                "Database error")
                            mock_override.return_value = None

                            response = await middleware.dispatch(
                                mock_request, mock_call_next)

                            # Should still succeed despite update failure
                            assert response.status_code == 200
                            assert mock_request.state.auth_user.id == 'sa123'

    @pytest.mark.asyncio
    async def test_token_service_exception(self, middleware, mock_request,
                                           mock_call_next):
        """Test middleware when token service raises an exception."""
        mock_request.headers = {'authorization': 'Bearer sky_valid_token'}

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
            with mock.patch('sky.users.token_service.token_service'
                           ) as mock_token_service:
                mock_token_service.verify_token.side_effect = Exception(
                    "Token service error")

                response = await middleware.dispatch(mock_request,
                                                     mock_call_next)

                assert response.status_code == 401
                assert "Service account authentication failed" in response.body.decode(
                )

    @pytest.mark.asyncio
    async def test_path_stripping_variations(self, middleware, mock_call_next):
        """Test path stripping for various URL patterns."""
        test_cases = [
            ('/sa/api/v1/status', '/api/v1/status'),
            ('/sa/api/v1/clusters', '/api/v1/clusters'),
            ('/sa/dashboard', '/dashboard'),
            ('/sa/', '/'),
        ]

        for original_path, expected_path in test_cases:
            request = mock.Mock(spec=fastapi.Request)
            request.url.path = original_path
            request.headers = {'authorization': 'Bearer sky_valid_token'}
            request.state = mock.Mock()
            request.scope = {'path': original_path}

            with mock.patch.dict(
                    os.environ,
                {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
                with mock.patch('sky.users.token_service.token_service'
                               ) as mock_token_service:
                    with mock.patch(
                            'sky.global_user_state.get_user') as mock_get_user:
                        with mock.patch(
                                'sky.global_user_state.update_service_account_token_last_used'
                        ):
                            with mock.patch(
                                    'sky.server.server._override_user_info_in_request_body'
                            ):
                                mock_token_service.verify_token.return_value = {
                                    'sub': 'sa123',
                                    'token_id': 'token123'
                                }
                                mock_get_user.return_value = models.User(
                                    id='sa123', name='test-service-account')

                                await middleware.dispatch(
                                    request, mock_call_next)

                                assert request.scope['path'] == expected_path

    @pytest.mark.asyncio
    async def test_case_insensitive_bearer_token(self, middleware, mock_request,
                                                 mock_call_next):
        """Test that Bearer token matching is case insensitive."""
        mock_request.headers = {
            'authorization': 'bearer sky_valid_token'
        }  # lowercase 'bearer'

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}):
            with mock.patch('sky.users.token_service.token_service'
                           ) as mock_token_service:
                with mock.patch(
                        'sky.global_user_state.get_user') as mock_get_user:
                    with mock.patch(
                            'sky.global_user_state.update_service_account_token_last_used'
                    ):
                        with mock.patch(
                                'sky.server.server._override_user_info_in_request_body'
                        ):
                            mock_token_service.verify_token.return_value = {
                                'sub': 'sa123',
                                'token_id': 'token123'
                            }
                            mock_get_user.return_value = models.User(
                                id='sa123', name='test-service-account')

                            response = await middleware.dispatch(
                                mock_request, mock_call_next)

                            assert response.status_code == 200
