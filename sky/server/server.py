"""SkyPilot API Server exposing RESTful APIs."""

import argparse
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import contextlib
import datetime
from enum import IntEnum
import hashlib
import html
import json
import multiprocessing
import os
import pathlib
import posixpath
import re
import resource
import shlex
import shutil
import socket
import struct
import sys
import threading
import traceback
import typing
from typing import (Any, Awaitable, Callable, Dict, List, Literal, Optional,
                    Set, Tuple, Type)
import uuid
import zipfile

import aiofiles
import anyio
import fastapi
from fastapi import responses as fastapi_responses
from fastapi.middleware import cors
import jwt as pyjwt
import starlette.middleware.base
import uvloop

import sky
from sky import catalog
from sky import check as sky_check
from sky import clouds
from sky import core
from sky import exceptions
from sky import execution
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.data import storage_utils
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.jobs.server import server as jobs_rest
from sky.metrics import utils as metrics_utils
from sky.provision import metadata_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.provision.slurm import utils as slurm_utils
from sky.recipes import server as recipes_rest
from sky.schemas.api import responses
from sky.serve.server import server as serve_rest
from sky.server import common
from sky.server import config as server_config
from sky.server import constants as server_constants
from sky.server import daemons
from sky.server import metrics
from sky.server import middleware_utils
from sky.server import plugins
from sky.server import state
from sky.server import stream_utils
from sky.server import version_check
from sky.server import versions
from sky.server.auth import loopback
from sky.server.auth import oauth2_proxy
from sky.server.auth import sessions as auth_sessions
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import preconditions
from sky.server.requests import request_names
from sky.server.requests import requests as requests_lib
from sky.skylet import constants
from sky.ssh_node_pools import server as ssh_node_pools_rest
from sky.usage import usage_lib
from sky.users import permission
from sky.users import server as users_rest
from sky.utils import admin_policy_utils
from sky.utils import command_runner
from sky.utils import common as common_lib
from sky.utils import common_utils
from sky.utils import context
from sky.utils import context_utils
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import env_options
from sky.utils import interactive_utils
from sky.utils import perf_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import ux_utils
from sky.utils.db import db_utils
from sky.volumes.server import server as volumes_rest
from sky.workspaces import server as workspaces_rest

if typing.TYPE_CHECKING:
    from sky import backends

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

P = ParamSpec('P')

_SERVER_USER_HASH_KEY = 'server_user_hash'

logger = sky_logging.init_logger(__name__)

# TODO(zhwu): Streaming requests, such log tailing after sky launch or sky logs,
# need to be detached from the main requests queue. Otherwise, the streaming
# response will block other requests from being processed.


def _basic_auth_401_response(content: str):
    """Return a 401 response with basic auth realm."""
    return fastapi.responses.JSONResponse(
        status_code=401,
        headers={'WWW-Authenticate': 'Basic realm=\"SkyPilot\"'},
        content=content)


def _try_set_basic_auth_user(request: fastapi.Request):
    auth_header = request.headers.get('authorization')
    if not auth_header or not auth_header.lower().startswith('basic '):
        return

    # Check username and password
    encoded = auth_header.split(' ', 1)[1]
    try:
        decoded = base64.b64decode(encoded).decode()
        username, password = decoded.split(':', 1)
    except Exception:  # pylint: disable=broad-except
        return

    users = global_user_state.get_user_by_name(username)
    if not users:
        return

    for user in users:
        if not user.name or not user.password:
            continue
        username_encoded = username.encode('utf8')
        db_username_encoded = user.name.encode('utf8')
        if (username_encoded == db_username_encoded and
                common.crypt_ctx.verify(password, user.password)):
            request.state.auth_user = user
            break


@middleware_utils.websocket_aware
class RBACMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to handle RBAC."""

    async def dispatch(self, request: fastapi.Request, call_next):
        # TODO(hailong): should have a list of paths
        # that are not checked for RBAC
        if (request.url.path.startswith('/dashboard/') or
                request.url.path.startswith('/api/')):
            return await call_next(request)

        auth_user = request.state.auth_user
        if auth_user is None:
            return await call_next(request)

        permission_service = permission.permission_service
        # Check the role permission
        if permission_service.check_endpoint_permission(auth_user.id,
                                                        request.url.path,
                                                        request.method):
            return fastapi.responses.JSONResponse(
                status_code=403, content={'detail': 'Forbidden'})

        return await call_next(request)


class RequestIDMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to add a request ID to each request."""

    async def dispatch(self, request: fastapi.Request, call_next):
        request_id = requests_lib.get_new_request_id()
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers['X-Skypilot-Request-ID'] = request_id
        return response


def _extract_identity_from_jwt(jwt_token: str, claim: str) -> Optional[str]:
    """Extract identity claim from a JWT token without verification.

    This is for trusted proxy scenarios where the external proxy has already
    verified the token. We only decode to extract the claim.

    Args:
        jwt_token: The JWT token string.
        claim: The claim name to extract (e.g., 'email', 'sub').

    Returns:
        The claim value if found, None otherwise.
    """
    try:
        # Trusted proxy scenario - skip all verification since the proxy
        # has already authenticated the request
        payload = pyjwt.decode(jwt_token,
                               options={
                                   'verify_signature': False,
                                   'verify_exp': False,
                                   'verify_aud': False,
                               })
        return payload.get(claim)
    except pyjwt.exceptions.DecodeError as e:
        logger.debug(f'Failed to decode JWT from header: {e}')
        return None
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Unexpected error decoding JWT: {e}')
        return None


def _extract_user_from_header(
    request: fastapi.Request,
    proxy_config: server_config.ExternalProxyConfig,
) -> Optional[models.User]:
    """Extract user identity from request header.

    Supports both plaintext headers (e.g., X-Auth-Request-Email) and
    JWT-encoded headers.
    """
    if proxy_config.header_name not in request.headers:
        return None

    header_value = request.headers[proxy_config.header_name]

    if proxy_config.header_format == 'jwt':
        user_name = _extract_identity_from_jwt(header_value,
                                               proxy_config.jwt_identity_claim)
    else:
        user_name = header_value

    if not user_name:
        return None

    user_hash = hashlib.md5(
        user_name.encode()).hexdigest()[:common_utils.USER_HASH_LENGTH]
    return models.User(id=user_hash, name=user_name)


def _get_auth_user_header(request: fastapi.Request) -> Optional[models.User]:
    """Legacy function for backward compatibility.

    This function is used by _generate_auth_token() which does not have
    access to the middleware config. It uses the default configuration
    which is backward compatible.
    """
    proxy_config = server_config.load_external_proxy_config()
    return _extract_user_from_header(request, proxy_config)


def _generate_auth_token(request: fastapi.Request) -> str:
    """Generate an auth token from the request.

    The token contains the user info and cookies, base64 encoded.
    Used by both /token and /api/v1/auth/authorize endpoints.
    """
    user = _get_auth_user_header(request)
    token_data = {
        # Token version number, bump for backwards incompatible changes.
        'v': 1,
        'user': user.id if user is not None else None,
        'cookies': dict(request.cookies),
    }
    json_bytes = json.dumps(token_data).encode('utf-8')
    return base64.b64encode(json_bytes).decode('utf-8')


@middleware_utils.websocket_aware
class InitializeRequestAuthUserMiddleware(
        starlette.middleware.base.BaseHTTPMiddleware):

    async def dispatch(self, request: fastapi.Request, call_next):
        # Make sure that request.state.auth_user is set. Otherwise, we may get a
        # KeyError while trying to read it.
        request.state.auth_user = None
        return await call_next(request)


@middleware_utils.websocket_aware
class BasicAuthMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to handle HTTP Basic Auth."""

    async def dispatch(self, request: fastapi.Request, call_next):
        # If a previous middleware already authenticated the user, pass through
        if request.state.auth_user is not None:
            return await call_next(request)

        if loopback.is_loopback_request(request):
            return await call_next(request)

        if request.url.path.startswith('/api/health'):
            # Try to set the auth user from basic auth
            _try_set_basic_auth_user(request)
            return await call_next(request)

        auth_header = request.headers.get('authorization')
        if not auth_header:
            return _basic_auth_401_response('Authentication required')

        # Only handle basic auth
        if not auth_header.lower().startswith('basic '):
            return _basic_auth_401_response('Invalid authentication method')

        # Check username and password
        encoded = auth_header.split(' ', 1)[1]
        try:
            decoded = base64.b64decode(encoded).decode()
            username, password = decoded.split(':', 1)
        except Exception:  # pylint: disable=broad-except
            return _basic_auth_401_response('Invalid basic auth')

        users = global_user_state.get_user_by_name(username)
        if not users:
            return _basic_auth_401_response('Invalid credentials')

        valid_user = False
        for user in users:
            if not user.name or not user.password:
                continue
            username_encoded = username.encode('utf8')
            db_username_encoded = user.name.encode('utf8')
            if (username_encoded == db_username_encoded and
                    common.crypt_ctx.verify(password, user.password)):
                valid_user = True
                request.state.auth_user = user
                break
        if not valid_user:
            return _basic_auth_401_response('Invalid credentials')

        return await call_next(request)


@middleware_utils.websocket_aware
class BearerTokenMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to handle Bearer Token Auth (Service Accounts)."""

    async def dispatch(self, request: fastapi.Request, call_next):
        """Make sure correct bearer token auth is present.

        1. If the request has the X-Skypilot-Auth-Mode: token header, it must
           have a valid bearer token.
        2. For backwards compatibility, if the request has a Bearer token
           beginning with "sky_" (even if X-Skypilot-Auth-Mode is not present),
           it must be a valid token.
        3. If X-Skypilot-Auth-Mode is not set to "token", and there is no Bearer
           token beginning with "sky_", allow the request to continue.

        In conjunction with an auth proxy, the idea is to make the auth proxy
        bypass requests with bearer tokens, instead setting the
        X-Skypilot-Auth-Mode header. The auth proxy should either validate the
        auth or set the header X-Skypilot-Auth-Mode: token.
        """
        # If a previous middleware already authenticated the user, pass through
        if request.state.auth_user is not None:
            return await call_next(request)

        has_skypilot_auth_header = (
            request.headers.get('X-Skypilot-Auth-Mode') == 'token')
        auth_header = request.headers.get('authorization')
        has_bearer_token_starting_with_sky = (
            auth_header and auth_header.lower().startswith('bearer ') and
            auth_header.split(' ', 1)[1].startswith('sky_'))

        if (not has_skypilot_auth_header and
                not has_bearer_token_starting_with_sky):
            # This is case #3 above. We do not need to validate the request.
            # No Bearer token, continue with normal processing (OAuth2 cookies,
            # etc.)
            return await call_next(request)
        # After this point, all requests must be validated.

        if auth_header is None:
            return fastapi.responses.JSONResponse(
                status_code=401, content={'detail': 'Authentication required'})

        # Extract token
        split_header = auth_header.split(' ', 1)
        if split_header[0].lower() != 'bearer':
            return fastapi.responses.JSONResponse(
                status_code=401,
                content={'detail': 'Invalid authentication method'})
        sa_token = split_header[1]

        # Handle SkyPilot service account tokens
        return await self._handle_service_account_token(request, sa_token,
                                                        call_next)

    async def _handle_service_account_token(self, request: fastapi.Request,
                                            sa_token: str, call_next):
        """Handle SkyPilot service account tokens."""
        # Check if service account tokens are enabled
        sa_enabled = os.environ.get(constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS,
                                    'false').lower()
        if sa_enabled != 'true':
            return fastapi.responses.JSONResponse(
                status_code=401,
                content={'detail': 'Service account authentication disabled'})

        try:
            # Import here to avoid circular imports
            # pylint: disable=import-outside-toplevel
            from sky.users.token_service import token_service

            # Verify and decode JWT token
            payload = token_service.verify_token(sa_token)

            if payload is None:
                logger.warning('Service account token verification failed')
                return fastapi.responses.JSONResponse(
                    status_code=401,
                    content={
                        'detail': 'Invalid or expired service account token'
                    })

            # Extract user information from JWT payload
            user_id = payload.get('sub')
            user_name = payload.get('name')
            token_id = payload.get('token_id')

            if not user_id or not token_id:
                logger.warning(
                    'Invalid token payload: missing user_id or token_id')
                return fastapi.responses.JSONResponse(
                    status_code=401,
                    content={'detail': 'Invalid token payload'})

            # Verify user still exists in database
            user_info = global_user_state.get_user(user_id)
            if user_info is None:
                logger.warning(
                    f'Service account user {user_id} no longer exists')
                return fastapi.responses.JSONResponse(
                    status_code=401,
                    content={'detail': 'Service account user no longer exists'})

            # Update last used timestamp for token tracking
            try:
                global_user_state.update_service_account_token_last_used(
                    token_id)
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(f'Failed to update token last used time: {e}')

            # Set the authenticated user
            auth_user = models.User(id=user_id,
                                    name=user_name or user_info.name)
            request.state.auth_user = auth_user

            logger.debug(f'Authenticated service account: {user_id}')

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Service account authentication failed: {e}',
                         exc_info=True)
            return fastapi.responses.JSONResponse(
                status_code=401,
                content={
                    'detail': f'Service account authentication failed: {str(e)}'
                })

        return await call_next(request)


