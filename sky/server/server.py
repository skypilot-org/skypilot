"""SkyPilot API Server exposing RESTful APIs."""

import argparse
import asyncio
import base64
import contextlib
import dataclasses
import datetime
import hashlib
import json
import logging
import multiprocessing
import os
import pathlib
import posixpath
import re
import shutil
import sys
import threading
from typing import Any, Dict, List, Literal, Optional, Set, Tuple
import uuid
import zipfile

import aiofiles
import fastapi
from fastapi.middleware import cors
from passlib.hash import apr_md5_crypt
import starlette.middleware.base

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
from sky.jobs.server import server as jobs_rest
from sky.metrics import utils as metrics_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.serve.server import server as serve_rest
from sky.server import common
from sky.server import config as server_config
from sky.server import constants as server_constants
from sky.server import metrics
from sky.server import state
from sky.server import stream_utils
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import preconditions
from sky.server.requests import requests as requests_lib
from sky.skylet import constants
from sky.ssh_node_pools import server as ssh_node_pools_rest
from sky.usage import usage_lib
from sky.users import permission
from sky.users import server as users_rest
from sky.utils import admin_policy_utils
from sky.utils import common as common_lib
from sky.utils import common_utils
from sky.utils import context
from sky.utils import context_utils
from sky.utils import dag_utils
from sky.utils import env_options
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.volumes.server import server as volumes_rest
from sky.workspaces import server as workspaces_rest

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

P = ParamSpec('P')


def _add_timestamp_prefix_for_server_logs() -> None:
    server_logger = sky_logging.init_logger('sky.server')
    # Clear existing handlers first to prevent duplicates
    server_logger.handlers.clear()
    # Disable propagation to avoid the root logger of SkyPilot being affected
    server_logger.propagate = False
    # Add date prefix to the log message printed by loggers under
    # server.
    stream_handler = logging.StreamHandler(sys.stdout)
    if env_options.Options.SHOW_DEBUG_INFO.get():
        stream_handler.setLevel(logging.DEBUG)
    else:
        stream_handler.setLevel(logging.INFO)
    stream_handler.flush = sys.stdout.flush  # type: ignore
    stream_handler.setFormatter(sky_logging.FORMATTER)
    server_logger.addHandler(stream_handler)
    # Add date prefix to the log message printed by uvicorn.
    for name in ['uvicorn', 'uvicorn.access']:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.addHandler(stream_handler)


_add_timestamp_prefix_for_server_logs()
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


# TODO(hailong): Remove this function and use request.state.auth_user instead.
async def _override_user_info_in_request_body(request: fastapi.Request,
                                              auth_user: Optional[models.User]):
    if auth_user is None:
        return

    body = await request.body()
    if body:
        try:
            original_json = await request.json()
        except json.JSONDecodeError as e:
            logger.error(f'Error parsing request JSON: {e}')
        else:
            logger.debug(f'Overriding user for {request.state.request_id}: '
                         f'{auth_user.name}, {auth_user.id}')
            if 'env_vars' in original_json:
                if isinstance(original_json.get('env_vars'), dict):
                    original_json['env_vars'][
                        constants.USER_ID_ENV_VAR] = auth_user.id
                    original_json['env_vars'][
                        constants.USER_ENV_VAR] = auth_user.name
                else:
                    logger.warning(
                        f'"env_vars" in request body is not a dictionary '
                        f'for request {request.state.request_id}. '
                        'Skipping user info injection into body.')
            else:
                original_json['env_vars'] = {}
                original_json['env_vars'][
                    constants.USER_ID_ENV_VAR] = auth_user.id
                original_json['env_vars'][
                    constants.USER_ENV_VAR] = auth_user.name
            request._body = json.dumps(original_json).encode('utf-8')  # pylint: disable=protected-access


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
                apr_md5_crypt.verify(password, user.password)):
            request.state.auth_user = user
            break


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
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        # TODO(syang): remove X-Request-ID when v0.10.0 is released.
        response.headers['X-Request-ID'] = request_id
        response.headers['X-Skypilot-Request-ID'] = request_id
        return response


def _get_auth_user_header(request: fastapi.Request) -> Optional[models.User]:
    header_name = os.environ.get(constants.ENV_VAR_SERVER_AUTH_USER_HEADER,
                                 'X-Auth-Request-Email')
    if header_name not in request.headers:
        return None
    user_name = request.headers[header_name]
    user_hash = hashlib.md5(
        user_name.encode()).hexdigest()[:common_utils.USER_HASH_LENGTH]
    return models.User(id=user_hash, name=user_name)


