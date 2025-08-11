"""Tests for AnonymousAccessMiddleware."""

import os
import unittest.mock as mock

import fastapi
import pytest

from sky.server.server import AnonymousAccessMiddleware
from sky.skylet import constants


class TestAnonymousAccessMiddleware:

    @pytest.fixture
    def middleware(self):
        return AnonymousAccessMiddleware(app=mock.Mock())

    @pytest.fixture
    def mock_call_next(self):

        async def call_next(
                request):  # noqa: ARG001 - part of middleware signature
            return fastapi.responses.JSONResponse({"message": "success"})

        return call_next

    def _make_request(self, path: str, authenticated: bool = False):
        request = mock.Mock(spec=fastapi.Request)
        request.url.path = path
        request.headers = {}
        request.state = mock.Mock()
        request.scope = {"path": path}
        request.state.auth_user = mock.Mock() if authenticated else None
        return request

    @pytest.mark.asyncio
    async def test_authenticated_user_bypasses_checks(self, middleware,
                                                      mock_call_next):
        request = self._make_request("/api/v1/status", authenticated=True)

        response = await middleware.dispatch(request, mock_call_next)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_anonymous_access_allowed_by_default(self, middleware,
                                                       mock_call_next):
        # Default is allowed if the env var is unset or set to 'false'
        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_DISABLE_ANONYMOUS_ACCESS: 'false'},
                clear=False):
            request = self._make_request("/api/v1/status", authenticated=False)
            response = await middleware.dispatch(request, mock_call_next)

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_anonymous_access_disabled_blocks_protected_paths(
            self, middleware, mock_call_next):
        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_DISABLE_ANONYMOUS_ACCESS: 'true'},
                clear=False):
            # Protected path should be blocked when anonymous access is disabled
            request = self._make_request("/api/v1/status", authenticated=False)
            response = await middleware.dispatch(request, mock_call_next)

            assert response.status_code == 401
            assert "Authentication failed" in response.body.decode()

    @pytest.mark.asyncio
    async def test_anonymous_access_disabled_allows_health(
            self, middleware, mock_call_next):
        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_DISABLE_ANONYMOUS_ACCESS: 'true'},
                clear=False):
            request = self._make_request("/api/health", authenticated=False)
            response = await middleware.dispatch(request, mock_call_next)

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_anonymous_access_disabled_allows_dashboard(
            self, middleware, mock_call_next):
        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_DISABLE_ANONYMOUS_ACCESS: 'true'},
                clear=False):
            request = self._make_request("/dashboard/index.html",
                                         authenticated=False)
            response = await middleware.dispatch(request, mock_call_next)

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_anonymous_access_disabled_allows_root(
            self, middleware, mock_call_next):
        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_DISABLE_ANONYMOUS_ACCESS: 'true'},
                clear=False):
            request = self._make_request("/", authenticated=False)
            response = await middleware.dispatch(request, mock_call_next)

            assert response.status_code == 200