@middleware_utils.websocket_aware
class AuthProxyMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to handle external auth proxy.

    This middleware extracts user identity from HTTP headers set by an
    external authentication proxy (e.g., oauth2-proxy)
    """

    # pylint: disable=redefined-outer-name
    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self.config = server_config.load_external_proxy_config()
        if self.config.enabled:
            logger.debug('AuthProxyMiddleware enabled with header: '
                         f'{self.config.header_name}, '
                         f'format: {self.config.header_format}')
        else:
            logger.debug('AuthProxyMiddleware disabled via configuration')

    async def dispatch(self, request: fastapi.Request, call_next):
        if not self.config.enabled:
            return await call_next(request)

        auth_user = _extract_user_from_header(request, self.config)

        if request.state.auth_user is not None:
            # Previous middleware is trusted more than this middleware.  For
            # instance, a client could set the Authorization and the
            # X-Auth-Request-Email header. In that case, the auth proxy will be
            # skipped and we should rely on the Bearer token to authenticate the
            # user - but that means the user could set X-Auth-Request-Email to
            # whatever the user wants. We should thus ignore it.
            if auth_user is not None:
                logger.debug('Warning: ignoring auth proxy header since the '
                             'auth user was already set.')
            return await call_next(request)

        # Add user to database if auth_user is present
        if auth_user is not None:
            newly_added = global_user_state.add_or_update_user(auth_user)
            if newly_added:
                permission.permission_service.add_user_if_not_exists(
                    auth_user.id)

        # Store user info in request.state for access by GET endpoints
        if auth_user is not None:
            request.state.auth_user = auth_user

        return await call_next(request)


# Default expiration time for upload ids before cleanup.
_DEFAULT_UPLOAD_EXPIRATION_TIME = datetime.timedelta(hours=1)
# Key: (upload_id, user_hash), Value: the time when the upload id needs to be
# cleaned up.
upload_ids_to_cleanup: Dict[Tuple[str, str], datetime.datetime] = {}


async def cleanup_upload_ids():
    """Cleans up the temporary chunks uploaded by the client after a delay."""
    # Clean up the temporary chunks uploaded by the client after an hour. This
    # is to prevent stale chunks taking up space on the API server.
    while True:
        await asyncio.sleep(3600)
        current_time = datetime.datetime.now()
        # We use list() to avoid modifying the dict while iterating over it.
        upload_ids_to_cleanup_list = list(upload_ids_to_cleanup.items())
        for (upload_id, user_hash), expire_time in upload_ids_to_cleanup_list:
            if current_time > expire_time:
                logger.info(f'Cleaning up upload id: {upload_id}')
                client_file_mounts_dir = (
                    common.API_SERVER_CLIENT_DIR.expanduser().resolve() /
                    user_hash / 'file_mounts')
                shutil.rmtree(client_file_mounts_dir / upload_id,
                              ignore_errors=True)
                (client_file_mounts_dir /
                 upload_id).with_suffix('.zip').unlink(missing_ok=True)
                upload_ids_to_cleanup.pop((upload_id, user_hash))


async def loop_lag_monitor(loop: asyncio.AbstractEventLoop,
                           interval: float = 0.1) -> None:
    target = loop.time() + interval

    pid = str(os.getpid())
    lag_threshold = perf_utils.get_loop_lag_threshold()

    def tick():
        nonlocal target
        now = loop.time()
        lag = max(0.0, now - target)
        if lag_threshold is not None and lag > lag_threshold:
            logger.warning(f'Event loop lag {lag} seconds exceeds threshold '
                           f'{lag_threshold} seconds.')
        metrics_utils.SKY_APISERVER_EVENT_LOOP_LAG_SECONDS.labels(
            pid=pid).observe(lag)
        target = now + interval
        loop.call_at(target, tick)

    loop.call_at(target, tick)


async def schedule_on_boot_check_async():
    try:
        await executor.schedule_request_async(
            request_id='skypilot-server-on-boot-check',
            request_name=request_names.RequestName.CHECK,
            request_body=payloads.CheckBody(),
            func=sky_check.check,
            schedule_type=requests_lib.ScheduleType.SHORT,
            is_skypilot_system=True,
        )
    except exceptions.RequestAlreadyExistsError:
        # Lifespan will be executed in each uvicorn worker process, we
        # can safely ignore the error if the task is already scheduled.
        logger.debug('Request skypilot-server-on-boot-check already exists.')


@contextlib.asynccontextmanager
async def lifespan(app: fastapi.FastAPI):  # pylint: disable=redefined-outer-name
    """FastAPI lifespan context manager."""
    del app  # unused
    # Startup: Run background tasks
    for event in daemons.INTERNAL_REQUEST_DAEMONS:
        if event.should_skip():
            continue
        try:
            await executor.schedule_request_async(
                request_id=event.id,
                request_name=event.name,
                request_body=payloads.RequestBody(),
                func=event.run_event,
                schedule_type=requests_lib.ScheduleType.SHORT,
                is_skypilot_system=True,
                # Request deamon should be retried if the process pool is
                # broken.
                retryable=True,
            )
        except exceptions.RequestAlreadyExistsError:
            # Lifespan will be executed in each uvicorn worker process, we
            # can safely ignore the error if the task is already scheduled.
            logger.debug(f'Request {event.id} already exists.')
    await schedule_on_boot_check_async()
    asyncio.create_task(cleanup_upload_ids())
    # Start periodic version check task (runs daily)
    asyncio.create_task(version_check.check_versions_periodically())
    if metrics_utils.METRICS_ENABLED:
        # Start monitoring the event loop lag in each server worker
        # event loop (process).
        asyncio.create_task(loop_lag_monitor(asyncio.get_event_loop()))
    yield
    # Shutdown: Add any cleanup code here if needed


# Add a new middleware class to handle /internal/dashboard prefix
class InternalDashboardPrefixMiddleware(
        starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to handle /internal/dashboard prefix in requests."""

    async def dispatch(self, request: fastapi.Request, call_next):
        path = request.url.path
        if path.startswith('/internal/dashboard/'):
            # Remove /internal/dashboard prefix and update request scope
            request.scope['path'] = path.replace('/internal/dashboard/', '/', 1)
        return await call_next(request)


class CacheControlStaticMiddleware(starlette.middleware.base.BaseHTTPMiddleware
                                  ):
    """Middleware to add cache control headers to static files."""

    async def dispatch(self, request: fastapi.Request, call_next):
        if request.url.path.startswith('/dashboard/_next'):
            response = await call_next(request)
            response.headers['Cache-Control'] = 'max-age=3600'
            return response
        return await call_next(request)


class PathCleanMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to check the path of requests."""

    async def dispatch(self, request: fastapi.Request, call_next):
        if request.url.path.startswith('/dashboard/'):
            # If the requested path is not relative to the expected directory,
            # then the user is attempting path traversal, so deny the request.
            parent = pathlib.Path('/dashboard')
            request_path = pathlib.Path(posixpath.normpath(request.url.path))
            if not _is_relative_to(request_path, parent):
                return fastapi.responses.JSONResponse(
                    status_code=403, content={'detail': 'Forbidden'})
        return await call_next(request)


@middleware_utils.websocket_aware
class GracefulShutdownMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to control requests when server is shutting down."""

    async def dispatch(self, request: fastapi.Request, call_next):
        if state.get_block_requests():
            # Allow /api/ paths to continue, which are critical to operate
            # on-going requests but will not submit new requests.
            if not request.url.path.startswith('/api/'):
                # Client will retry on 503 error.
                return fastapi.responses.JSONResponse(
                    status_code=503,
                    content={
                        'detail': 'Server is shutting down, '
                                  'please try again later.'
                    })

        return await call_next(request)