class InitializeRequestAuthUserMiddleware(
        starlette.middleware.base.BaseHTTPMiddleware):

    async def dispatch(self, request: fastapi.Request, call_next):
        # Make sure that request.state.auth_user is set. Otherwise, we may get a
        # KeyError while trying to read it.
        request.state.auth_user = None
        return await call_next(request)


class BasicAuthMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to handle HTTP Basic Auth."""

    async def dispatch(self, request: fastapi.Request, call_next):
        if request.url.path.startswith('/api/'):
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
                    apr_md5_crypt.verify(password, user.password)):
                valid_user = True
                request.state.auth_user = user
                await _override_user_info_in_request_body(request, user)
                break
        if not valid_user:
            return _basic_auth_401_response('Invalid credentials')

        return await call_next(request)


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

            # Override user info in request body for service account requests
            await _override_user_info_in_request_body(request, auth_user)

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


class AuthProxyMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to handle auth proxy."""

    async def dispatch(self, request: fastapi.Request, call_next):
        auth_user = _get_auth_user_header(request)

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

        await _override_user_info_in_request_body(request, auth_user)
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


@contextlib.asynccontextmanager
async def lifespan(app: fastapi.FastAPI):  # pylint: disable=redefined-outer-name
    """FastAPI lifespan context manager."""
    del app  # unused
    # Startup: Run background tasks
    for event in requests_lib.INTERNAL_REQUEST_DAEMONS:
        try:
            executor.schedule_request(
                request_id=event.id,
                request_name=event.name,
                request_body=payloads.RequestBody(),
                func=event.run_event,
                schedule_type=requests_lib.ScheduleType.SHORT,
                is_skypilot_system=True,
            )
        except exceptions.RequestAlreadyExistsError:
            # Lifespan will be executed in each uvicorn worker process, we
            # can safely ignore the error if the task is already scheduled.
            logger.debug(f'Request {event.id} already exists.')
    asyncio.create_task(cleanup_upload_ids())
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
    # TODO(syang): remove X-Request-ID \when v0.10.0 is released.
    expose_headers=['X-Request-ID', 'X-Skypilot-Request-ID'])
# The order of all the authentication-related middleware is important.
# RBACMiddleware must precede all the auth middleware, so it can access
# request.state.auth_user.
app.add_middleware(RBACMiddleware)
# AuthProxyMiddleware should precede BasicAuthMiddleware and
# BearerTokenMiddleware, since it should be skipped if either of those set the
# auth user.
app.add_middleware(AuthProxyMiddleware)
enable_basic_auth = os.environ.get(constants.ENV_VAR_ENABLE_BASIC_AUTH, 'false')
if str(enable_basic_auth).lower() == 'true':
    app.add_middleware(BasicAuthMiddleware)
# Bearer token middleware should always be present to handle service account
# authentication
app.add_middleware(BearerTokenMiddleware)
# InitializeRequestAuthUserMiddleware must be the last added middleware so that
# request.state.auth_user is always set, but can be overridden by the auth
# middleware above.
app.add_middleware(InitializeRequestAuthUserMiddleware)
app.add_middleware(RequestIDMiddleware)
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


