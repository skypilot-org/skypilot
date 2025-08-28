"""Authentication based on oauth2-proxy."""

import asyncio
import hashlib
import http
import os
import traceback
from typing import Optional
import urllib

import aiohttp
import fastapi
import starlette.middleware.base

from sky import models
from sky import sky_logging
from sky.server.auth import authn
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

# We do not support setting these in config.yaml because:
# 1. config.yaml can be updated dynamically, but auth middleware does not
#    support hot reload yet.
# 2. If we introduce hot reload for auth middleware, bad config might
#    invalidate all authenticated sessions and thus cannot be rolled back
#    by API users.
# TODO(aylei): we should introduce server.yaml for static server admin config,
# which is more structured than multiple environment variables and can be less
# confusing to users.
OAUTH2_PROXY_BASE_URL_ENV_VAR = 'SKYPILOT_AUTH_OAUTH2_PROXY_BASE_URL'
OAUTH2_PROXY_ENABLED_ENV_VAR = 'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED'


class OAuth2ProxyMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to handle authentication by delegating to OAuth2 Proxy."""

    def __init__(self, application: fastapi.FastAPI):
        super().__init__(application)
        self.enabled: bool = (os.getenv(OAUTH2_PROXY_ENABLED_ENV_VAR,
                                        'false') == 'true')
        self.proxy_base: str = ''
        if self.enabled:
            proxy_base = os.getenv(OAUTH2_PROXY_BASE_URL_ENV_VAR)
            if not proxy_base:
                raise ValueError('OAuth2 Proxy is enabled but base_url is not '
                                 'set')
            self.proxy_base = proxy_base.rstrip('/')

    async def dispatch(self, request: fastapi.Request, call_next):
        if not self.enabled:
            return await call_next(request)

        # Forward /oauth2/* to oauth2-proxy, including /oauth2/start and
        # /oauth2/callback.
        if request.url.path.startswith('/oauth2'):
            return await self.forward_to_oauth2_proxy(request)

        return await self.authenticate(request, call_next)

    async def forward_to_oauth2_proxy(self, request: fastapi.Request):
        """Forward requests to oauth2-proxy service."""
        logger.debug(f'forwarding to oauth2-proxy: {request.url.path}')
        path = request.url.path.lstrip('/')
        target_url = f'{self.proxy_base}/{path}'
        body = await request.body()
        async with aiohttp.ClientSession() as session:
            try:
                forwarded_headers = dict(request.headers)
                async with session.request(
                        method=request.method,
                        url=target_url,
                        headers=forwarded_headers,
                        data=body,
                        cookies=request.cookies,
                        params=request.query_params,
                        allow_redirects=False,
                ) as response:
                    response_body = await response.read()
                    fastapi_response = fastapi.responses.Response(
                        content=response_body,
                        status_code=response.status,
                        headers=dict(response.headers),
                    )
                    # Forward cookies from OAuth2 proxy response to client
                    for cookie_name, cookie in response.cookies.items():
                        fastapi_response.set_cookie(
                            key=cookie_name,
                            value=cookie.value,
                            max_age=cookie.get('max-age'),
                            expires=cookie.get('expires'),
                            path=cookie.get('path', '/'),
                            domain=cookie.get('domain'),
                            secure=cookie.get('secure', False),
                            httponly=cookie.get('httponly', False),
                        )
                    return fastapi_response
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f'Error forwarding to OAuth2 proxy: {e}')
                return fastapi.responses.JSONResponse(
                    status_code=http.HTTPStatus.BAD_GATEWAY,
                    content={'detail': 'oauth2-proxy service unavailable'})

    async def authenticate(self, request: fastapi.Request, call_next):
        if request.state.auth_user is not None:
            # Already authenticated
            return await call_next(request)

        async with aiohttp.ClientSession() as session:
            try:
                return await self._authenticate(request, call_next, session)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f'Error communicating with OAuth2 proxy: {e}'
                             f'{traceback.format_exc()}')
                return fastapi.responses.JSONResponse(
                    status_code=http.HTTPStatus.BAD_GATEWAY,
                    content={'detail': 'oauth2-proxy service unavailable'})

    async def _authenticate(self, request: fastapi.Request, call_next,
                            session: aiohttp.ClientSession):
        forwarded_headers = dict(request.headers)
        auth_url = f'{self.proxy_base}/oauth2/auth'
        forwarded_headers['X-Forwarded-Uri'] = str(request.url).rstrip('/')
        # Remove content-length and content-type headers and drop request body
        # to reduce the auth overhead.
        forwarded_headers.pop('content-length', None)
        forwarded_headers.pop('content-type', None)
        logger.debug(f'authenticate request: {auth_url}, '
                     f'headers: {forwarded_headers}')

        async with session.request(
                method='GET',
                url=auth_url,
                headers=forwarded_headers,
                cookies=request.cookies,
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
        ) as auth_response:

            if auth_response.status == http.HTTPStatus.ACCEPTED:
                # User is authenticated, extract user info from headers
                auth_user = self.get_auth_user(auth_response)
                if not auth_user:
                    return fastapi.responses.JSONResponse(
                        status_code=http.HTTPStatus.INTERNAL_SERVER_ERROR,
                        content={
                            'detail':
                                'oauth2-proxy is enabled but did not'
                                'return user info, check your oauth2-proxy'
                                'setup.'
                        })
                request.state.auth_user = auth_user
                await authn.override_user_info_in_request_body(
                    request, auth_user)
                return await call_next(request)
            elif auth_response.status == http.HTTPStatus.UNAUTHORIZED:
                # For /api/health, we should allow unauthenticated requests to
                # not break healthz check.
                # TODO(aylei): remove this to an aggregated login middleware
                # in favor of the unified authentication.
                if request.url.path.startswith('/api/health'):
                    request.state.anonymous_user = True
                    return await call_next(request)

                # TODO(aylei): in unified authentication, the redirection
                # or rejection should be done after all the authentication
                # methods are performed.
                # Not authenticated, redirect to sign-in
                redirect_path = request.url.path
                if request.url.query:
                    redirect_path += f'?{request.url.query}'
                rd = urllib.parse.quote(redirect_path)
                signin_url = (f'{request.base_url}oauth2/start?'
                              f'rd={rd}')
                return fastapi.responses.RedirectResponse(url=signin_url)
            else:
                logger.error('oauth2-proxy returned unexpected status '
                             f'{auth_response.status}: {auth_response.text}')
                return fastapi.responses.JSONResponse(
                    status_code=auth_response.status,
                    content={'detail': 'oauth2-proxy error'})

    def get_auth_user(
            self, response: aiohttp.ClientResponse) -> Optional[models.User]:
        """Extract user info from OAuth2 proxy response headers."""
        email_header = response.headers.get('X-Auth-Request-Email')
        if email_header:
            user_hash = hashlib.md5(email_header.encode()).hexdigest(
            )[:common_utils.USER_HASH_LENGTH]
            return models.User(id=user_hash, name=email_header)
        return None