@middleware_utils.websocket_aware
class APIVersionMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to add API version to the request."""

    async def dispatch(self, request: fastapi.Request, call_next):
        version_info = versions.check_compatibility_at_server(request.headers)
        # Bypass version handling for backward compatibility with clients prior
        # to v0.11.0, the client will check the version in the body of
        # /api/health response and hint an upgrade.
        # TODO(aylei): remove this after v0.13.0 is released.
        if version_info is None:
            return await call_next(request)
        if version_info.error is None:
            versions.set_remote_api_version(version_info.api_version)
            versions.set_remote_version(version_info.version)
            response = await call_next(request)
        else:
            response = fastapi.responses.JSONResponse(
                status_code=400,
                content={
                    'error': common.ApiServerStatus.VERSION_MISMATCH.value,
                    'message': version_info.error,
                })
        response.headers[server_constants.API_VERSION_HEADER] = str(
            server_constants.API_VERSION)
        response.headers[server_constants.VERSION_HEADER] = \
            versions.get_local_readable_version()
        return response


app = fastapi.FastAPI(prefix='/api/v1', debug=True, lifespan=lifespan)
# Middleware wraps in the order defined here. E.g., given
#   app.add_middleware(Middleware1)
#   app.add_middleware(Middleware2)
#   app.add_middleware(Middleware3)
# The effect will be like:
#   Middleware3(Middleware2(Middleware1(request)))
# If MiddlewareN does something like print(n); call_next(); print(n), you'll get
#   3; 2; 1; <request>; 1; 2; 3
# Use environment variable to make the metrics middleware optional.
if os.environ.get(constants.ENV_VAR_SERVER_METRICS_ENABLED):
    app.add_middleware(metrics.PrometheusMiddleware)
app.add_middleware(APIVersionMiddleware)
# The order of all the authentication-related middleware is important.
# RBACMiddleware must precede all the auth middleware, so it can access
# request.state.auth_user.
app.add_middleware(RBACMiddleware)
app.add_middleware(InternalDashboardPrefixMiddleware)
app.add_middleware(GracefulShutdownMiddleware)
app.add_middleware(PathCleanMiddleware)
app.add_middleware(CacheControlStaticMiddleware)
app.add_middleware(
    cors.CORSMiddleware,
    # TODO(zhwu): in production deployment, we should restrict the allowed
    # origins to the domains that are allowed to access the API server.
    allow_origins=['*'],  # Specify the correct domains for production
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['X-Skypilot-Request-ID'])
# Authentication based on oauth2-proxy.
app.add_middleware(oauth2_proxy.OAuth2ProxyMiddleware)
# AuthProxyMiddleware should precede BasicAuthMiddleware and
# BearerTokenMiddleware, since it should be skipped if either of those set the
# auth user.
app.add_middleware(AuthProxyMiddleware)
enable_basic_auth = os.environ.get(constants.ENV_VAR_ENABLE_BASIC_AUTH, 'false')
disable_basic_auth_middleware = os.environ.get(
    constants.SKYPILOT_DISABLE_BASIC_AUTH_MIDDLEWARE, 'false')
if (str(enable_basic_auth).lower() == 'true' and
        str(disable_basic_auth_middleware).lower() != 'true'):
    app.add_middleware(BasicAuthMiddleware)
# Bearer token middleware should always be present to handle service account
# authentication
app.add_middleware(BearerTokenMiddleware)
# InitializeRequestAuthUserMiddleware must be the last added middleware so that
# request.state.auth_user is always set, but can be overridden by the auth
# middleware above.
app.add_middleware(InitializeRequestAuthUserMiddleware)
app.add_middleware(RequestIDMiddleware)

# Load plugins after all the middlewares are added, to keep the core
# middleware stack intact if a plugin adds new middlewares.
# Note: server.py will be imported twice in server process, once as
# the top-level entrypoint module and once imported by uvicorn, we only
# load the plugin when imported by uvicorn for server process.
# TODO(aylei): move uvicorn app out of the top-level module to avoid
# duplicate app initialization.
if __name__ == 'sky.server.server':
    plugins.load_plugins(plugins.ExtensionContext(app=app))

app.include_router(jobs_rest.router, prefix='/jobs', tags=['jobs'])
app.include_router(serve_rest.router, prefix='/serve', tags=['serve'])
app.include_router(users_rest.router, prefix='/users', tags=['users'])
app.include_router(workspaces_rest.router,
                   prefix='/workspaces',
                   tags=['workspaces'])
app.include_router(volumes_rest.router, prefix='/volumes', tags=['volumes'])
app.include_router(ssh_node_pools_rest.router,
                   prefix='/ssh_node_pools',
                   tags=['ssh_node_pools'])
app.include_router(recipes_rest.router, prefix='/recipes', tags=['recipes'])
# increase the resource limit for the server
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))


@app.exception_handler(exceptions.ConcurrentWorkerExhaustedError)
def handle_concurrent_worker_exhausted_error(
        request: fastapi.Request, e: exceptions.ConcurrentWorkerExhaustedError):
    del request  # request is not used
    # Print detailed error message to server log
    logger.error('Concurrent worker exhausted: '
                 f'{common_utils.format_exception(e)}')
    with ux_utils.enable_traceback():
        logger.error(f'  Traceback: {traceback.format_exc()}')
    # Return human readable error message to client
    return fastapi.responses.JSONResponse(
        status_code=503,
        content={
            'detail':
                ('The server has exhausted its concurrent worker limit. '
                 'Please try again or scale the server if the load persists.')
        })


@app.get('/token')
async def token(request: fastapi.Request,
                local_port: Optional[int] = None) -> fastapi.responses.Response:
    del local_port  # local_port is used by the served js, but ignored by server
    # Use base64 encoding to avoid having to escape anything in the HTML.
    base64_str = _generate_auth_token(request)
    user = _get_auth_user_header(request)

    html_dir = pathlib.Path(__file__).parent / 'html'
    token_page_path = html_dir / 'token_page.html'
    try:
        with open(token_page_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except FileNotFoundError as e:
        raise fastapi.HTTPException(
            status_code=500, detail='Token page template not found.') from e

    user_info_string = html.escape(
        f'Logged in as {user.name}') if user is not None else ''
    html_content = html_content.replace(
        'SKYPILOT_API_SERVER_USER_TOKEN_PLACEHOLDER',
        base64_str).replace('USER_PLACEHOLDER', user_info_string)

    return fastapi.responses.HTMLResponse(
        content=html_content,
        headers={
            'Cache-Control': 'no-cache, no-transform',
            # X-Accel-Buffering: no is useful for preventing buffering issues
            # with some reverse proxies.
            'X-Accel-Buffering': 'no'
        })


@app.get('/api/v1/auth/token')
async def poll_auth_token(
        code_verifier: Optional[str] = None) -> fastapi.responses.Response:
    """Poll for auth token using code_verifier.

    Computes code_challenge from code_verifier to look up the session.

    Query params:
        code_verifier: The original code verifier (required)

    Returns:
        - 200 with token if session is authorized
        - 404 if session not found (user hasn't clicked Authorize yet)
    """
    if not code_verifier:
        raise fastapi.HTTPException(status_code=400,
                                    detail='code_verifier is required')

    auth_token = auth_sessions.auth_session_store.poll_session(code_verifier)

    if auth_token is None:
        raise fastapi.HTTPException(status_code=404, detail='Session not found')

    return fastapi.responses.JSONResponse(content={'token': auth_token},
                                          headers={'Cache-Control': 'no-store'})


@app.post('/api/v1/auth/authorize')
async def authorize_auth_session(
        request: fastapi.Request) -> fastapi.responses.JSONResponse:
    """Authorize an auth session (called when user clicks Authorize button).

    This endpoint requires authentication (via auth proxy cookies).
    It generates the token and creates a session for the CLI to retrieve.

    Request body:
        code_challenge: The code challenge from the CLI

    Returns:
        - 200 if successfully authorized
    """
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        raise fastapi.HTTPException(status_code=400,
                                    detail='Invalid JSON body') from e

    code_challenge = body.get('code_challenge')
    if not code_challenge:
        raise fastapi.HTTPException(status_code=400,
                                    detail='code_challenge is required')
    # Validate format: base64url-encoded SHA256, 43 chars of A-Za-z0-9_-
    if not re.match(r'^[A-Za-z0-9_-]{43}$', code_challenge):
        raise fastapi.HTTPException(status_code=400,
                                    detail='Invalid code_challenge format')

    auth_token = _generate_auth_token(request)

    # Create the session with the token
    auth_sessions.auth_session_store.create_session(code_challenge, auth_token)

    return fastapi.responses.JSONResponse(content={'status': 'authorized'},
                                          headers={'Cache-Control': 'no-store'})


@app.get('/auth/authorize')
async def authorize_page(
        request: fastapi.Request) -> fastapi.responses.Response:
    """Serve the authorization page where users click to authorize the CLI.

    This page requires authentication (via auth proxy). The code_challenge
    query param is read by JavaScript and sent to the POST endpoint.
    """
    user = request.state.auth_user
    if user is None:
        user = _get_auth_user_header(request)
    user_info = html.escape(
        f'Logged in as {user.name}') if user is not None else ''

    html_dir = pathlib.Path(__file__).parent / 'html'
    authorize_page_path = html_dir / 'authorize_page.html'
    with open(authorize_page_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    html_content = html_content.replace('USER_PLACEHOLDER', user_info)

    return fastapi.responses.HTMLResponse(
        content=html_content,
        headers={'Cache-Control': 'no-cache, no-transform'})


@app.post('/check')
async def check(request: fastapi.Request,
                check_body: payloads.CheckBody) -> None:
    """Checks enabled clouds."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CHECK,
        request_body=check_body,
        func=sky_check.check,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.get('/enabled_clouds')
async def enabled_clouds(request: fastapi.Request,
                         workspace: Optional[str] = None,
                         expand: bool = False) -> None:
    """Gets enabled clouds on the server."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.ENABLED_CLOUDS,
        request_body=payloads.EnabledCloudsBody(workspace=workspace,
                                                expand=expand),
        func=core.enabled_clouds,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.post('/realtime_kubernetes_gpu_availability')
async def realtime_kubernetes_gpu_availability(
    request: fastapi.Request,
    realtime_gpu_availability_body: payloads.RealtimeGpuAvailabilityRequestBody
) -> None:
    """Gets real-time Kubernetes GPU availability."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.
        REALTIME_KUBERNETES_GPU_AVAILABILITY,
        request_body=realtime_gpu_availability_body,
        func=core.realtime_kubernetes_gpu_availability,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.post('/kubernetes_node_info')
async def kubernetes_node_info(
        request: fastapi.Request,
        kubernetes_node_info_body: payloads.KubernetesNodeInfoRequestBody
) -> None:
    """Gets Kubernetes nodes information and hints."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.KUBERNETES_NODE_INFO,
        request_body=kubernetes_node_info_body,
        func=kubernetes_utils.get_kubernetes_node_info,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.post('/slurm_gpu_availability')
async def slurm_gpu_availability(
    request: fastapi.Request,
    slurm_gpu_availability_body: payloads.SlurmGpuAvailabilityRequestBody
) -> None:
    """Gets real-time Slurm GPU availability."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.REALTIME_SLURM_GPU_AVAILABILITY,
        request_body=slurm_gpu_availability_body,
        func=core.realtime_slurm_gpu_availability,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


# Keep the GET method for backwards compatibility
@app.api_route('/slurm_node_info', methods=['GET', 'POST'])
async def slurm_node_info(
        request: fastapi.Request,
        slurm_node_info_body: payloads.SlurmNodeInfoRequestBody) -> None:
    """Gets detailed information for each node in the Slurm cluster."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SLURM_NODE_INFO,
        request_body=slurm_node_info_body,
        func=slurm_utils.slurm_node_info,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.get('/status_kubernetes')
async def status_kubernetes(request: fastapi.Request) -> None:
    """[Experimental] Get all SkyPilot resources (including from other '
    'users) in the current Kubernetes context."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.STATUS_KUBERNETES,
        request_body=payloads.RequestBody(),
        func=core.status_kubernetes,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.post('/list_accelerators')
async def list_accelerators(
        request: fastapi.Request,
        list_accelerator_counts_body: payloads.ListAcceleratorsBody) -> None:
    """Gets list of accelerators from cloud catalog."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.LIST_ACCELERATORS,
        request_body=list_accelerator_counts_body,
        func=catalog.list_accelerators,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.post('/list_accelerator_counts')
async def list_accelerator_counts(
        request: fastapi.Request,
        list_accelerator_counts_body: payloads.ListAcceleratorCountsBody
) -> None:
    """Gets list of accelerator counts from cloud catalog."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.LIST_ACCELERATOR_COUNTS,
        request_body=list_accelerator_counts_body,
        func=catalog.list_accelerator_counts,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.post('/validate')
async def validate(validate_body: payloads.ValidateBody) -> None:
    """Validates the user's DAG."""
    # TODO(SKY-1035): validate if existing cluster satisfies the requested
    # resources, e.g. sky exec --gpus V100:8 existing-cluster-with-no-gpus

    # TODO: Our current launch process is split into three calls:
    # validate, optimize, and launch. This requires us to apply the admin policy
    # in each step, which may be an expensive operation. We should consolidate
    # these into a single call or have a TTL cache for (task, admin_policy)
    # pairs.
    logger.debug(f'Validating tasks: {validate_body.dag}')

    context.initialize()
    ctx = context.get()
    assert ctx is not None
    # TODO(aylei): generalize this to all requests without a db record.
    ctx.override_envs(validate_body.env_vars)

    def validate_dag(dag: dag_utils.dag_lib.Dag):
        # TODO: Admin policy may contain arbitrary code, which may be expensive
        # to run and may block the server thread. However, moving it into the
        # executor adds a ~150ms penalty on the local API server because of
        # added RTTs. For now, we stick to doing the validation inline in the
        # server thread.
        with admin_policy_utils.apply_and_use_config_in_current_request(
                dag,
                request_name=request_names.AdminPolicyRequestName.VALIDATE,
                request_options=validate_body.get_request_options()) as dag:
            dag.resolve_and_validate_volumes()
            # Skip validating workdir and file_mounts, as those need to be
            # validated after the files are uploaded to the SkyPilot API server
            # with `upload_mounts_to_api_server`.
            dag.validate(skip_file_mounts=True, skip_workdir=True)

    try:
        dag = dag_utils.load_dag_from_yaml_str(validate_body.dag)
        # Apply admin policy and validate DAG is blocking, run it in a separate
        # thread executor to avoid blocking the uvicorn event loop.
        await asyncio.to_thread(validate_dag, dag)
    except Exception as e:  # pylint: disable=broad-except
        # Print the exception to the API server log.
        if env_options.Options.SHOW_DEBUG_INFO.get():
            logger.info('/validate exception:', exc_info=True)
        # Set the exception stacktrace for the serialized exception.
        requests_lib.set_exception_stacktrace(e)
        raise fastapi.HTTPException(
            status_code=400, detail=exceptions.serialize_exception(e)) from e


@app.post('/optimize')
async def optimize(optimize_body: payloads.OptimizeBody,
                   request: fastapi.Request) -> None:
    """Optimizes the user's DAG."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.OPTIMIZE,
        request_body=optimize_body,
        ignore_return_value=True,
        func=core.optimize,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.post('/upload')
async def upload_zip_file(request: fastapi.Request, user_hash: str,
                          upload_id: str, chunk_index: int,
                          total_chunks: int) -> payloads.UploadZipFileResponse:
    """Uploads a zip file to the API server.

    This endpoints can be called multiple times for the same upload_id with
    different chunk_index. The server will merge the chunks and unzip the file
    when all chunks are uploaded.

    This implementation is simplified and may need to be improved in the future,
    e.g., adopting S3-style multipart upload.

    Args:
        user_hash: The user hash.
        upload_id: The upload id, a valid SkyPilot run_timestamp appended with 8
            hex characters, e.g. 'sky-2025-01-17-09-10-13-933602-35d31c22'.
        chunk_index: The chunk index, starting from 0.
        total_chunks: The total number of chunks.
    """
    # Field _body would be set if the request body has been received, fail fast
    # to surface potential memory issues, i.e. catch the issue in our smoke
    # test.
    # pylint: disable=protected-access
    if hasattr(request, '_body'):
        raise fastapi.HTTPException(
            status_code=500,
            detail='Upload request body should not be received before streaming'
        )
    # Add the upload id to the cleanup list.
    upload_ids_to_cleanup[(upload_id,
                           user_hash)] = (datetime.datetime.now() +
                                          _DEFAULT_UPLOAD_EXPIRATION_TIME)
    # For anonymous access, use the user hash from client
    user_id = user_hash
    if request.state.auth_user is not None:
        # Otherwise, the authenticated identity should be used.
        user_id = request.state.auth_user.id

    # TODO(SKY-1271): We need to double check security of uploading zip file.
    client_file_mounts_dir = (
        common.API_SERVER_CLIENT_DIR.expanduser().resolve() / user_id /
        'file_mounts')
    await anyio.Path(client_file_mounts_dir).mkdir(parents=True, exist_ok=True)

    # Check upload_id to be a valid SkyPilot run_timestamp appended with 8 hex
    # characters, e.g. 'sky-2025-01-17-09-10-13-933602-35d31c22'.
    if not re.match(
            r'sky-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-'
            r'[0-9]{2}-[0-9]{6}-[0-9a-f]{8}$', upload_id):
        raise ValueError(
            f'Invalid upload_id: {upload_id}. Please use a valid uuid.')
    # Check chunk_index to be a valid integer
    if chunk_index < 0 or chunk_index >= total_chunks:
        raise ValueError(
            f'Invalid chunk_index: {chunk_index}. Please use a valid integer.')
    # Check total_chunks to be a valid integer
    if total_chunks < 1:
        raise ValueError(
            f'Invalid total_chunks: {total_chunks}. Please use a valid integer.'
        )

    if total_chunks == 1:
        zip_file_path = client_file_mounts_dir / f'{upload_id}.zip'
    else:
        chunk_dir = client_file_mounts_dir / upload_id
        await anyio.Path(chunk_dir).mkdir(parents=True, exist_ok=True)
        zip_file_path = chunk_dir / f'part{chunk_index}.incomplete'

    try:
        async with aiofiles.open(zip_file_path, 'wb') as f:
            async for chunk in request.stream():
                await f.write(chunk)
    except starlette.requests.ClientDisconnect as e:
        # Client disconnected, remove the zip file.
        zip_file_path.unlink(missing_ok=True)
        raise fastapi.HTTPException(
            status_code=400,
            detail='Client disconnected, please try again.') from e
    except Exception as e:
        logger.error(f'Error uploading zip file: {zip_file_path}')
        # Client disconnected, remove the zip file.
        zip_file_path.unlink(missing_ok=True)
        raise fastapi.HTTPException(
            status_code=500,
            detail=('Error uploading zip file: '
                    f'{common_utils.format_exception(e)}'))

    def get_missing_chunks(total_chunks: int) -> Set[str]:
        return set(f'part{i}' for i in range(total_chunks)) - set(
            p.name for p in chunk_dir.glob('part*'))

    if total_chunks > 1:
        zip_file_path.rename(zip_file_path.with_suffix(''))
        missing_chunks = get_missing_chunks(total_chunks)
        if missing_chunks:
            return payloads.UploadZipFileResponse(
                status=responses.UploadStatus.UPLOADING.value,
                missing_chunks=missing_chunks)
        zip_file_path = client_file_mounts_dir / f'{upload_id}.zip'
        async with aiofiles.open(zip_file_path, 'wb') as zip_file:
            for chunk in range(total_chunks):
                async with aiofiles.open(chunk_dir / f'part{chunk}', 'rb') as f:
                    while True:
                        # Use 64KB buffer to avoid memory overflow, same size as
                        # shutil.copyfileobj.
                        data = await f.read(64 * 1024)
                        if not data:
                            break
                        await zip_file.write(data)

    logger.info(f'Uploaded zip file: {zip_file_path}')
    await unzip_file(zip_file_path, client_file_mounts_dir)
    if total_chunks > 1:
        await asyncio.to_thread(shutil.rmtree, chunk_dir)
    return payloads.UploadZipFileResponse(
        status=responses.UploadStatus.COMPLETED.value)


def _is_relative_to(path: pathlib.Path, parent: pathlib.Path) -> bool:
    """Checks if path is a subpath of parent."""
    try:
        # We cannot use is_relative_to, as it is only added after 3.9.
        path.relative_to(parent)
        return True
    except ValueError:
        return False


async def unzip_file(zip_file_path: pathlib.Path,
                     client_file_mounts_dir: pathlib.Path) -> None:
    """Unzips a zip file without blocking the event loop."""

    def _do_unzip() -> None:
        try:
            with zipfile.ZipFile(zip_file_path, 'r') as zipf:
                for member in zipf.infolist():
                    # Determine the new path
                    original_path = os.path.normpath(member.filename)
                    new_path = client_file_mounts_dir / original_path.lstrip(
                        '/')

                    # Security check: ensure extracted path stays within target
                    # directory to prevent Zip Slip attacks (path traversal via
                    # malicious "../" sequences in archive member names).
                    resolved_path = new_path.resolve()
                    if not _is_relative_to(resolved_path,
                                           client_file_mounts_dir):
                        raise ValueError(
                            f'Zip member {member.filename!r} would extract '
                            'outside target directory. Aborted.')

                    if (member.external_attr >> 28) == 0xA:
                        # Symlink. Read the target path and create a symlink.
                        new_path.parent.mkdir(parents=True, exist_ok=True)
                        target = zipf.read(member).decode()
                        assert not os.path.isabs(target), target
                        # Since target is a relative path, we need to check that
                        # it is under `client_file_mounts_dir` for security.
                        full_target_path = (new_path.parent / target).resolve()
                        if not _is_relative_to(full_target_path,
                                               client_file_mounts_dir):
                            raise ValueError(
                                f'Symlink target {target} leads to a '
                                'file not in userspace. Aborted.')

                        if new_path.exists() or new_path.is_symlink():
                            new_path.unlink(missing_ok=True)
                        new_path.symlink_to(
                            target,
                            target_is_directory=member.filename.endswith('/'))
                        continue

                    # Handle directories
                    if member.filename.endswith('/'):
                        new_path.mkdir(parents=True, exist_ok=True)
                        continue

                    # Handle files
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    with zipf.open(member) as member_file, new_path.open(
                            'wb') as f:
                        # Use shutil.copyfileobj to copy files in chunks,
                        # so it does not load the entire file into memory.
                        shutil.copyfileobj(member_file, f)
        except zipfile.BadZipFile as e:
            logger.error(f'Bad zip file: {zip_file_path}')
            raise fastapi.HTTPException(
                status_code=400,
                detail=f'Invalid zip file: {common_utils.format_exception(e)}')
        except Exception as e:
            logger.error(f'Error unzipping file: {zip_file_path}')
            raise fastapi.HTTPException(
                status_code=500,
                detail=(f'Error unzipping file: '
                        f'{common_utils.format_exception(e)}'))
        finally:
            # Cleanup the temporary file regardless of
            # success/failure handling above
            zip_file_path.unlink(missing_ok=True)

    await asyncio.to_thread(_do_unzip)


@app.post('/launch')
async def launch(launch_body: payloads.LaunchBody,
                 request: fastapi.Request) -> None:
    """Launches a cluster or task."""
    request_id = request.state.request_id
    logger.info(f'Launching request: {request_id}')
    await executor.schedule_request_async(
        request_id,
        request_name=request_names.RequestName.CLUSTER_LAUNCH,
        request_body=launch_body,
        func=execution.launch,
        schedule_type=requests_lib.ScheduleType.LONG,
        request_cluster_name=launch_body.cluster_name,
        retryable=launch_body.retry_until_up,
        auth_user=request.state.auth_user,
    )


@app.post('/exec')
# pylint: disable=redefined-builtin
async def exec(request: fastapi.Request, exec_body: payloads.ExecBody) -> None:
    """Executes a task on an existing cluster."""
    cluster_name = exec_body.cluster_name
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_EXEC,
        request_body=exec_body,
        func=execution.exec,
        precondition=preconditions.ClusterStartCompletePrecondition(
            request_id=request.state.request_id,
            cluster_name=cluster_name,
        ),
        schedule_type=requests_lib.ScheduleType.LONG,
        request_cluster_name=cluster_name,
        auth_user=request.state.auth_user,
    )


@app.post('/stop')
async def stop(request: fastapi.Request,
               stop_body: payloads.StopOrDownBody) -> None:
    """Stops a cluster."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_STOP,
        request_body=stop_body,
        func=core.stop,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=stop_body.cluster_name,
        auth_user=request.state.auth_user,
    )


@app.post('/status')
async def status(
    request: fastapi.Request,
    status_body: payloads.StatusBody = payloads.StatusBody()
) -> None:
    """Gets cluster statuses."""
    if state.get_block_requests():
        raise fastapi.HTTPException(
            status_code=503,
            detail='Server is shutting down, please try again later.')
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_STATUS,
        request_body=status_body,
        func=core.status,
        schedule_type=(requests_lib.ScheduleType.LONG if
                       status_body.refresh != common_lib.StatusRefreshMode.NONE
                       else requests_lib.ScheduleType.SHORT),
        auth_user=request.state.auth_user,
    )