@app.get('/token')
async def token(request: fastapi.Request,
                local_port: Optional[int] = None) -> fastapi.responses.Response:
    del local_port  # local_port is used by the served js, but ignored by server
    user = _get_auth_user_header(request)

    token_data = {
        'v': 1,  # Token version number, bump for backwards incompatible.
        'user': user.id if user is not None else None,
        'cookies': request.cookies,
    }
    # Use base64 encoding to avoid having to escape anything in the HTML.
    json_bytes = json.dumps(token_data).encode('utf-8')
    base64_str = base64.b64encode(json_bytes).decode('utf-8')

    html_dir = pathlib.Path(__file__).parent / 'html'
    token_page_path = html_dir / 'token_page.html'
    try:
        with open(token_page_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except FileNotFoundError as e:
        raise fastapi.HTTPException(
            status_code=500, detail='Token page template not found.') from e

    user_info_string = f'Logged in as {user.name}' if user is not None else ''
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


@app.post('/check')
async def check(request: fastapi.Request,
                check_body: payloads.CheckBody) -> None:
    """Checks enabled clouds."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='check',
        request_body=check_body,
        func=sky_check.check,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@app.get('/enabled_clouds')
async def enabled_clouds(request: fastapi.Request,
                         workspace: Optional[str] = None,
                         expand: bool = False) -> None:
    """Gets enabled clouds on the server."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='enabled_clouds',
        request_body=payloads.EnabledCloudsBody(workspace=workspace,
                                                expand=expand),
        func=core.enabled_clouds,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@app.post('/realtime_kubernetes_gpu_availability')
async def realtime_kubernetes_gpu_availability(
    request: fastapi.Request,
    realtime_gpu_availability_body: payloads.RealtimeGpuAvailabilityRequestBody
) -> None:
    """Gets real-time Kubernetes GPU availability."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='realtime_kubernetes_gpu_availability',
        request_body=realtime_gpu_availability_body,
        func=core.realtime_kubernetes_gpu_availability,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@app.post('/kubernetes_node_info')
async def kubernetes_node_info(
        request: fastapi.Request,
        kubernetes_node_info_body: payloads.KubernetesNodeInfoRequestBody
) -> None:
    """Gets Kubernetes nodes information and hints."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='kubernetes_node_info',
        request_body=kubernetes_node_info_body,
        func=kubernetes_utils.get_kubernetes_node_info,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@app.get('/status_kubernetes')
async def status_kubernetes(request: fastapi.Request) -> None:
    """Gets Kubernetes status."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='status_kubernetes',
        request_body=payloads.RequestBody(),
        func=core.status_kubernetes,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@app.post('/list_accelerators')
async def list_accelerators(
        request: fastapi.Request,
        list_accelerator_counts_body: payloads.ListAcceleratorsBody) -> None:
    """Gets list of accelerators from cloud catalog."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='list_accelerators',
        request_body=list_accelerator_counts_body,
        func=catalog.list_accelerators,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@app.post('/list_accelerator_counts')
async def list_accelerator_counts(
        request: fastapi.Request,
        list_accelerator_counts_body: payloads.ListAcceleratorCountsBody
) -> None:
    """Gets list of accelerator counts from cloud catalog."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='list_accelerator_counts',
        request_body=list_accelerator_counts_body,
        func=catalog.list_accelerator_counts,
        schedule_type=requests_lib.ScheduleType.SHORT,
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
        # Resolve the volumes before admin policy and validation.
        dag.resolve_and_validate_volumes()
        # TODO: Admin policy may contain arbitrary code, which may be expensive
        # to run and may block the server thread. However, moving it into the
        # executor adds a ~150ms penalty on the local API server because of
        # added RTTs. For now, we stick to doing the validation inline in the
        # server thread.
        with admin_policy_utils.apply_and_use_config_in_current_request(
                dag, request_options=validate_body.request_options) as dag:
            # Skip validating workdir and file_mounts, as those need to be
            # validated after the files are uploaded to the SkyPilot API server
            # with `upload_mounts_to_api_server`.
            dag.validate(skip_file_mounts=True, skip_workdir=True)

    try:
        dag = dag_utils.load_chain_dag_from_yaml_str(validate_body.dag)
        # Apply admin policy and validate DAG is blocking, run it in a separate
        # thread executor to avoid blocking the uvicorn event loop.
        await context_utils.to_thread(validate_dag, dag)
    except Exception as e:  # pylint: disable=broad-except
        raise fastapi.HTTPException(
            status_code=400, detail=exceptions.serialize_exception(e)) from e


@app.post('/optimize')
async def optimize(optimize_body: payloads.OptimizeBody,
                   request: fastapi.Request) -> None:
    """Optimizes the user's DAG."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='optimize',
        request_body=optimize_body,
        ignore_return_value=True,
        func=core.optimize,
        schedule_type=requests_lib.ScheduleType.SHORT,
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
    # Add the upload id to the cleanup list.
    upload_ids_to_cleanup[(upload_id,
                           user_hash)] = (datetime.datetime.now() +
                                          _DEFAULT_UPLOAD_EXPIRATION_TIME)

    # TODO(SKY-1271): We need to double check security of uploading zip file.
    client_file_mounts_dir = (
        common.API_SERVER_CLIENT_DIR.expanduser().resolve() / user_hash /
        'file_mounts')
    client_file_mounts_dir.mkdir(parents=True, exist_ok=True)

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
        chunk_dir.mkdir(parents=True, exist_ok=True)
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
            return payloads.UploadZipFileResponse(status='uploading',
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
    unzip_file(zip_file_path, client_file_mounts_dir)
    if total_chunks > 1:
        shutil.rmtree(chunk_dir)
    return payloads.UploadZipFileResponse(status='completed')


def _is_relative_to(path: pathlib.Path, parent: pathlib.Path) -> bool:
    """Checks if path is a subpath of parent."""
    try:
        # We cannot use is_relative_to, as it is only added after 3.9.
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def unzip_file(zip_file_path: pathlib.Path,
               client_file_mounts_dir: pathlib.Path) -> None:
    """Unzips a zip file."""
    try:
        with zipfile.ZipFile(zip_file_path, 'r') as zipf:
            for member in zipf.infolist():
                # Determine the new path
                original_path = os.path.normpath(member.filename)
                new_path = client_file_mounts_dir / original_path.lstrip('/')

                if (member.external_attr >> 28) == 0xA:
                    # Symlink. Read the target path and create a symlink.
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    target = zipf.read(member).decode()
                    assert not os.path.isabs(target), target
                    # Since target is a relative path, we need to check that it
                    # is under `client_file_mounts_dir` for security.
                    full_target_path = (new_path.parent / target).resolve()
                    if not _is_relative_to(full_target_path,
                                           client_file_mounts_dir):
                        raise ValueError(f'Symlink target {target} leads to a '
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
                with zipf.open(member) as member_file, new_path.open('wb') as f:
                    # Use shutil.copyfileobj to copy files in chunks, so it does
                    # not load the entire file into memory.
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

    # Cleanup the temporary file
    zip_file_path.unlink()


@app.post('/launch')
async def launch(launch_body: payloads.LaunchBody,
                 request: fastapi.Request) -> None:
    """Launches a cluster or task."""
    request_id = request.state.request_id
    logger.info(f'Launching request: {request_id}')
    executor.schedule_request(
        request_id,
        request_name='launch',
        request_body=launch_body,
        func=execution.launch,
        schedule_type=requests_lib.ScheduleType.LONG,
        request_cluster_name=launch_body.cluster_name,
        retryable=launch_body.retry_until_up,
    )


@app.post('/exec')
# pylint: disable=redefined-builtin
async def exec(request: fastapi.Request, exec_body: payloads.ExecBody) -> None:
    """Executes a task on an existing cluster."""
    cluster_name = exec_body.cluster_name
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='exec',
        request_body=exec_body,
        func=execution.exec,
        precondition=preconditions.ClusterStartCompletePrecondition(
            request_id=request.state.request_id,
            cluster_name=cluster_name,
        ),
        schedule_type=requests_lib.ScheduleType.LONG,
        request_cluster_name=cluster_name,
    )


@app.post('/stop')
async def stop(request: fastapi.Request,
               stop_body: payloads.StopOrDownBody) -> None:
    """Stops a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='stop',
        request_body=stop_body,
        func=core.stop,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=stop_body.cluster_name,
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
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='status',
        request_body=status_body,
        func=core.status,
        schedule_type=(requests_lib.ScheduleType.LONG if
                       status_body.refresh != common_lib.StatusRefreshMode.NONE
                       else requests_lib.ScheduleType.SHORT),
    )


@app.post('/endpoints')
async def endpoints(request: fastapi.Request,
                    endpoint_body: payloads.EndpointsBody) -> None:
    """Gets the endpoint for a given cluster and port number (endpoint)."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='endpoints',
        request_body=endpoint_body,
        func=core.endpoints,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=endpoint_body.cluster,
    )


@app.post('/down')
async def down(request: fastapi.Request,
               down_body: payloads.StopOrDownBody) -> None:
    """Tears down a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='down',
        request_body=down_body,
        func=core.down,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=down_body.cluster_name,
    )


@app.post('/start')
async def start(request: fastapi.Request,
                start_body: payloads.StartBody) -> None:
    """Restarts a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='start',
        request_body=start_body,
        func=core.start,
        schedule_type=requests_lib.ScheduleType.LONG,
        request_cluster_name=start_body.cluster_name,
    )


@app.post('/autostop')
async def autostop(request: fastapi.Request,
                   autostop_body: payloads.AutostopBody) -> None:
    """Schedules an autostop/autodown for a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='autostop',
        request_body=autostop_body,
        func=core.autostop,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=autostop_body.cluster_name,
    )


@app.post('/queue')
async def queue(request: fastapi.Request,
                queue_body: payloads.QueueBody) -> None:
    """Gets the job queue of a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='queue',
        request_body=queue_body,
        func=core.queue,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=queue_body.cluster_name,
    )


@app.post('/job_status')
async def job_status(request: fastapi.Request,
                     job_status_body: payloads.JobStatusBody) -> None:
    """Gets the status of a job."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='job_status',
        request_body=job_status_body,
        func=core.job_status,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=job_status_body.cluster_name,
    )


@app.post('/cancel')
async def cancel(request: fastapi.Request,
                 cancel_body: payloads.CancelBody) -> None:
    """Cancels jobs on a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='cancel',
        request_body=cancel_body,
        func=core.cancel,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=cancel_body.cluster_name,
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
    # Only initialize the context in logs handler to limit the scope of this
    # experimental change.
    # TODO(aylei): init in lifespan() to enable SkyPilot context in all APIs.
    context.initialize()
    request_task = executor.prepare_request(
        request_id=request.state.request_id,
        request_name='logs',
        request_body=cluster_job_body,
        func=core.tail_logs,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )
    task = asyncio.create_task(executor.execute_request_coroutine(request_task))

    def cancel_task():
        task.cancel()

    # Cancel the task after the request is done or client disconnects
    background_tasks.add_task(cancel_task)
    # TODO(zhwu): This makes viewing logs in browser impossible. We should adopt
    # the same approach as /stream.
    return stream_utils.stream_response(
        request_id=request.state.request_id,
        logs_path=request_task.log_path,
        background_tasks=background_tasks,
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
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='download_logs',
        request_body=cluster_jobs_body,
        func=core.download_logs,
        schedule_type=requests_lib.ScheduleType.SHORT,
        request_cluster_name=cluster_jobs_body.cluster_name,
    )