@app.post('/endpoints')
async def endpoints(request: fastapi.Request,
                    endpoint_body: payloads.EndpointsBody) -> None:
    """Gets the endpoint for a given cluster and port number (endpoint)."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_ENDPOINTS,
        request_body=endpoint_body,
        func=core.endpoints,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=endpoint_body.cluster,
        auth_user=request.state.auth_user,
    )


@app.post('/down')
async def down(request: fastapi.Request,
               down_body: payloads.StopOrDownBody) -> None:
    """Tears down a cluster."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_DOWN,
        request_body=down_body,
        func=core.down,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=down_body.cluster_name,
        auth_user=request.state.auth_user,
    )


@app.post('/start')
async def start(request: fastapi.Request,
                start_body: payloads.StartBody) -> None:
    """Restarts a cluster."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_START,
        request_body=start_body,
        func=core.start,
        schedule_type=requests_lib.ScheduleType.LONG,
        request_cluster_name=start_body.cluster_name,
        auth_user=request.state.auth_user,
    )


@app.post('/autostop')
async def autostop(request: fastapi.Request,
                   autostop_body: payloads.AutostopBody) -> None:
    """Schedules an autostop/autodown for a cluster."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_AUTOSTOP,
        request_body=autostop_body,
        func=core.autostop,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=autostop_body.cluster_name,
        auth_user=request.state.auth_user,
    )


@app.post('/queue')
async def queue(request: fastapi.Request,
                queue_body: payloads.QueueBody) -> None:
    """Gets the job queue of a cluster."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_QUEUE,
        request_body=queue_body,
        func=core.queue,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=queue_body.cluster_name,
        auth_user=request.state.auth_user,
    )


@app.post('/job_status')
async def job_status(request: fastapi.Request,
                     job_status_body: payloads.JobStatusBody) -> None:
    """Gets the status of a job."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_JOB_STATUS,
        request_body=job_status_body,
        func=core.job_status,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=job_status_body.cluster_name,
        auth_user=request.state.auth_user,
    )


@app.post('/cancel')
async def cancel(request: fastapi.Request,
                 cancel_body: payloads.CancelBody) -> None:
    """Cancels jobs on a cluster."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_JOB_CANCEL,
        request_body=cancel_body,
        func=core.cancel,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=cancel_body.cluster_name,
        auth_user=request.state.auth_user,
    )


@app.post('/logs')
async def logs(
    request: fastapi.Request, cluster_job_body: payloads.ClusterJobBody,
    background_tasks: fastapi.BackgroundTasks
) -> fastapi.responses.StreamingResponse:
    """Tails the logs of a job."""
    # TODO(zhwu): This should wait for the request on the cluster, e.g., async
    # launch, to finish, so that a user does not need to manually pull the
    # request status.
    executor.check_request_thread_executor_available()
    request_task = await executor.prepare_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_JOB_LOGS,
        request_body=cluster_job_body,
        func=core.tail_logs,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=cluster_job_body.cluster_name,
        auth_user=request.state.auth_user,
    )
    task = executor.execute_request_in_coroutine(request_task)
    background_tasks.add_task(task.cancel)
    # TODO(zhwu): This makes viewing logs in browser impossible. We should adopt
    # the same approach as /stream.
    return stream_utils.stream_response_for_long_request(
        request_id=request.state.request_id,
        logs_path=request_task.log_path,
        background_tasks=background_tasks,
        kill_request_on_disconnect=False,
    )


@app.post('/download_logs')
async def download_logs(
        request: fastapi.Request,
        cluster_jobs_body: payloads.ClusterJobsDownloadLogsBody) -> None:
    """Downloads the logs of a job."""
    user_hash = cluster_jobs_body.env_vars[constants.USER_ID_ENV_VAR]
    logs_dir_on_api_server = common.api_server_user_logs_dir_prefix(user_hash)
    logs_dir_on_api_server.expanduser().mkdir(parents=True, exist_ok=True)
    # We should reuse the original request body, so that the env vars, such as
    # user hash, are kept the same.
    cluster_jobs_body.local_dir = str(logs_dir_on_api_server)
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_JOB_DOWNLOAD_LOGS,
        request_body=cluster_jobs_body,
        func=core.download_logs,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=cluster_jobs_body.cluster_name,
        auth_user=request.state.auth_user,
    )


@app.post('/download')
async def download(download_body: payloads.DownloadBody,
                   request: fastapi.Request) -> None:
    """Downloads a folder from the cluster to the local machine."""
    folder_paths = [
        pathlib.Path(folder_path) for folder_path in download_body.folder_paths
    ]
    user_hash = download_body.env_vars[constants.USER_ID_ENV_VAR]
    logs_dir_on_api_server = common.api_server_user_logs_dir_prefix(user_hash)
    for folder_path in folder_paths:
        if not str(folder_path).startswith(str(logs_dir_on_api_server)):
            raise fastapi.HTTPException(
                status_code=400,
                detail=
                f'Invalid folder path: {folder_path}; {logs_dir_on_api_server}')

        if not folder_path.expanduser().resolve().exists():
            raise fastapi.HTTPException(
                status_code=404, detail=f'Folder not found: {folder_path}')

    # Create a temporary zip file
    log_id = str(uuid.uuid4().hex)
    zip_filename = f'folder_{log_id}.zip'
    zip_path = pathlib.Path(
        logs_dir_on_api_server).expanduser().resolve() / zip_filename

    try:

        def _zip_files_and_folders(folder_paths, zip_path):
            folders = [
                str(folder_path.expanduser().resolve())
                for folder_path in folder_paths
            ]
            # Check for optional query parameter to control zip entry structure
            relative = request.query_params.get('relative', 'home')
            if relative == 'items':
                # Dashboard-friendly: entries relative to selected folders
                storage_utils.zip_files_and_folders(folders,
                                                    zip_path,
                                                    relative_to_items=True)
            else:
                # CLI-friendly (default): entries with full paths for mapping
                storage_utils.zip_files_and_folders(folders, zip_path)

        await asyncio.to_thread(_zip_files_and_folders, folder_paths, zip_path)

        # Add home path to the response headers, so that the client can replace
        # the remote path in the zip file to the local path.
        headers = {
            'Content-Disposition': f'attachment; filename="{zip_filename}"',
            'X-Home-Path': str(pathlib.Path.home())
        }

        # Return the zip file as a download
        return fastapi.responses.FileResponse(
            path=zip_path,
            filename=zip_filename,
            media_type='application/zip',
            headers=headers,
            background=fastapi.BackgroundTasks().add_task(
                lambda: zip_path.unlink(missing_ok=True)))
    except Exception as e:
        raise fastapi.HTTPException(status_code=500,
                                    detail=f'Error creating zip file: {str(e)}')


# TODO(aylei): run it asynchronously after global_user_state support async op
@app.post('/provision_logs')
def provision_logs(provision_logs_body: payloads.ProvisionLogsBody,
                   follow: bool = True,
                   tail: int = 0) -> fastapi.responses.StreamingResponse:
    """Streams the provision.log for the latest launch request of a cluster."""
    log_path = None
    cluster_name = provision_logs_body.cluster_name
    worker = provision_logs_body.worker
    # stream head node logs
    if worker is None:
        # Prefer clusters table first, then cluster_history as fallback.
        log_path_str = global_user_state.get_cluster_provision_log_path(
            cluster_name)
        if not log_path_str:
            log_path_str = (
                global_user_state.get_cluster_history_provision_log_path(
                    cluster_name))
        if not log_path_str:
            raise fastapi.HTTPException(
                status_code=404,
                detail=('Provision log path is not recorded for this cluster. '
                        'Please relaunch to generate provisioning logs.'))
        log_path = pathlib.Path(log_path_str).expanduser().resolve()
        if not log_path.exists():
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'Provision log path does not exist: {str(log_path)}')

    # stream worker node logs
    else:
        handle = global_user_state.get_handle_from_cluster_name(cluster_name)
        if handle is None:
            raise fastapi.HTTPException(
                status_code=404,
                detail=('Cluster handle is not recorded for this cluster. '
                        'Please relaunch to generate provisioning logs.'))
        # instance_ids includes head node
        instance_ids = handle.instance_ids
        if instance_ids is None:
            raise fastapi.HTTPException(
                status_code=400,
                detail='Instance IDs are not recorded for this cluster. '
                'Please relaunch to generate provisioning logs.')
        if worker > len(instance_ids) - 1:
            raise fastapi.HTTPException(
                status_code=400,
                detail=f'Worker {worker} is out of range. '
                f'The cluster has {len(instance_ids)} nodes.')
        log_path = metadata_utils.get_instance_log_dir(
            handle.get_cluster_name_on_cloud(), instance_ids[worker])

    # Tail semantics: 0 means print all lines. Convert 0 -> None for streamer.
    effective_tail = None if tail is None or tail <= 0 else tail

    return fastapi.responses.StreamingResponse(
        content=stream_utils.log_streamer(None,
                                          log_path,
                                          tail=effective_tail,
                                          follow=follow,
                                          cluster_name=cluster_name),
        media_type='text/plain',
        headers={
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
            'Transfer-Encoding': 'chunked',
        },
    )


@app.post('/autostop_logs')
async def autostop_logs(
    request: fastapi.Request, autostop_logs_body: payloads.AutostopLogsBody,
    background_tasks: fastapi.BackgroundTasks
) -> fastapi.responses.StreamingResponse:
    """Tails the autostop hook logs of a cluster."""
    executor.check_request_thread_executor_available()
    request_task = await executor.prepare_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_AUTOSTOP_LOGS,
        request_body=autostop_logs_body,
        func=core.tail_autostop_logs,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=autostop_logs_body.cluster_name,
        auth_user=request.state.auth_user,
    )
    task = executor.execute_request_in_coroutine(request_task)
    background_tasks.add_task(task.cancel)
    return stream_utils.stream_response_for_long_request(
        request_id=request.state.request_id,
        logs_path=request_task.log_path,
        background_tasks=background_tasks,
        kill_request_on_disconnect=False,
    )


@app.post('/cost_report')
async def cost_report(request: fastapi.Request,
                      cost_report_body: payloads.CostReportBody) -> None:
    """Gets the cost report of a cluster."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_COST_REPORT,
        request_body=cost_report_body,
        func=core.cost_report,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.post('/cluster_events')
async def cluster_events(
        request: fastapi.Request,
        cluster_events_body: payloads.ClusterEventsBody) -> None:
    """Gets events for a cluster."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_EVENTS,
        request_body=cluster_events_body,
        func=core.get_cluster_events,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=cluster_events_body.cluster_name or '',
        auth_user=request.state.auth_user,
    )


@app.get('/storage/ls')
async def storage_ls(request: fastapi.Request) -> None:
    """Gets the storages."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.STORAGE_LS,
        request_body=payloads.RequestBody(),
        func=core.storage_ls,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.post('/storage/delete')
async def storage_delete(request: fastapi.Request,
                         storage_body: payloads.StorageBody) -> None:
    """Deletes a storage."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.STORAGE_DELETE,
        request_body=storage_body,
        func=core.storage_delete,
        schedule_type=requests_lib.ScheduleType.LONG,
        auth_user=request.state.auth_user,
    )


@app.post('/local_up')
async def local_up(request: fastapi.Request,
                   local_up_body: payloads.LocalUpBody) -> None:
    """Launches a Kubernetes cluster on API server."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.LOCAL_UP,
        request_body=local_up_body,
        func=core.local_up,
        schedule_type=requests_lib.ScheduleType.LONG,
        auth_user=request.state.auth_user,
    )