@app.post('/download')
async def download(download_body: payloads.DownloadBody) -> None:
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
        folders = [
            str(folder_path.expanduser().resolve())
            for folder_path in folder_paths
        ]
        storage_utils.zip_files_and_folders(folders, zip_path)

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


@app.post('/cost_report')
async def cost_report(request: fastapi.Request,
                      cost_report_body: payloads.CostReportBody) -> None:
    """Gets the cost report of a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='cost_report',
        request_body=cost_report_body,
        func=core.cost_report,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@app.get('/storage/ls')
async def storage_ls(request: fastapi.Request) -> None:
    """Gets the storages."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='storage_ls',
        request_body=payloads.RequestBody(),
        func=core.storage_ls,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@app.post('/storage/delete')
async def storage_delete(request: fastapi.Request,
                         storage_body: payloads.StorageBody) -> None:
    """Deletes a storage."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='storage_delete',
        request_body=storage_body,
        func=core.storage_delete,
        schedule_type=requests_lib.ScheduleType.LONG,
    )


@app.post('/local_up')
async def local_up(request: fastapi.Request,
                   local_up_body: payloads.LocalUpBody) -> None:
    """Launches a Kubernetes cluster on API server."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='local_up',
        request_body=local_up_body,
        func=core.local_up,
        schedule_type=requests_lib.ScheduleType.LONG,
    )