@app.post('/local_down')
async def local_down(request: fastapi.Request,
                     local_down_body: payloads.LocalDownBody) -> None:
    """Tears down the Kubernetes cluster started by local_up."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.LOCAL_DOWN,
        request_body=local_down_body,
        func=core.local_down,
        schedule_type=requests_lib.ScheduleType.LONG,
        auth_user=request.state.auth_user,
    )


async def get_expanded_request_id(request_id: str) -> str:
    """Gets the expanded request ID for a given request ID prefix."""
    request_tasks = await requests_lib.get_requests_async_with_prefix(
        request_id, fields=['request_id'])
    if request_tasks is None:
        raise fastapi.HTTPException(status_code=404,
                                    detail=f'Request {request_id!r} not found')
    if len(request_tasks) > 1:
        raise fastapi.HTTPException(status_code=400,
                                    detail=('Multiple requests found for '
                                            f'request ID prefix: {request_id}'))
    return request_tasks[0].request_id


# === API server related APIs ===
@app.get('/api/get', response_class=fastapi_responses.ORJSONResponse)
async def api_get(request_id: str) -> payloads.RequestPayload:
    """Gets a request with a given request ID prefix."""
    # Validate request_id prefix matches a single request.
    request_id = await get_expanded_request_id(request_id)

    while True:
        req_status = await requests_lib.get_request_status_async(request_id)
        if req_status is None:
            print(f'No task with request ID {request_id}', flush=True)
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id!r} not found')
        if (req_status.status == requests_lib.RequestStatus.RUNNING and
                daemons.is_daemon_request_id(request_id)):
            # Daemon requests run forever, break without waiting for complete.
            break
        if req_status.status > requests_lib.RequestStatus.RUNNING:
            break
        # yield control to allow other coroutines to run, sleep shortly
        # to avoid storming the DB and CPU in the meantime
        await asyncio.sleep(0.1)
    request_task = await requests_lib.get_request_async(request_id)
    # TODO(aylei): refine this, /api/get will not be retried and this is
    # meaningless to retry. It is the original request that should be retried.
    if request_task.should_retry:
        raise fastapi.HTTPException(
            status_code=503, detail=f'Request {request_id!r} should be retried')
    request_error = request_task.get_error()
    if request_error is not None:
        raise fastapi.HTTPException(status_code=500,
                                    detail=request_task.encode().model_dump())
    return request_task.encode()


@app.get('/api/stream')
async def stream(
    request: fastapi.Request,
    request_id: Optional[str] = None,
    log_path: Optional[str] = None,
    tail: Optional[int] = None,
    follow: bool = True,
    # Choices: 'auto', 'plain', 'html', 'console'
    # 'auto': automatically choose between HTML and plain text
    #         based on the request source
    # 'plain': plain text for HTML clients
    # 'html': HTML for browsers
    # 'console': console for CLI/API clients
    # pylint: disable=redefined-builtin
    format: Literal['auto', 'plain', 'html', 'console'] = 'auto',
) -> fastapi.responses.Response:
    """Streams the logs of a request.

    When format is 'auto' and the request is coming from a browser, the response
    is a HTML page with JavaScript to handle streaming, which will request the
    API server again with format='plain' to get the actual log content.

    Args:
        request_id: Request ID to stream logs for.
        log_path: Log path to stream logs for.
        tail: Number of lines to stream from the end of the log file.
        follow: Whether to follow the log file.
        format: Response format - 'auto' (HTML for browsers, plain for HTML
            clients, console for CLI/API clients), 'plain' (force plain text),
            'html' (force HTML), or 'console' (force console)
    """
    # We need to save the user-supplied request ID for the response header.
    user_supplied_request_id = request_id
    if request_id is not None and log_path is not None:
        raise fastapi.HTTPException(
            status_code=400,
            detail='Only one of request_id and log_path can be provided')

    if request_id is not None:
        request_id = await get_expanded_request_id(request_id)

    if request_id is None and log_path is None:
        request_id = await requests_lib.get_latest_request_id_async()
        if request_id is None:
            raise fastapi.HTTPException(status_code=404,
                                        detail='No request found')

    # Determine if we should use HTML format
    if format == 'auto':
        # Check if request is coming from a browser
        user_agent = request.headers.get('user-agent', '').lower()
        use_html = any(browser in user_agent
                       for browser in ['mozilla', 'chrome', 'safari', 'edge'])
    else:
        use_html = format == 'html'

    if use_html:
        # Return HTML page with JavaScript to handle streaming
        stream_url = request.url.include_query_params(format='plain')
        html_dir = pathlib.Path(__file__).parent / 'html'
        with open(html_dir / 'log.html', 'r', encoding='utf-8') as file:
            html_content = file.read()
        return fastapi.responses.HTMLResponse(
            html_content.replace('{stream_url}', str(stream_url)),
            headers={
                'Cache-Control': 'no-cache, no-transform',
                'X-Accel-Buffering': 'no'
            })

    polling_interval = stream_utils.DEFAULT_POLL_INTERVAL
    # Original plain text streaming logic
    if request_id is not None:
        request_task = await requests_lib.get_request_async(
            request_id, fields=['request_id', 'schedule_type'])
        if request_task is None:
            print(f'No task with request ID {request_id}')
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id!r} not found')
        # req.log_path is derived from request_id,
        # so it's ok to just grab the request_id in the above query.
        log_path_to_stream = request_task.log_path
        if not log_path_to_stream.exists():
            # The log file might be deleted by the request GC daemon but the
            # request task is still in the database.
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'Log of request {request_id!r} has been deleted')
        if request_task.schedule_type == requests_lib.ScheduleType.LONG:
            polling_interval = stream_utils.LONG_REQUEST_POLL_INTERVAL
        del request_task
    else:
        assert log_path is not None, (request_id, log_path)
        if log_path == constants.API_SERVER_LOGS:
            resolved_log_path = pathlib.Path(
                constants.API_SERVER_LOGS).expanduser()
            if not resolved_log_path.exists():
                raise fastapi.HTTPException(
                    status_code=404,
                    detail='Server log file does not exist. The API server may '
                    'have been started with `--foreground` - check the '
                    'stdout of API server process, such as: '
                    '`kubectl logs -n api-server-namespace '
                    'api-server-pod-name`')
        else:
            # This should be a log path under ~/sky_logs.
            resolved_logs_directory = pathlib.Path(
                constants.SKY_LOGS_DIRECTORY).expanduser().resolve()
            resolved_log_path = resolved_logs_directory.joinpath(
                log_path).resolve()
            # Make sure the log path is under ~/sky_logs. We calculate the
            # common path to check if the log path is under ~/sky_logs.
            # This prevents path traversal using '..'
            if os.path.commonpath([resolved_log_path, resolved_logs_directory
                                  ]) != str(resolved_logs_directory):
                raise fastapi.HTTPException(
                    status_code=400,
                    detail=f'Unauthorized log path: {log_path!r}')
            elif not resolved_log_path.exists():
                raise fastapi.HTTPException(
                    status_code=404,
                    detail=f'Log path {log_path!r} does not exist')

        log_path_to_stream = resolved_log_path

    headers = {
        'Cache-Control': 'no-cache, no-transform',
        'X-Accel-Buffering': 'no',
        'Transfer-Encoding': 'chunked'
    }
    if request_id is not None:
        headers[server_constants.STREAM_REQUEST_HEADER] = (
            user_supplied_request_id
            if user_supplied_request_id else request_id)

    return fastapi.responses.StreamingResponse(
        content=stream_utils.log_streamer(request_id,
                                          log_path_to_stream,
                                          plain_logs=format == 'plain',
                                          tail=tail,
                                          follow=follow,
                                          polling_interval=polling_interval),
        media_type='text/plain',
        headers=headers,
    )


@app.post('/api/cancel')
async def api_cancel(request: fastapi.Request,
                     request_cancel_body: payloads.RequestCancelBody) -> None:
    """Cancels requests."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.API_CANCEL,
        request_body=request_cancel_body,
        func=requests_lib.kill_requests_with_prefix,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@app.get('/api/status')
async def api_status(
    request_ids: Optional[List[str]] = fastapi.Query(
        None, description='Request ID prefixes to get status for.'),
    all_status: bool = fastapi.Query(
        False, description='Get finished requests as well.'),
    limit: Optional[int] = fastapi.Query(
        None, description='Number of requests to show.'),
    fields: Optional[List[str]] = fastapi.Query(
        None, description='Fields to get. If None, get all fields.'),
) -> List[payloads.RequestPayload]:
    """Gets the list of requests."""
    if request_ids is None:
        statuses = None
        if not all_status:
            statuses = [
                requests_lib.RequestStatus.PENDING,
                requests_lib.RequestStatus.RUNNING,
            ]
        request_tasks = await requests_lib.get_request_tasks_async(
            req_filter=requests_lib.RequestTaskFilter(
                status=statuses,
                limit=limit,
                fields=fields,
                sort=True,
            ))
        return requests_lib.encode_requests(request_tasks)
    else:
        encoded_request_tasks = []
        for request_id in request_ids:
            request_tasks = await requests_lib.get_requests_async_with_prefix(
                request_id)
            if request_tasks is None:
                continue
            for request_task in request_tasks:
                encoded_request_tasks.append(request_task.readable_encode())
        return encoded_request_tasks


@app.get('/api/plugins', response_class=fastapi_responses.ORJSONResponse)
async def list_plugins() -> Dict[str, List[Dict[str, Any]]]:
    """Return metadata about loaded backend plugins."""
    plugin_infos = []
    for plugin_info in plugins.get_plugins():
        if plugin_info.hidden_from_display:
            continue
        info = {
            'js_extension_path': plugin_info.js_extension_path,
            'requires_early_init': plugin_info.requires_early_init,
        }
        for attr in ('name', 'version', 'commit'):
            value = getattr(plugin_info, attr, None)
            if value is not None:
                info[attr] = value
        plugin_infos.append(info)
    return {'plugins': plugin_infos}


@app.get(
    '/api/health',
    # response_model_exclude_unset omits unset fields
    # in the response JSON.
    response_model_exclude_unset=True)
async def health(request: fastapi.Request) -> responses.APIHealthResponse:
    """Checks the health of the API server.

    Returns:
        responses.APIHealthResponse: The health response.
    """
    user = request.state.auth_user
    server_status = common.ApiServerStatus.HEALTHY
    if getattr(request.state, 'anonymous_user', False):
        # API server authentication is enabled, but the request is not
        # authenticated. We still have to serve the request because the
        # /api/health endpoint has two different usage:
        # 1. For health check from `api start` and external ochestration
        #    tools (k8s), which does not require authentication and user info.
        # 2. Return server info to client and hint client to login if required.
        # Separating these two usage to different APIs will break backward
        # compatibility for existing ochestration solutions (e.g. helm chart).
        # So we serve these two usages in a backward compatible manner below.
        client_version = versions.get_remote_api_version()
        # - For Client with API version >= 14, we return 200 response with
        #   status=NEEDS_AUTH, new client will handle the login process.
        # - For health check from `sky api start`, the client code always uses
        #   the same API version with the server, thus there is no compatibility
        #   issue.
        server_status = common.ApiServerStatus.NEEDS_AUTH
        if client_version is None:
            # - For health check from ochestration tools (e.g. k8s), we also
            #   return 200 with status=NEEDS_AUTH, which passes HTTP probe
            #   check.
            # - There is no harm when an malicious client calls /api/health
            #   without authentication since no sensitive information is
            #   returned.
            return responses.APIHealthResponse(
                status=common.ApiServerStatus.HEALTHY,)
        # TODO(aylei): remove this after min_compatible_api_version >= 14.
        if client_version < 14:
            # For Client with API version < 14, the NEEDS_AUTH status is not
            # honored. Return 401 to trigger the login process.
            raise fastapi.HTTPException(status_code=401,
                                        detail='Authentication required')

    logger.debug(f'Health endpoint: request.state.auth_user = {user}')

    # Get latest version from cache (returns None for dev versions
    # or if not available)
    latest_version = version_check.get_latest_version_for_current()

    return responses.APIHealthResponse(
        status=server_status,
        # Kept for backward compatibility, clients before 0.11.0 will read this
        # field to check compatibility and hint the user to upgrade the CLI.
        # TODO(aylei): remove this field after 0.13.0
        api_version=str(server_constants.API_VERSION),
        version=sky.__version__,
        version_on_disk=common.get_skypilot_version_on_disk(),
        commit=sky.__commit__,
        # Whether basic auth on api server is enabled
        basic_auth_enabled=os.environ.get(constants.ENV_VAR_ENABLE_BASIC_AUTH,
                                          'false').lower() == 'true',
        user=user if user is not None else None,
        # Whether service account token is enabled
        service_account_token_enabled=(os.environ.get(
            constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS,
            'false').lower() == 'true'),
        # Whether basic auth on ingress is enabled
        ingress_basic_auth_enabled=os.environ.get(
            constants.SKYPILOT_INGRESS_BASIC_AUTH_ENABLED,
            'false').lower() == 'true',
        # Latest version info (if available and newer than current)
        latest_version=latest_version,
    )


class SSHMessageType(IntEnum):
    REGULAR_DATA = 0
    PINGPONG = 1
    LATENCY_MEASUREMENT = 2


async def _get_cluster_and_validate(
    cluster_name: str,
    cloud_type: Type[clouds.Cloud],
) -> 'backends.CloudVmRayResourceHandle':
    """Fetch cluster status and validate it's UP and correct cloud type."""
    # Run core.status in another thread to avoid blocking the event loop.
    # TODO(aylei): core.status() will be called with server user, which has
    # permission to all workspaces, this will break workspace isolation.
    # It is ok for now, as users with limited access will not get the ssh config
    # for the clusters in non-accessible workspaces.
    with ThreadPoolExecutor(max_workers=1) as thread_pool_executor:
        cluster_records = await context_utils.to_thread_with_executor(
            thread_pool_executor, core.status, cluster_name, all_users=True)
    cluster_record = cluster_records[0]
    if cluster_record['status'] not in (status_lib.ClusterStatus.INIT,
                                        status_lib.ClusterStatus.UP,
                                        status_lib.ClusterStatus.AUTOSTOPPING):
        raise fastapi.HTTPException(
            status_code=400, detail=f'Cluster {cluster_name} is not running')

    handle: Optional['backends.CloudVmRayResourceHandle'] = cluster_record[
        'handle']
    assert handle is not None, 'Cluster handle is None'
    if not isinstance(handle.launched_resources.cloud, cloud_type):
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Cluster {cluster_name} is not a {str(cloud_type())} '
            'cluster. Use ssh to connect to the cluster instead.')

    return handle


async def _run_websocket_proxy(
    websocket: fastapi.WebSocket,
    read_from_backend: Callable[[], Awaitable[bytes]],
    write_to_backend: Callable[[bytes], Awaitable[None]],
    close_backend: Callable[[], Awaitable[None]],
    timestamps_supported: bool,
) -> bool:
    """Run bidirectional WebSocket-to-backend proxy.

    Args:
        websocket: FastAPI WebSocket connection
        read_from_backend: Async callable to read bytes from backend
        write_to_backend: Async callable to write bytes to backend
        close_backend: Async callable to close backend connection
        timestamps_supported: Whether to use message type framing

    Returns:
        True if SSH failed, False otherwise
    """
    ssh_failed = False
    websocket_closed = False

    async def websocket_to_backend():
        try:
            async for message in websocket.iter_bytes():
                if timestamps_supported:
                    type_size = struct.calcsize('!B')
                    message_type = struct.unpack('!B', message[:type_size])[0]
                    if message_type == SSHMessageType.REGULAR_DATA:
                        # Regular data - strip type byte and forward to backend
                        message = message[type_size:]
                    elif message_type == SSHMessageType.PINGPONG:
                        # PING message - respond with PONG
                        ping_id_size = struct.calcsize('!I')
                        if len(message) != type_size + ping_id_size:
                            raise ValueError(
                                f'Invalid PING message length: {len(message)}')
                        # Return the same PING message for latency measurement
                        await websocket.send_bytes(message)
                        continue
                    elif message_type == SSHMessageType.LATENCY_MEASUREMENT:
                        # Latency measurement from client
                        latency_size = struct.calcsize('!Q')
                        if len(message) != type_size + latency_size:
                            raise ValueError('Invalid latency measurement '
                                             f'message length: {len(message)}')
                        avg_latency_ms = struct.unpack(
                            '!Q',
                            message[type_size:type_size + latency_size])[0]
                        latency_seconds = avg_latency_ms / 1000
                        metrics_utils.SKY_APISERVER_WEBSOCKET_SSH_LATENCY_SECONDS.labels(  # pylint: disable=line-too-long
                            pid=os.getpid()).observe(latency_seconds)
                        continue
                    else:
                        raise ValueError(
                            f'Unknown message type: {message_type}')

                try:
                    await write_to_backend(message)
                except Exception as e:  # pylint: disable=broad-except
                    # Typically we will not reach here, if the conn to backend
                    # is disconnected, backend_to_websocket will exit first.
                    # But just in case.
                    logger.error(f'Failed to write to backend through '
                                 f'connection: {e}')
                    nonlocal ssh_failed
                    ssh_failed = True
                    break
        except fastapi.WebSocketDisconnect:
            pass
        nonlocal websocket_closed
        websocket_closed = True
        await close_backend()

    async def backend_to_websocket():
        try:
            while True:
                data = await read_from_backend()
                if not data:
                    if not websocket_closed:
                        logger.warning(
                            'SSH connection to backend is disconnected '
                            'before websocket connection is closed')
                        nonlocal ssh_failed
                        ssh_failed = True
                    break
                if timestamps_supported:
                    # Prepend message type byte (0 = regular data)
                    message_type_bytes = struct.pack(
                        '!B', SSHMessageType.REGULAR_DATA.value)
                    data = message_type_bytes + data
                await websocket.send_bytes(data)
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            await websocket.close()
        except Exception:  # pylint: disable=broad-except
            # The websocket might have been closed by the client
            pass

    await asyncio.gather(websocket_to_backend(),
                         backend_to_websocket(),
                         return_exceptions=True)

    return ssh_failed