@app.post('/local_down')
async def local_down(request: fastapi.Request) -> None:
    """Tears down the Kubernetes cluster started by local_up."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='local_down',
        request_body=payloads.RequestBody(),
        func=core.local_down,
        schedule_type=requests_lib.ScheduleType.LONG,
    )


# === API server related APIs ===
@app.get('/api/get')
async def api_get(request_id: str) -> requests_lib.RequestPayload:
    """Gets a request with a given request ID prefix."""
    while True:
        request_task = requests_lib.get_request(request_id)
        if request_task is None:
            print(f'No task with request ID {request_id}', flush=True)
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id!r} not found')
        if request_task.status > requests_lib.RequestStatus.RUNNING:
            if request_task.should_retry:
                raise fastapi.HTTPException(
                    status_code=503,
                    detail=f'Request {request_id!r} should be retried')
            request_error = request_task.get_error()
            if request_error is not None:
                raise fastapi.HTTPException(status_code=500,
                                            detail=dataclasses.asdict(
                                                request_task.encode()))
            return request_task.encode()
        # yield control to allow other coroutines to run, sleep shortly
        # to avoid storming the DB and CPU in the meantime
        await asyncio.sleep(0.1)


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
    if request_id is not None and log_path is not None:
        raise fastapi.HTTPException(
            status_code=400,
            detail='Only one of request_id and log_path can be provided')

    if request_id is None and log_path is None:
        request_id = requests_lib.get_latest_request_id()
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

    # Original plain text streaming logic
    if request_id is not None:
        request_task = requests_lib.get_request(request_id)
        if request_task is None:
            print(f'No task with request ID {request_id}')
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id!r} not found')
        log_path_to_stream = request_task.log_path
    else:
        assert log_path is not None, (request_id, log_path)
        if log_path == constants.API_SERVER_LOGS:
            resolved_log_path = pathlib.Path(
                constants.API_SERVER_LOGS).expanduser()
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
    return fastapi.responses.StreamingResponse(
        content=stream_utils.log_streamer(request_id,
                                          log_path_to_stream,
                                          plain_logs=format == 'plain',
                                          tail=tail,
                                          follow=follow),
        media_type='text/plain',
        headers={
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
            'Transfer-Encoding': 'chunked'
        },
    )


@app.post('/api/cancel')
async def api_cancel(request: fastapi.Request,
                     request_cancel_body: payloads.RequestCancelBody) -> None:
    """Cancels requests."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='api_cancel',
        request_body=request_cancel_body,
        func=requests_lib.kill_requests,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@app.get('/api/status')
async def api_status(
    request_ids: Optional[List[str]] = fastapi.Query(
        None, description='Request IDs to get status for.'),
    all_status: bool = fastapi.Query(
        False, description='Get finished requests as well.'),
) -> List[requests_lib.RequestPayload]:
    """Gets the list of requests."""
    if request_ids is None:
        statuses = None
        if not all_status:
            statuses = [
                requests_lib.RequestStatus.PENDING,
                requests_lib.RequestStatus.RUNNING,
            ]
        return [
            request_task.readable_encode()
            for request_task in requests_lib.get_request_tasks(status=statuses)
        ]
    else:
        encoded_request_tasks = []
        for request_id in request_ids:
            request_task = requests_lib.get_request(request_id)
            if request_task is None:
                continue
            encoded_request_tasks.append(request_task.readable_encode())
        return encoded_request_tasks


@app.get('/api/health')
async def health(request: fastapi.Request) -> Dict[str, Any]:
    """Checks the health of the API server.

    Returns:
        A dictionary with the following keys:
        - status: str; The status of the API server.
        - api_version: str; The API version of the API server.
        - version: str; The version of SkyPilot used for API server.
        - version_on_disk: str; The version of the SkyPilot installation on
          disk, which can be used to warn about restarting the API server
        - commit: str; The commit hash of SkyPilot used for API server.
    """
    user = request.state.auth_user
    logger.info(f'Health endpoint: request.state.auth_user = {user}')
    return {
        'status': common.ApiServerStatus.HEALTHY.value,
        'api_version': server_constants.API_VERSION,
        'version': sky.__version__,
        'version_on_disk': common.get_skypilot_version_on_disk(),
        'commit': sky.__commit__,
        'user': user.to_dict() if user is not None else None,
        'basic_auth_enabled': os.environ.get(
            constants.ENV_VAR_ENABLE_BASIC_AUTH, 'false').lower() == 'true',
    }


@app.websocket('/kubernetes-pod-ssh-proxy')
async def kubernetes_pod_ssh_proxy(websocket: fastapi.WebSocket,
                                   cluster_name: str) -> None:
    """Proxies SSH to the Kubernetes pod with websocket."""
    await websocket.accept()
    logger.info(f'WebSocket connection accepted for cluster: {cluster_name}')

    cluster_records = core.status(cluster_name, all_users=True)
    cluster_record = cluster_records[0]
    if cluster_record['status'] != status_lib.ClusterStatus.UP:
        raise fastapi.HTTPException(
            status_code=400, detail=f'Cluster {cluster_name} is not running')

    handle = cluster_record['handle']
    assert handle is not None, 'Cluster handle is None'
    if not isinstance(handle.launched_resources.cloud, clouds.Kubernetes):
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Cluster {cluster_name} is not a Kubernetes cluster'
            'Use ssh to connect to the cluster instead.')

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
    try:
        # Connect to the local port
        reader, writer = await asyncio.open_connection('127.0.0.1', local_port)

        async def websocket_to_ssh():
            try:
                async for message in websocket.iter_bytes():
                    writer.write(message)
                    await writer.drain()
            except fastapi.WebSocketDisconnect:
                pass
            writer.close()

        async def ssh_to_websocket():
            try:
                while True:
                    data = await reader.read(1024)
                    if not data:
                        break
                    await websocket.send_bytes(data)
            except Exception:  # pylint: disable=broad-except
                pass
            await websocket.close()

        await asyncio.gather(websocket_to_ssh(), ssh_to_websocket())
    finally:
        proc.terminate()


@app.get('/all_contexts')
async def all_contexts(request: fastapi.Request) -> None:
    """Gets all Kubernetes and SSH node pool contexts."""

    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='all_contexts',
        request_body=payloads.RequestBody(),
        func=core.get_all_contexts,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@app.get('/gpu-metrics')
async def gpu_metrics() -> fastapi.Response:
    """Gets the GPU metrics from multiple external k8s clusters"""
    contexts = core.get_all_contexts()
    all_metrics = []
    successful_contexts = 0

    tasks = [
        asyncio.create_task(metrics_utils.get_metrics_for_context(context))
        for context in contexts
        if context != 'in-cluster'
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                f'Failed to get metrics for context {contexts[i]}: {result}')
        else:
            metrics_text = result
            all_metrics.append(metrics_text)
            successful_contexts += 1

    combined_metrics = '\n\n'.join(all_metrics)

    # Return as plain text for Prometheus compatibility
    return fastapi.Response(
        content=combined_metrics,
        media_type='text/plain; version=0.0.4; charset=utf-8')


# === Internal APIs ===
@app.get('/api/completion/cluster_name')
async def complete_cluster_name(incomplete: str,) -> List[str]:
    return global_user_state.get_cluster_names_start_with(incomplete)


@app.get('/api/completion/storage_name')
async def complete_storage_name(incomplete: str,) -> List[str]:
    return global_user_state.get_storage_names_start_with(incomplete)


@app.get('/api/completion/volume_name')
async def complete_volume_name(incomplete: str,) -> List[str]:
    return global_user_state.get_volume_names_start_with(incomplete)


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


if __name__ == '__main__':
    import uvicorn

    from sky.server import uvicorn as skyuvicorn

    requests_lib.reset_db_and_logs()

    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=46580, type=int)
    parser.add_argument('--deploy', action='store_true')
    # Serve metrics on a separate port to isolate it from the application APIs:
    # metrics port will not be exposed to the public network typically.
    parser.add_argument('--metrics-port', default=9090, type=int)
    cmd_args = parser.parse_args()
    if cmd_args.port == cmd_args.metrics_port:
        raise ValueError('port and metrics-port cannot be the same')

    # Show the privacy policy if it is not already shown. We place it here so
    # that it is shown only when the API server is started.
    usage_lib.maybe_show_privacy_policy()

    config = server_config.compute_server_config(cmd_args.deploy)
    num_workers = config.num_server_workers

    queue_server: Optional[multiprocessing.Process] = None
    workers: List[executor.RequestWorker] = []
    try:
        if os.environ.get(constants.ENV_VAR_SERVER_METRICS_ENABLED):
            metrics_thread = threading.Thread(target=metrics.run_metrics_server,
                                              args=(cmd_args.host,
                                                    cmd_args.metrics_port),
                                              daemon=True)
            metrics_thread.start()
        queue_server, workers = executor.start(config)

        logger.info(f'Starting SkyPilot API server, workers={num_workers}')
        # We don't support reload for now, since it may cause leakage of request
        # workers or interrupt running requests.
        config = uvicorn.Config('sky.server.server:app',
                                host=cmd_args.host,
                                port=cmd_args.port,
                                workers=num_workers)
        skyuvicorn.run(config)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(f'Failed to start SkyPilot API server: '
                     f'{common_utils.format_exception(exc, use_bracket=True)}')
        raise
    finally:
        logger.info('Shutting down SkyPilot API server...')

        subprocess_utils.run_in_parallel(lambda worker: worker.cancel(),
                                         workers,
                                         num_threads=len(workers))
        if queue_server is not None:
            queue_server.kill()
            queue_server.join()