@app.websocket('/kubernetes-pod-ssh-proxy')
async def kubernetes_pod_ssh_proxy(
        websocket: fastapi.WebSocket,
        cluster_name: str,
        client_version: Optional[int] = None) -> None:
    """Proxies SSH to the Kubernetes pod with websocket."""
    await websocket.accept()
    logger.info(f'WebSocket connection accepted for cluster: {cluster_name}')

    timestamps_supported = client_version is not None and client_version > 21
    logger.info(f'Websocket timestamps supported: {timestamps_supported}, \
        client_version = {client_version}')

    handle = await _get_cluster_and_validate(cluster_name, clouds.Kubernetes)

    kubectl_cmd = handle.get_command_runners()[0].port_forward_command(
        port_forward=[(None, 22)])
    proc = await asyncio.create_subprocess_exec(
        *kubectl_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
    logger.info(f'Started kubectl port-forward with command: {kubectl_cmd}')

    # Wait for port-forward to be ready and get the local port
    local_port = None
    assert proc.stdout is not None
    while True:
        stdout_line = await proc.stdout.readline()
        if stdout_line:
            decoded_line = stdout_line.decode()
            logger.info(f'kubectl port-forward stdout: {decoded_line}')
            if 'Forwarding from 127.0.0.1' in decoded_line:
                port_str = decoded_line.split(':')[-1]
                local_port = int(port_str.replace(' -> ', ':').split(':')[0])
                break
        else:
            await websocket.close()
            return

    logger.info(f'Starting port-forward to local port: {local_port}')
    conn_gauge = metrics_utils.SKY_APISERVER_WEBSOCKET_CONNECTIONS.labels(
        pid=os.getpid())
    ssh_failed = False
    try:
        conn_gauge.inc()
        # Connect to the local port
        reader, writer = await asyncio.open_connection('127.0.0.1', local_port)

        async def write_and_drain(data: bytes) -> None:
            writer.write(data)
            await writer.drain()

        async def close_writer() -> None:
            writer.close()

        ssh_failed = await _run_websocket_proxy(
            websocket,
            read_from_backend=lambda: reader.read(1024),
            write_to_backend=write_and_drain,
            close_backend=close_writer,
            timestamps_supported=timestamps_supported,
        )
    finally:
        conn_gauge.dec()
        reason = ''
        try:
            logger.info('Terminating kubectl port-forward process')
            proc.terminate()
        except ProcessLookupError:
            stdout = await proc.stdout.read()
            logger.error('kubectl port-forward was terminated before the '
                         'ssh websocket connection was closed. Remaining '
                         f'output: {str(stdout)}')
            reason = 'KubectlPortForwardExit'
            metrics_utils.SKY_APISERVER_WEBSOCKET_CLOSED_TOTAL.labels(
                pid=os.getpid(), reason=reason).inc()
        else:
            if ssh_failed:
                reason = 'SSHToPodDisconnected'
            else:
                reason = 'ClientClosed'
        metrics_utils.SKY_APISERVER_WEBSOCKET_CLOSED_TOTAL.labels(
            pid=os.getpid(), reason=reason).inc()


@app.websocket('/slurm-job-ssh-proxy')
async def slurm_job_ssh_proxy(websocket: fastapi.WebSocket,
                              cluster_name: str,
                              worker: int = 0,
                              client_version: Optional[int] = None) -> None:
    """Proxies SSH to the Slurm job via sshd inside srun."""
    await websocket.accept()
    logger.info(f'WebSocket connection accepted for cluster: '
                f'{cluster_name}')

    timestamps_supported = client_version is not None and client_version > 21
    logger.info(f'Websocket timestamps supported: {timestamps_supported}, \
        client_version = {client_version}')

    handle = await _get_cluster_and_validate(cluster_name, clouds.Slurm)

    assert handle.cached_cluster_info is not None, 'Cached cluster info is None'
    provider_config = handle.cached_cluster_info.provider_config
    assert provider_config is not None, 'Provider config is None'
    login_node_ssh_config = provider_config['ssh']
    login_node_host = login_node_ssh_config['hostname']
    login_node_port = int(login_node_ssh_config['port'])
    login_node_user = login_node_ssh_config['user']
    login_node_key = login_node_ssh_config.get('private_key', None)
    login_node_proxy_command = login_node_ssh_config.get('proxycommand', None)
    login_node_proxy_jump = login_node_ssh_config.get('proxyjump', None)

    login_node_runner = command_runner.SSHCommandRunner(
        (login_node_host, login_node_port),
        login_node_user,
        login_node_key,
        ssh_proxy_command=login_node_proxy_command,
        ssh_proxy_jump=login_node_proxy_jump,
    )

    ssh_cmd = login_node_runner.ssh_base_command(
        ssh_mode=command_runner.SshMode.NON_INTERACTIVE,
        port_forward=None,
        connect_timeout=None)

    # There can only be one InstanceInfo per instance_id.
    head_instance = handle.cached_cluster_info.get_head_instance()
    assert head_instance is not None, 'Head instance is None'
    job_id = head_instance.tags['job_id']

    # Instances are ordered: head first, then workers
    instances = handle.cached_cluster_info.instances
    node_hostnames = [inst[0].tags['node'] for inst in instances.values()]
    if worker >= len(node_hostnames):
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Worker index {worker} out of range. '
            f'Cluster has {len(node_hostnames)} nodes.')
    target_node = node_hostnames[worker]

    # Run sshd inside the Slurm job "container" via srun, such that it inherits
    # the resource constraints of the Slurm job.
    is_container_image = handle.launched_resources.extract_docker_image(
    ) is not None
    ssh_cmd += [
        shlex.quote(
            slurm_utils.srun_sshd_command(
                job_id,
                target_node,
                login_node_user,
                handle.cluster_name_on_cloud,
                is_container_image,
            ))
    ]

    proc = await asyncio.create_subprocess_shell(
        ' '.join(ssh_cmd),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,  # Capture stderr separately for logging
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    stdin = proc.stdin
    stdout = proc.stdout
    stderr = proc.stderr

    async def log_stderr():
        while True:
            line = await stderr.readline()
            if not line:
                break
            logger.debug(f'srun stderr: {line.decode().rstrip()}')

    stderr_task = None
    if env_options.Options.SHOW_DEBUG_INFO.get():
        stderr_task = asyncio.create_task(log_stderr())
    conn_gauge = metrics_utils.SKY_APISERVER_WEBSOCKET_CONNECTIONS.labels(
        pid=os.getpid())
    ssh_failed = False
    try:
        conn_gauge.inc()

        async def write_and_drain(data: bytes) -> None:
            stdin.write(data)
            await stdin.drain()

        async def close_stdin() -> None:
            stdin.close()

        ssh_failed = await _run_websocket_proxy(
            websocket,
            read_from_backend=lambda: stdout.read(4096),
            write_to_backend=write_and_drain,
            close_backend=close_stdin,
            timestamps_supported=timestamps_supported,
        )

    finally:
        conn_gauge.dec()
        reason = ''
        try:
            logger.info('Terminating srun process')
            proc.terminate()
        except ProcessLookupError:
            stdout_data = await stdout.read()
            logger.error('srun process was terminated before the '
                         'ssh websocket connection was closed. Remaining '
                         f'output: {str(stdout_data)}')
            reason = 'SrunProcessExit'
            metrics_utils.SKY_APISERVER_WEBSOCKET_CLOSED_TOTAL.labels(
                pid=os.getpid(), reason=reason).inc()
        else:
            if ssh_failed:
                reason = 'SSHToSlurmJobDisconnected'
            else:
                reason = 'ClientClosed'

        metrics_utils.SKY_APISERVER_WEBSOCKET_CLOSED_TOTAL.labels(
            pid=os.getpid(), reason=reason).inc()

        # Cancel the stderr logging task if it's still running
        if stderr_task is not None and not stderr_task.done():
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass


@app.websocket('/ssh-interactive-auth')
async def ssh_interactive_auth(websocket: fastapi.WebSocket,
                               session_id: str) -> None:
    """Proxies PTY for SSH interactive authentication via websocket.

    This endpoint receives a PTY file descriptor from a worker process
    and bridges it bidirectionally with a websocket connection, allowing
    the client to handle interactive SSH authentication (e.g., 2FA).

    Detects auth completion by monitoring terminal echo state and data flow.
    """
    await websocket.accept()
    logger.info(f'WebSocket connection accepted for SSH auth session: '
                f'{session_id}')

    loop = asyncio.get_running_loop()

    # Connect to worker process to receive PTY file descriptor
    fd_socket_path = interactive_utils.get_pty_socket_path(session_id)
    fd_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    master_fd = -1
    try:
        # Connect to worker's FD-passing socket
        await loop.sock_connect(fd_sock, fd_socket_path)
        master_fd = await loop.run_in_executor(None, interactive_utils.recv_fd,
                                               fd_sock)
        logger.debug(f'Received PTY master fd {master_fd} for session '
                     f'{session_id}')

        # Bridge PTY ↔ websocket bidirectionally
        async def websocket_to_pty():
            """Forward websocket messages to PTY."""
            try:
                async for message in websocket.iter_bytes():
                    await loop.run_in_executor(None, os.write, master_fd,
                                               message)
            except fastapi.WebSocketDisconnect:
                logger.debug(f'WebSocket disconnected for session {session_id}')
            except asyncio.CancelledError:
                pass
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error in websocket_to_pty: {e}')

        async def pty_to_websocket():
            """Forward PTY output to websocket and detect auth completion.

            Detects auth completion by monitoring terminal echo state.
            Echo is disabled during password prompts and enabled after
            successful authentication. Auth is considered complete when
            echo has been enabled for a sustained period (1s).
            """
            try:
                while True:
                    try:
                        data = await loop.run_in_executor(
                            None, os.read, master_fd, 4096)
                    except OSError as e:
                        logger.error(f'PTY read error (likely closed): {e}')
                        break

                    if not data:
                        break

                    await websocket.send_bytes(data)
            except asyncio.CancelledError:
                pass
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error in pty_to_websocket: {e}')
            finally:
                try:
                    await websocket.close()
                except Exception:  # pylint: disable=broad-except
                    pass

        await asyncio.gather(websocket_to_pty(), pty_to_websocket())

    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Error in SSH interactive auth websocket: {e}')
        raise
    finally:
        # Clean up
        if master_fd >= 0:
            try:
                os.close(master_fd)
            except OSError:
                pass
        fd_sock.close()
        logger.debug(f'SSH interactive auth session {session_id} completed')


@app.get('/all_contexts')
async def all_contexts(request: fastapi.Request) -> None:
    """Gets all Kubernetes and SSH node pool contexts."""

    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.ALL_CONTEXTS,
        request_body=payloads.RequestBody(),
        func=core.get_all_contexts,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


# === Internal APIs ===
@app.get('/api/completion/cluster_name')
async def complete_cluster_name(incomplete: str,) -> List[str]:
    return await asyncio.to_thread(
        global_user_state.get_cluster_names_start_with, incomplete)


@app.get('/api/completion/storage_name')
async def complete_storage_name(incomplete: str,) -> List[str]:
    return await asyncio.to_thread(
        global_user_state.get_storage_names_start_with, incomplete)


@app.get('/api/completion/volume_name')
async def complete_volume_name(incomplete: str,) -> List[str]:
    return await asyncio.to_thread(
        global_user_state.get_volume_names_start_with, incomplete)


@app.get('/api/completion/api_request')
async def complete_api_request(incomplete: str,) -> List[str]:
    return await requests_lib.get_api_request_ids_start_with(incomplete)


@app.get('/dashboard/{full_path:path}')
async def serve_dashboard(full_path: str):
    """Serves the Next.js dashboard application.

    Args:
        full_path: The path requested by the client.
        e.g. /clusters, /jobs

    Returns:
        FileResponse for static files or index.html for client-side routing.

    Raises:
        HTTPException: If the path is invalid or file not found.
    """
    # Try to serve the staticfile directly e.g. /skypilot.svg,
    # /favicon.ico, and /_next/, etc.
    file_path = os.path.join(server_constants.DASHBOARD_DIR, full_path)
    if os.path.isfile(file_path):
        return fastapi.responses.FileResponse(file_path)

    # Serve plugin catch-all page for any /plugins/* paths so client-side
    # routing can bootstrap correctly.
    if full_path == 'plugins' or full_path.startswith('plugins/'):
        plugin_catchall = os.path.join(server_constants.DASHBOARD_DIR,
                                       'plugins', '[...slug].html')
        if os.path.isfile(plugin_catchall):
            return fastapi.responses.FileResponse(plugin_catchall)

    # Serve recipe detail page for any /recipes/* paths (dynamic route)
    if full_path.startswith('recipes/') and full_path != 'recipes/':
        recipe_page = os.path.join(server_constants.DASHBOARD_DIR, 'recipes',
                                   '[recipe].html')
        if os.path.isfile(recipe_page):
            return fastapi.responses.FileResponse(recipe_page)

    # Serve index.html for client-side routing
    # e.g. /clusters, /jobs
    index_path = os.path.join(server_constants.DASHBOARD_DIR, 'index.html')
    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            content = f.read()

        return fastapi.responses.HTMLResponse(content=content)
    except Exception as e:
        logger.error(f'Error serving dashboard: {e}')
        raise fastapi.HTTPException(status_code=500, detail=str(e))


# Redirect the root path to dashboard
@app.get('/')
async def root():
    return fastapi.responses.RedirectResponse(url='/dashboard/')


def _init_or_restore_server_user_hash():
    """Restores the server user hash from the global user state db.

    The API server must have a stable user hash across restarts and potential
    multiple replicas. Thus we persist the user hash in db and restore it on
    startup. When upgrading from old version, the user hash will be read from
    the local file (if any) to keep the user hash consistent.
    """

    def apply_user_hash(user_hash: str) -> None:
        # For local API server, the user hash in db and local file should be
        # same so there is no harm to override here.
        common_utils.set_user_hash_locally(user_hash)
        # Refresh the server user hash for current process after restore or
        # initialize the user hash in db, child processes will get the correct
        # server id from the local cache file.
        common_lib.refresh_server_id()

    user_hash = global_user_state.get_system_config(_SERVER_USER_HASH_KEY)
    if user_hash is not None:
        apply_user_hash(user_hash)
        return

    # Initial deployment, generate a user hash and save it to the db.
    user_hash = common_utils.get_user_hash()
    global_user_state.set_system_config(_SERVER_USER_HASH_KEY, user_hash)
    apply_user_hash(user_hash)


if __name__ == '__main__':
    import uvicorn

    from sky.server import uvicorn as skyuvicorn

    logger.info('Initializing SkyPilot API server')
    skyuvicorn.add_timestamp_prefix_for_server_logs()

    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=46580, type=int)
    parser.add_argument('--deploy', action='store_true')
    # Serve metrics on a separate port to isolate it from the application APIs:
    # metrics port will not be exposed to the public network typically.
    parser.add_argument('--metrics-port', default=9090, type=int)
    cmd_args = parser.parse_args()
    if cmd_args.port == cmd_args.metrics_port:
        logger.error('port and metrics-port cannot be the same, exiting.')
        raise ValueError('port and metrics-port cannot be the same')

    # Fail fast if the port is not available to avoid corrupt the state
    # of potential running server instance.
    # We might reach here because the running server is currently not
    # responding, thus the healthz check fails and `sky api start` think
    # we should start a new server instance.
    if not common_utils.is_port_available(cmd_args.port):
        logger.error(f'Port {cmd_args.port} is not available, exiting.')
        raise RuntimeError(f'Port {cmd_args.port} is not available')

    # Maybe touch the signal file on API server startup. Do it again here even
    # if we already touched it in the sky/server/common.py::_start_api_server.
    # This is because the sky/server/common.py::_start_api_server function call
    # is running outside the skypilot API server process tree. The process tree
    # starts within that function (see the `subprocess.Popen` call in
    # sky/server/common.py::_start_api_server). When pg is used, the
    # _start_api_server function will not load the config file from db, which
    # will ignore the consolidation mode config. Here, inside the process tree,
    # we already reload the config as a server (with env var _start_api_server),
    # so we will respect the consolidation mode config.
    # Refers to #7717 for more details.
    managed_job_utils.is_consolidation_mode(on_api_restart=True)

    # Show the privacy policy if it is not already shown. We place it here so
    # that it is shown only when the API server is started.
    usage_lib.maybe_show_privacy_policy()

    # Initialize global user state db
    db_utils.set_max_connections(1)
    logger.info('Initializing database engine')
    global_user_state.initialize_and_get_db()
    logger.info('Database engine initialized')
    # Initialize request db
    requests_lib.reset_db_and_logs()
    # Restore the server user hash
    logger.info('Initializing server user hash')
    _init_or_restore_server_user_hash()
    # Pre-load plugin RBAC rules before initializing permission service.
    # This ensures plugin RBAC rules are available when policies are created.
    logger.info('Pre-loading plugin RBAC rules')
    plugins.load_plugin_rbac_rules()
    logger.info('Initializing permission service')
    permission.permission_service.initialize()
    logger.info('Permission service initialized')

    max_db_connections = global_user_state.get_max_db_connections()
    logger.info(f'Max db connections: {max_db_connections}')

    # Reserve memory for jobs and serve/pool controller in consolidation mode.
    reserved_memory_mb = (
        controller_utils.compute_memory_reserved_for_controllers(
            reserve_for_controllers=os.environ.get(
                constants.OVERRIDE_CONSOLIDATION_MODE) is not None,
            # For jobs controller, we need to reserve for both jobs and
            # pool controller.
            reserve_extra_for_pool=not os.environ.get(
                constants.IS_SKYPILOT_SERVE_CONTROLLER)))

    config = server_config.compute_server_config(
        cmd_args.deploy,
        max_db_connections,
        reserved_memory_mb=reserved_memory_mb)

    num_workers = config.num_server_workers

    queue_server: Optional[multiprocessing.Process] = None
    workers: List[executor.RequestWorker] = []
    # Global background tasks that will be scheduled in a separate event loop.
    global_tasks: List[asyncio.Task] = []
    try:
        background = uvloop.new_event_loop()
        if os.environ.get(constants.ENV_VAR_SERVER_METRICS_ENABLED):
            metrics_server = metrics.build_metrics_server(
                cmd_args.host, cmd_args.metrics_port)
            global_tasks.append(background.create_task(metrics_server.serve()))
        global_tasks.append(
            background.create_task(requests_lib.requests_gc_daemon()))
        global_tasks.append(
            background.create_task(
                global_user_state.cluster_event_retention_daemon()))
        global_tasks.append(
            background.create_task(
                managed_job_state.job_event_retention_daemon()))
        threading.Thread(target=background.run_forever, daemon=True).start()

        queue_server, workers = executor.start(config)

        logger.info(f'Starting SkyPilot API server, workers={num_workers}')
        # We don't support reload for now, since it may cause leakage of request
        # workers or interrupt running requests.
        uvicorn_config = uvicorn.Config('sky.server.server:app',
                                        host=cmd_args.host,
                                        port=cmd_args.port,
                                        workers=num_workers,
                                        ws_per_message_deflate=False)
        skyuvicorn.run(uvicorn_config,
                       max_db_connections=config.num_db_connections_per_worker)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(f'Failed to start SkyPilot API server: '
                     f'{common_utils.format_exception(exc, use_bracket=True)}')
        raise
    finally:
        logger.info('Shutting down SkyPilot API server...')

        for gt in global_tasks:
            gt.cancel()
        for plugin in plugins.get_plugins():
            plugin.shutdown()
        subprocess_utils.run_in_parallel(lambda worker: worker.cancel(),
                                         workers,
                                         num_threads=len(workers))
        if queue_server is not None:
            queue_server.kill()
            queue_server.join()
