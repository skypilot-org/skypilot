"""Common data structures and constants used in the API."""

import dataclasses
import enum
import functools
from http.cookiejar import CookieJar
from http.cookiejar import MozillaCookieJar
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import typing
from typing import (Any, Callable, cast, Dict, Generic, Literal, Optional,
                    Tuple, TypeVar, Union)
from urllib.request import Request
import uuid

import cachetools
import click
import colorama
import filelock
from packaging import version as version_lib
from passlib import context as passlib_context
from typing_extensions import ParamSpec

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.client import service_account_auth
from sky.data import data_utils
from sky.server import constants as server_constants
from sky.server import rest
from sky.server import versions
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import rich_utils
from sky.utils import ux_utils
from sky.utils import yaml_utils

if typing.TYPE_CHECKING:
    import aiohttp
    import pydantic
    import requests

    from sky import dag as dag_lib
    from sky import models
else:
    aiohttp = adaptors_common.LazyImport('aiohttp')
    pydantic = adaptors_common.LazyImport('pydantic')
    requests = adaptors_common.LazyImport('requests')

DEFAULT_SERVER_URL = 'http://127.0.0.1:46580'
AVAILBLE_LOCAL_API_SERVER_HOSTS = ['0.0.0.0', 'localhost', '127.0.0.1']
AVAILABLE_LOCAL_API_SERVER_URLS = [
    f'http://{host}:46580' for host in AVAILBLE_LOCAL_API_SERVER_HOSTS
]

API_SERVER_CMD = '-m sky.server.server'
# The client dir on the API server for storing user-specific data, such as file
# mounts, logs, etc. This dir is ephemeral and will be cleaned up when the API
# server is restarted.
API_SERVER_CLIENT_DIR = pathlib.Path('~/.sky/api_server/clients')
RETRY_COUNT_ON_TIMEOUT = 3

# The maximum time to wait for the API server to start, set to a conservative
# value that unlikely to reach since the server might be just starting slowly
# (e.g. in high contention env) and we will exit eagerly if server exit.
WAIT_APISERVER_START_TIMEOUT_SEC = 60

_LOCAL_API_SERVER_RESTART_HINT = (
    f'{colorama.Fore.YELLOW}The local SkyPilot API server is not compatible '
    'with the client. Please restart the API server with:\n'
    f'{colorama.Style.BRIGHT}sky api stop; sky api start'
    f'{colorama.Style.RESET_ALL}')
_SERVER_INSTALL_VERSION_MISMATCH_WARNING = (
    f'{colorama.Fore.YELLOW}SkyPilot API server version does not match the '
    'installation on disk:\n'
    f'{colorama.Style.RESET_ALL}'
    f'{colorama.Style.DIM}'
    'running API server version: {server_version}\n'
    'installed API server version: {version_on_disk}\n'
    f'{colorama.Style.RESET_ALL}'
    f'{colorama.Fore.YELLOW}This can happen if you upgraded SkyPilot without '
    'restarting the API server.'
    f'{colorama.Style.RESET_ALL}')

T = TypeVar('T')
P = ParamSpec('P')


class RequestId(str, Generic[T]):
    pass


ApiVersion = Optional[str]

logger = sky_logging.init_logger(__name__)

hinted_for_server_install_version_mismatch = False
_upgrade_hint_shown = False

crypt_ctx = passlib_context.CryptContext([
    'bcrypt', 'sha256_crypt', 'sha512_crypt', 'des_crypt', 'apr_md5_crypt',
    'ldap_sha1'
])


class ApiServerStatus(enum.Enum):
    HEALTHY = 'healthy'
    UNHEALTHY = 'unhealthy'
    VERSION_MISMATCH = 'version_mismatch'
    NEEDS_AUTH = 'needs_auth'


@dataclasses.dataclass
class ApiServerInfo:
    status: ApiServerStatus
    api_version: ApiVersion = None
    version: Optional[str] = None
    version_on_disk: Optional[str] = None
    commit: Optional[str] = None
    user: Optional[Dict[str, Any]] = None
    basic_auth_enabled: bool = False
    error: Optional[str] = None
    latest_version: Optional[str] = None


def check_and_print_upgrade_hint(api_server_info: ApiServerInfo,
                                 endpoint: str) -> None:
    """Check for newer SkyPilot version and print upgrade hint if available.

    This function checks the API server info for latest_version and prints
    an upgrade hint if a newer version is available. It only shows the hint
    once per CLI session.

    Args:
        api_server_info: The API server info containing version information.
        endpoint: The endpoint URL of the API server.
    """
    global _upgrade_hint_shown

    # Skip if we've already shown the hint
    if _upgrade_hint_shown:
        return

    # Skip if no latest version info
    if not api_server_info.latest_version:
        return

    latest_version = api_server_info.latest_version
    current_version = api_server_info.version or 'unknown'

    # Skip if current version is unknown (can't compare)
    if current_version == 'unknown':
        return

    # Compare versions - only show hint if latest is actually newer
    try:
        if version_lib.parse(latest_version) <= version_lib.parse(
                current_version):
            return
    except version_lib.InvalidVersion as e:
        logger.debug(f'Failed to parse version string(s): {e}')
        return

    # Check if API server is local or remote
    is_local = is_api_server_local(endpoint)

    _upgrade_hint_shown = True

    # Build the hint message
    hint_lines = [
        f'{colorama.Fore.YELLOW}'
        f'💡 A new version of SkyPilot is available: {latest_version} '
        f'(you have {current_version})'
        f'{colorama.Style.RESET_ALL}',
    ]

    # Add server-specific instructions
    if is_local:
        # Server only returns stable releases, so always
        # use stable upgrade command
        upgrade_command = 'pip install --upgrade skypilot'

        hint_lines.extend([
            f'{ux_utils.INDENT_SYMBOL}Upgrade with: '
            f'{colorama.Fore.CYAN}{upgrade_command}'
            f'{colorama.Style.RESET_ALL}',
            f'{ux_utils.INDENT_LAST_SYMBOL}'
            'After upgrading, restart the API server: '
            f'{colorama.Fore.CYAN}sky api stop && sky api start'
            f'{colorama.Style.RESET_ALL}',
        ])
    else:
        hint_lines.append(
            f'{ux_utils.INDENT_LAST_SYMBOL}Note: This is a remote API server. '
            f'Please upgrade SkyPilot on the server side via your deployment '
            f'method.'
            f'{colorama.Style.RESET_ALL}')

    click.echo('\n'.join(hint_lines), err=True)


def get_api_cookie_jar_path() -> pathlib.Path:
    """Returns the Path to the API cookie jar file."""
    return pathlib.Path(
        os.environ.get(server_constants.API_COOKIE_FILE_ENV_VAR,
                       server_constants.API_COOKIE_FILE_DEFAULT_LOCATION)
    ).expanduser().resolve()


def get_api_cookie_jar() -> requests.cookies.RequestsCookieJar:
    """Returns the cookie jar used by the client to access the API server."""
    cookie_jar = requests.cookies.RequestsCookieJar()
    cookie_path = get_api_cookie_jar_path()
    if cookie_path.exists():
        file_cookie_jar = MozillaCookieJar(cookie_path)
        file_cookie_jar.load()
        cookie_jar.update(file_cookie_jar)
    return cookie_jar


def get_cookie_header_for_url(url: str) -> Dict[str, str]:
    """Extract Cookie header value from a cookie jar for a specific URL"""
    cookies = get_api_cookie_jar()
    if not cookies:
        return {}

    # Use urllib Request to do URL-aware cookie filtering
    request = Request(url)
    cookies.add_cookie_header(request)
    cookie_header = request.get_header('Cookie')

    if cookie_header is None:
        return {}
    return {'Cookie': cookie_header}


def set_api_cookie_jar(cookie_jar: CookieJar,
                       create_if_not_exists: bool = True) -> None:
    """Updates the file cookie jar with the given cookie jar."""
    if len(cookie_jar) == 0:
        return
    cookie_path = get_api_cookie_jar_path()
    if not cookie_path.exists() and not create_if_not_exists:
        # if the file doesn't exist and we don't want to create it, do nothing
        return
    if not cookie_path.parent.exists():
        cookie_path.parent.mkdir(parents=True, exist_ok=True)

    # Writing directly to the cookie jar path can race with other processes that
    # are reading the cookie jar, making it look malformed. Instead, write to a
    # temporary file and then move it to the final location.
    # Avoid hardcoding the tmp file path, since it could cause a race with other
    # processes that are also writing to the tmp file.
    with tempfile.NamedTemporaryFile(dir=cookie_path.parent,
                                     delete=False) as tmp_file:
        tmp_cookie_path = tmp_file.name
    file_cookie_jar = MozillaCookieJar(tmp_cookie_path)
    if cookie_path.exists():
        file_cookie_jar.load(str(cookie_path))

    for cookie in cookie_jar:
        file_cookie_jar.set_cookie(cookie)
    file_cookie_jar.save()

    # Move the temporary file to the final location.
    os.replace(tmp_cookie_path, cookie_path)


def get_cookies_from_response(
        response: 'requests.Response') -> requests.cookies.RequestsCookieJar:
    """Returns the cookies from the API server response."""
    server_url = get_server_url()
    cookies = response.cookies
    for prev_resp in response.history:
        for cookie in prev_resp.cookies:
            if cookie.domain in server_url:
                cookies.set_cookie(cookie)
    return cookies


def _prepare_authenticated_request_params(
        path: str,
        server_url: Optional[str] = None,
        **kwargs) -> Tuple[str, Dict[str, Any]]:
    """Prepare common parameters for authenticated requests (sync or async).

    Returns:
        Tuple of (url, updated_kwargs)
    """
    if server_url is None:
        server_url = get_server_url()

    # Prepare headers and URL for service account authentication
    headers = service_account_auth.get_service_account_headers()

    # Merge with existing headers
    if 'headers' in kwargs:
        headers.update(kwargs['headers'])
    kwargs['headers'] = headers

    # Always use the same URL regardless of authentication type
    # OAuth2 proxy will handle authentication based on headers
    url = f'{server_url}/{path}' if not path.startswith(
        '/') else f'{server_url}{path}'

    # Use cookie authentication if no Bearer token present
    if not headers.get('Authorization') and 'cookies' not in kwargs:
        kwargs['cookies'] = get_api_cookie_jar()

    return url, kwargs


def _convert_requests_cookies_to_aiohttp(
        cookie_jar: requests.cookies.RequestsCookieJar) -> Dict[str, str]:
    """Convert requests cookie jar to aiohttp-compatible dict format."""
    cookies = {}
    for cookie in cookie_jar:
        cookies[cookie.name] = cookie.value
    return cookies  # type: ignore


def make_authenticated_request(method: str,
                               path: str,
                               server_url: Optional[str] = None,
                               retry: bool = True,
                               **kwargs) -> 'requests.Response':
    """Make an authenticated HTTP request to the API server.

    Automatically handles service account token authentication or cookie-based
    authentication based on what's available.

    Args:
        method: HTTP method (GET, POST, etc.)
        path: API path (e.g., '/api/v1/status')
        server_url: Server URL, defaults to configured server
        retry: Whether to retry on transient errors
        **kwargs: Additional arguments to pass to requests

    Returns:
        requests.Response object
    """
    url, kwargs = _prepare_authenticated_request_params(path, server_url,
                                                        **kwargs)

    # Make the request
    if retry:
        return rest.request(method, url, **kwargs)
    else:
        assert method == 'GET', 'Only GET requests can be done without retry'
        return rest.request_without_retry(method, url, **kwargs)


async def make_authenticated_request_async(
        session: 'aiohttp.ClientSession',
        method: str,
        path: str,
        server_url: Optional[str] = None,
        retry: bool = True,
        **kwargs) -> 'aiohttp.ClientResponse':
    """Make an authenticated async HTTP request to the API server using aiohttp.

    Automatically handles service account token authentication or cookie-based
    authentication based on what's available.

    Example usage:
        async with aiohttp.ClientSession() as session:
            response = await make_authenticated_request_async(
                session, 'GET', '/api/v1/status')
            data = await response.json()

    Args:
        session: aiohttp ClientSession to use for the request
        method: HTTP method (GET, POST, etc.)
        path: API path (e.g., '/api/v1/status')
        server_url: Server URL, defaults to configured server
        retry: Whether to retry on transient errors
        **kwargs: Additional arguments to pass to aiohttp

    Returns:
        aiohttp.ClientResponse object

    Raises:
        aiohttp.ClientError: For HTTP-related errors
        exceptions.ServerTemporarilyUnavailableError: When server returns 503
        exceptions.RequestInterruptedError: When request is interrupted
    """
    url, kwargs = _prepare_authenticated_request_params(path, server_url,
                                                        **kwargs)

    # Convert cookies to aiohttp format if needed
    if 'cookies' in kwargs and isinstance(kwargs['cookies'],
                                          requests.cookies.RequestsCookieJar):
        kwargs['cookies'] = _convert_requests_cookies_to_aiohttp(
            kwargs['cookies'])

    # Convert params to strings for aiohttp compatibility
    if 'params' in kwargs and kwargs['params'] is not None:
        normalized_params = {}
        for key, value in kwargs['params'].items():
            if isinstance(value, bool):
                normalized_params[key] = str(value).lower()
            elif value is not None:
                normalized_params[key] = str(value)
            # Skip None values
        kwargs['params'] = normalized_params

    # Make the request
    if retry:
        return await rest.request_async(session, method, url, **kwargs)
    else:
        assert method == 'GET', 'Only GET requests can be done without retry'
        return await rest.request_without_retry_async(session, method, url,
                                                      **kwargs)


@annotations.lru_cache(scope='global')
def get_server_url(host: Optional[str] = None) -> str:
    endpoint = DEFAULT_SERVER_URL
    if host is not None:
        endpoint = f'http://{host}:46580'

    url = os.environ.get(
        constants.SKY_API_SERVER_URL_ENV_VAR,
        skypilot_config.get_nested(('api_server', 'endpoint'), endpoint))
    return url.rstrip('/')


@annotations.lru_cache(scope='global')
def get_dashboard_url(server_url: str,
                      starting_page: Optional[str] = None) -> str:
    dashboard_url = server_url.rstrip('/')
    dashboard_url = f'{dashboard_url}/dashboard'
    if starting_page:
        dashboard_url = f'{dashboard_url}/{starting_page}'
    return dashboard_url


@annotations.lru_cache(scope='global')
def is_api_server_local(endpoint: Optional[str] = None):
    server_url = endpoint if endpoint is not None else get_server_url()
    return server_url in AVAILABLE_LOCAL_API_SERVER_URLS


def _handle_non_200_server_status(
        response: 'requests.Response') -> ApiServerInfo:
    if response.status_code == 401:
        return ApiServerInfo(status=ApiServerStatus.NEEDS_AUTH)
    if response.status_code == 400:
        # Check if a version mismatch error is returned.
        try:
            body = response.json()
            if (body.get('error',
                         '') == ApiServerStatus.VERSION_MISMATCH.value):
                return ApiServerInfo(status=ApiServerStatus.VERSION_MISMATCH,
                                     error=body.get('message', ''))
        except requests.JSONDecodeError:
            pass
    return ApiServerInfo(status=ApiServerStatus.UNHEALTHY)


@cachetools.cached(cache=cachetools.TTLCache(maxsize=10,
                                             ttl=5.0,
                                             timer=time.time),
                   lock=threading.RLock())
def get_api_server_status(endpoint: Optional[str] = None) -> ApiServerInfo:
    """Retrieve the status of the API server.

    This function checks the health of the API server by sending a request
    to the server's health endpoint. It retries the connection a specified
    number of times in case of a timeout.

    Args:
        endpoint (Optional[str]): The endpoint of the API server.
        If None, the default endpoint will be used.

    Returns:
        ApiServerInfo: An object containing the status and API version
        of the server. The status can be HEALTHY, UNHEALTHY
        or VERSION_MISMATCH.
    """
    time_out_try_count = 1
    server_url = endpoint if endpoint is not None else get_server_url()
    while time_out_try_count <= RETRY_COUNT_ON_TIMEOUT:
        try:
            response = make_authenticated_request('GET',
                                                  '/api/health',
                                                  server_url=server_url,
                                                  timeout=2.5)
        except requests.exceptions.Timeout:
            if time_out_try_count == RETRY_COUNT_ON_TIMEOUT:
                return ApiServerInfo(status=ApiServerStatus.UNHEALTHY)
            time_out_try_count += 1
            continue
        except requests.exceptions.ConnectionError:
            return ApiServerInfo(status=ApiServerStatus.UNHEALTHY)

        logger.debug(f'Health check status: {response.status_code}')

        if response.status_code != 200:
            return _handle_non_200_server_status(response)

        # The response is 200, so we can parse the response.
        try:
            result = response.json()
            server_status = result.get('status')
            api_version = result.get('api_version')
            version = result.get('version')
            version_on_disk = result.get('version_on_disk')
            commit = result.get('commit')
            user = result.get('user')
            basic_auth_enabled = result.get('basic_auth_enabled')
            latest_version = result.get('latest_version')
            server_info = ApiServerInfo(status=ApiServerStatus(server_status),
                                        api_version=api_version,
                                        version=version,
                                        version_on_disk=version_on_disk,
                                        commit=commit,
                                        user=user,
                                        basic_auth_enabled=basic_auth_enabled,
                                        latest_version=latest_version)
            if api_version is None or version is None or commit is None:
                logger.warning(f'API server response missing '
                               f'version info. {server_url} may '
                               f'not be running SkyPilot API server.')
                server_info.status = ApiServerStatus.UNHEALTHY
            version_info = versions.check_compatibility_at_client(
                response.headers)
            if version_info is None:
                # Backward compatibility for server prior to v0.11.0 which
                # does not check compatibility at server side.
                # TODO(aylei): remove this after v0.13.0 is released.
                return ApiServerInfo(
                    status=ApiServerStatus.VERSION_MISMATCH,
                    error=versions.SERVER_TOO_OLD_ERROR.format(
                        remote_version=version,
                        local_version=versions.get_local_readable_version(),
                        min_version=server_constants.MIN_COMPATIBLE_VERSION,
                        command=versions.install_version_command(
                            version, commit)))
            if version_info.error is not None:
                return ApiServerInfo(status=ApiServerStatus.VERSION_MISMATCH,
                                     error=version_info.error)

            cookies = get_cookies_from_response(response)
            # Save or refresh the cookie jar in case of session affinity and
            # OAuth.
            set_api_cookie_jar(cookies, create_if_not_exists=True)
            return server_info
        except (requests.JSONDecodeError, AttributeError) as e:
            # Try to check if we got redirected to a login page.
            for prev_response in response.history:
                logger.debug(f'Previous response: {prev_response.url}')
                # Heuristic: check if the url looks like a login page or
                # oauth flow.
                if any(key in prev_response.url for key in ['login', 'oauth2']):
                    logger.debug(f'URL {prev_response.url} looks like '
                                 'a login page or oauth flow, so try to '
                                 'get the cookie.')
                    return ApiServerInfo(status=ApiServerStatus.NEEDS_AUTH)
            logger.warning('Failed to parse API server response: '
                           f'{str(e)}')
            return ApiServerInfo(status=ApiServerStatus.UNHEALTHY)

    return ApiServerInfo(status=ApiServerStatus.UNHEALTHY)


def handle_request_error(response: 'requests.Response') -> None:
    # Keep the original HTTPError if the response code >= 400
    response.raise_for_status()

    # Other status codes are not expected neither, e.g. we do not expect to
    # handle redirection here.
    if response.status_code != 200:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                'Failed to process response from SkyPilot API server at '
                f'{response.url}. '
                f'Response: {response.status_code} '
                f'{response.text}')


def get_request_id(response: 'requests.Response') -> RequestId[T]:
    handle_request_error(response)
    request_id = response.headers.get('X-Skypilot-Request-ID')
    if request_id is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                'Failed to get request ID from SkyPilot API server at '
                f'{get_server_url()}. Response: {response.status_code} '
                f'{response.text}')
    return RequestId[T](request_id)


def get_stream_request_id(
        response: 'requests.Response') -> Optional[RequestId[T]]:
    """This is same as the above function, but just for `sdk.stream_and_get.
    We do this because `/api/stream` may choose the latest request id, and
    we need to keep track of that information. Request id in this case can
    be None."""
    handle_request_error(response)
    request_id = response.headers.get(server_constants.STREAM_REQUEST_HEADER)
    if request_id is not None:
        return RequestId[T](request_id)
    return None


def _start_api_server(deploy: bool = False,
                      host: str = '127.0.0.1',
                      foreground: bool = False,
                      metrics: bool = False,
                      metrics_port: Optional[int] = None,
                      enable_basic_auth: bool = False):
    """Starts a SkyPilot API server locally."""
    server_url = get_server_url(host)
    assert server_url in AVAILABLE_LOCAL_API_SERVER_URLS, (
        f'server url {server_url} is not a local url')

    with rich_utils.client_status('Starting SkyPilot API server, '
                                  f'view logs at {constants.API_SERVER_LOGS}'):
        logger.info(f'{colorama.Style.DIM}Failed to connect to '
                    f'SkyPilot API server at {server_url}. '
                    'Starting a local server.'
                    f'{colorama.Style.RESET_ALL}')
        if not is_api_server_local():
            raise RuntimeError(f'Cannot start API server: {get_server_url()} '
                               'is not a local URL')

        # Check available memory before starting the server.
        # Skip this warning if postgres is used, as:
        #   1) that's almost certainly a remote API server;
        #   2) the actual consolidation mode config is stashed in the database,
        #      and the value of `job_utils.is_consolidation_mode` will not be
        #      the actual value in the db, but only None as in this case, the
        #      whole YAML config is really just `db: <URI>`.
        if skypilot_config.get_nested(('db',), None) is None:
            avail_mem_size_gb: float = common_utils.get_mem_size_gb()
            # pylint: disable=import-outside-toplevel
            import sky.jobs.utils as job_utils
            max_memory = (server_constants.MIN_AVAIL_MEM_GB_CONSOLIDATION_MODE
                          if job_utils.is_consolidation_mode(
                              on_api_restart=True) else
                          server_constants.MIN_AVAIL_MEM_GB)
            if avail_mem_size_gb <= max_memory:
                logger.warning(
                    f'{colorama.Fore.YELLOW}Your SkyPilot API server machine '
                    f'only has {avail_mem_size_gb:.1f}GB memory available. '
                    f'At least {max_memory}GB is recommended to support higher '
                    'load with better performance.'
                    f'{colorama.Style.RESET_ALL}')

        args = [sys.executable, *API_SERVER_CMD.split()]
        if deploy:
            args += ['--deploy']
        if host is not None:
            args += [f'--host={host}']
        if metrics_port is not None:
            args += [f'--metrics-port={metrics_port}']

        if foreground:
            # Replaces the current process with the API server
            os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
            _set_metrics_env_var(os.environ, metrics, deploy)
            if enable_basic_auth:
                os.environ[constants.ENV_VAR_ENABLE_BASIC_AUTH] = 'true'
            # Raise the websockets library header limits before the server
            # process starts (env vars are read at import time).
            os.environ.setdefault(
                'WEBSOCKETS_MAX_LINE_LENGTH',
                server_constants.WEBSOCKETS_MAX_HEADER_LINE_LENGTH)
            os.environ.setdefault('WEBSOCKETS_MAX_NUM_HEADERS',
                                  server_constants.WEBSOCKETS_MAX_NUM_HEADERS)
            os.execvp(args[0], args)

        log_path = os.path.expanduser(constants.API_SERVER_LOGS)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        # For spawn mode, copy the environ to avoid polluting the SDK process.
        server_env = os.environ.copy()
        server_env[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        # Raise the websockets library header limits before the server
        # process starts (env vars are read at import time).
        server_env.setdefault(
            'WEBSOCKETS_MAX_LINE_LENGTH',
            server_constants.WEBSOCKETS_MAX_HEADER_LINE_LENGTH)
        server_env.setdefault('WEBSOCKETS_MAX_NUM_HEADERS',
                              server_constants.WEBSOCKETS_MAX_NUM_HEADERS)
        # Start the API server process in the background and don't wait for it.
        # If this is called from a CLI invocation, we need
        # start_new_session=True so that SIGINT on the CLI will not also kill
        # the API server.
        if enable_basic_auth:
            server_env[constants.ENV_VAR_ENABLE_BASIC_AUTH] = 'true'
        _set_metrics_env_var(server_env, metrics, deploy)
        with open(log_path, 'w', encoding='utf-8') as log_file:
            # Because the log file is opened using a with statement, it may seem
            # that the file will be closed when the with statement is exited
            # causing the child process to be unable to write to the log file.
            # However, Popen makes the file descriptor inheritable which means
            # the child process will inherit its own copy of the fd,
            # independent of the parent's fd table which enables to child
            # process to continue writing to the log file.
            proc = subprocess.Popen(args,
                                    stdout=log_file,
                                    stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL,
                                    start_new_session=True,
                                    env=server_env)

        start_time = time.time()
        while True:
            # Check if process has exited
            if proc.poll() is not None:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        'SkyPilot API server process exited unexpectedly.\n'
                        f'View logs at: {constants.API_SERVER_LOGS}')
            try:
                # Clear the cache to ensure fresh checks during startup
                get_api_server_status.cache_clear()  # type: ignore
                check_server_healthy()
            except exceptions.APIVersionMismatchError:
                raise
            except Exception as e:  # pylint: disable=broad-except
                if time.time() - start_time >= WAIT_APISERVER_START_TIMEOUT_SEC:
                    with ux_utils.print_exception_no_traceback():
                        raise RuntimeError(
                            'Failed to start SkyPilot API server at '
                            f'{get_server_url(host)}'
                            '\nView logs at: '
                            f'{constants.API_SERVER_LOGS}') from e
                time.sleep(0.5)
            else:
                break

        server_url = get_server_url(host)
        dashboard_msg = ''
        api_server_info = get_api_server_status(server_url)
        if api_server_info.version == versions.DEV_VERSION:
            dashboard_msg += (
                f'\n{colorama.Style.RESET_ALL}{ux_utils.INDENT_SYMBOL}'
                f'{colorama.Fore.YELLOW}')
            if not os.path.isdir(server_constants.DASHBOARD_DIR):
                dashboard_msg += (
                    'Dashboard is not built, '
                    'to build: npm --prefix sky/dashboard install '
                    '&& npm --prefix sky/dashboard run build\n')
            else:
                dashboard_msg += (
                    'Dashboard may be stale when installed from source, '
                    'to rebuild: npm --prefix sky/dashboard install '
                    '&& npm --prefix sky/dashboard run build')
        logger.info(
            ux_utils.finishing_message(
                f'SkyPilot API server started. {dashboard_msg}'))


def _set_metrics_env_var(env: Union[Dict[str, str], os._Environ], metrics: bool,
                         deploy: bool):
    """Sets the metrics environment variables.

    Args:
        env: The environment variables to set.
        metrics: Whether to enable metrics.
        deploy: Whether the server is running in deploy mode, which means
            multiple processes might be running.
    """
    del deploy
    if metrics or os.getenv(constants.ENV_VAR_SERVER_METRICS_ENABLED) == 'true':
        env[constants.ENV_VAR_SERVER_METRICS_ENABLED] = 'true'
        # Always set the metrics dir since we need to collect metrics from
        # subprocesses like the executor.
        metrics_dir = os.path.join(tempfile.gettempdir(), 'metrics')
        shutil.rmtree(metrics_dir, ignore_errors=True)
        os.makedirs(metrics_dir, exist_ok=True)
        # Refer to https://prometheus.github.io/client_python/multiprocess/
        env['PROMETHEUS_MULTIPROC_DIR'] = metrics_dir


def check_server_healthy(
    endpoint: Optional[str] = None
) -> Tuple[Literal[
        # Use an incomplete list of Literals here to enforce raising for other
        # enum values.
        ApiServerStatus.HEALTHY, ApiServerStatus.NEEDS_AUTH], ApiServerInfo]:
    """Check if the API server is healthy.

    Args:
        endpoint (Optional[str]): The endpoint of the API server.
        If None, the default endpoint will be used.

    Raises:
        RuntimeError: If the server is not healthy or the client version does
            not match the server version.

    Returns:
        ApiServerStatus: The status of the API server, unless the server is
            unhealthy or the client version does not match the server version,
            in which case an exception is raised.
    """
    endpoint = endpoint if endpoint is not None else get_server_url()
    api_server_info = get_api_server_status(endpoint)
    api_server_status = api_server_info.status
    if api_server_status == ApiServerStatus.VERSION_MISMATCH:
        msg = api_server_info.error
        if is_api_server_local(endpoint):
            # For local server, just hint user to restart the server to get
            # a consistent version.
            msg = _LOCAL_API_SERVER_RESTART_HINT
        with ux_utils.print_exception_no_traceback():
            raise exceptions.APIVersionMismatchError(msg)
    elif api_server_status == ApiServerStatus.UNHEALTHY:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ApiServerConnectionError(endpoint)

    # If the user ran pip upgrade, but the server wasn't restarted, warn them.
    # We check this using the info from /api/health, rather than in the
    # executor, because the executor could be started after the main server
    # process, picking up the new code, even though the main server process is
    # still running the old code.
    # Note that this code is running on the client side, so calling
    # get_skypilot_version_on_disk() from here is not correct.

    # Only show this hint once per process.
    global hinted_for_server_install_version_mismatch

    if (api_server_info.version_on_disk is not None and
            api_server_info.version != api_server_info.version_on_disk and
            not hinted_for_server_install_version_mismatch):

        logger.warning(
            _SERVER_INSTALL_VERSION_MISMATCH_WARNING.format(
                server_version=api_server_info.version,
                version_on_disk=api_server_info.version_on_disk))
        if is_api_server_local():
            logger.warning(_LOCAL_API_SERVER_RESTART_HINT)

        hinted_for_server_install_version_mismatch = True

    return api_server_status, api_server_info


# Keep in sync with sky/setup_files/setup.py find_version()
def get_skypilot_version_on_disk() -> str:
    """Get the version of the SkyPilot code on disk."""
    current_file_path = pathlib.Path(__file__)
    assert str(current_file_path).endswith(
        'server/common.py'), current_file_path
    sky_root = current_file_path.parent.parent
    with open(sky_root / '__init__.py', 'r', encoding='utf-8') as fp:
        version_match = re.search(r'^__version__ = [\'"]([^\'"]*)[\'"]',
                                  fp.read(), re.M)
        if version_match:
            return version_match.group(1)
        raise RuntimeError('Unable to find version string.')


def check_server_healthy_or_start_fn(deploy: bool = False,
                                     host: str = '127.0.0.1',
                                     foreground: bool = False,
                                     metrics: bool = False,
                                     metrics_port: Optional[int] = None,
                                     enable_basic_auth: bool = False):
    api_server_status = None
    try:
        api_server_status, _ = check_server_healthy()
        if api_server_status == ApiServerStatus.NEEDS_AUTH:
            endpoint = get_server_url()
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ApiServerAuthenticationError(endpoint)
    except exceptions.ApiServerConnectionError as exc:
        endpoint = get_server_url()
        if not is_api_server_local():
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ApiServerConnectionError(endpoint) from exc
        # Lock to prevent multiple processes from starting the server at the
        # same time, causing issues with database initialization.
        with filelock.FileLock(
                os.path.expanduser(constants.API_SERVER_CREATION_LOCK_PATH)):
            # Check again if server is already running. Other processes may
            # have started the server while we were waiting for the lock.
            get_api_server_status.cache_clear()  # type: ignore[attr-defined]
            api_server_info = get_api_server_status(endpoint)
            if api_server_info.status == ApiServerStatus.UNHEALTHY:
                _start_api_server(deploy, host, foreground, metrics,
                                  metrics_port, enable_basic_auth)


def check_server_healthy_or_start(func: Callable[P, T]) -> Callable[P, T]:

    @functools.wraps(func)
    def wrapper(*args, deploy: bool = False, host: str = '127.0.0.1', **kwargs):
        check_server_healthy_or_start_fn(deploy, host)
        return func(*args, **kwargs)

    return cast(Callable[P, T], wrapper)


def process_mounts_in_task_on_api_server(task: str, env_vars: Dict[str, str],
                                         workdir_only: bool) -> 'dag_lib.Dag':
    """Translates the file mounts path in a task to the path on API server.

    When a task involves file mounts, the client will invoke
    `upload_mounts_to_api_server` above to upload those local files to the API
    server first. This function will then translates the paths in the task to
    be the actual file paths on the API server, based on the
    `file_mounts_mapping` in the task set by the client.

    Args:
        task: The task to be translated.
        env_vars: The environment variables of the task.
        workdir_only: Whether to only translate the workdir, which is used for
            `exec`, as it does not need other files/folders in file_mounts.

    Returns:
        The translated task as a single-task dag.
    """
    from sky.utils import dag_utils  # pylint: disable=import-outside-toplevel

    versions.check_recipe_client_version(task)

    user_hash = env_vars.get(constants.USER_ID_ENV_VAR, 'unknown')

    # We should not use int(time.time()) as there can be multiple requests at
    # the same second.
    task_id = str(uuid.uuid4().hex)
    client_dir = (API_SERVER_CLIENT_DIR.expanduser().resolve() / user_hash)
    client_task_dir = client_dir / 'tasks'
    client_task_dir.mkdir(parents=True, exist_ok=True)

    client_task_path = client_task_dir / f'{task_id}.yaml'
    client_task_path.write_text(task)

    client_file_mounts_dir = client_dir / 'file_mounts'
    client_file_mounts_dir.mkdir(parents=True, exist_ok=True)

    def _get_client_file_mounts_path(
            original_path: str, file_mounts_mapping: Dict[str, str]) -> str:
        return str(client_file_mounts_dir /
                   file_mounts_mapping[original_path].lstrip('/'))

    task_configs = yaml_utils.read_yaml_all(str(client_task_path))
    for task_config in task_configs:
        if task_config is None:
            continue
        file_mounts_mapping = task_config.pop('file_mounts_mapping', {})
        if not file_mounts_mapping:
            # We did not mount any files to new paths on the remote server
            # so no need to resolve filepaths.
            continue
        if 'workdir' in task_config:
            workdir = task_config['workdir']
            if isinstance(workdir, str):
                task_config['workdir'] = str(
                    client_file_mounts_dir /
                    file_mounts_mapping[workdir].lstrip('/'))
        if workdir_only:
            continue
        if 'file_mounts' in task_config:
            file_mounts = task_config['file_mounts']
            for dst, src in file_mounts.items():
                if isinstance(src, str):
                    if not data_utils.is_cloud_store_url(src):
                        file_mounts[dst] = _get_client_file_mounts_path(
                            src, file_mounts_mapping)
                elif isinstance(src, dict):
                    if 'source' in src:
                        source = src['source']
                        if isinstance(source, str):
                            if data_utils.is_cloud_store_url(source):
                                continue
                            src['source'] = _get_client_file_mounts_path(
                                source, file_mounts_mapping)
                        else:
                            new_source = []
                            for src_item in source:
                                new_source.append(
                                    _get_client_file_mounts_path(
                                        src_item, file_mounts_mapping))
                            src['source'] = new_source
                else:
                    raise ValueError(f'Unexpected file_mounts value: {src}')
        if 'service' in task_config:
            service = task_config['service']
            if 'tls' in service:
                tls = service['tls']
                for key in ['keyfile', 'certfile']:
                    if key in tls:
                        tls[key] = _get_client_file_mounts_path(
                            tls[key], file_mounts_mapping)

    # We can switch to using string, but this is to make it easier to debug, by
    # persisting the translated task yaml file.
    translated_client_task_path = client_dir / f'{task_id}_translated.yaml'
    yaml_utils.dump_yaml(str(translated_client_task_path), task_configs)

    dag = dag_utils.load_dag_from_yaml(str(translated_client_task_path))
    return dag


def api_server_user_logs_dir_prefix(
        user_hash: Optional[str] = None) -> pathlib.Path:
    if user_hash is None:
        user_hash = common_utils.get_user_hash()
    return API_SERVER_CLIENT_DIR / user_hash / 'sky_logs'


def request_body_to_params(body: 'pydantic.BaseModel') -> Dict[str, Any]:
    return {
        k: v for k, v in body.model_dump(mode='json').items() if v is not None
    }


def reload_for_new_request(client_entrypoint: Optional[str],
                           client_command: Optional[str],
                           using_remote_api_server: bool, user: 'models.User',
                           request_id: str) -> None:
    """Reload modules, global variables, and usage message for a new request.

    Must be called within the request's context.
    """
    # This should be called first to make sure the logger is up-to-date.
    sky_logging.reload_logger()

    # Reload the skypilot config to make sure the latest config is used.
    # We don't need to grab the lock here because this function is only
    # run once we are inside the request's context, so there shouldn't
    # be any race conditions when reloading the config.
    skypilot_config.reload_config()

    # Reset the client entrypoint and command for the usage message.
    common_utils.set_request_context(
        client_entrypoint=client_entrypoint,
        client_command=client_command,
        using_remote_api_server=using_remote_api_server,
        user=user,
        request_id=request_id,
    )

    # Clear cache should be called before reload_logger and usage reset,
    # otherwise, the latest env var will not be used.
    annotations.clear_request_level_cache()

    # We need to reset usage message, so that the message is up-to-date with the
    # latest information in the context, e.g. client entrypoint and run id.
    usage_lib.messages.reset(usage_lib.MessageType.USAGE)


def clear_local_api_server_database() -> None:
    """Removes the local API server database.

    The CLI can call this during cleanup of a local API server, or the API
    server can call it during startup.
    """
    # Remove the database for requests including any files starting with
    # api.constants.API_SERVER_REQUEST_DB_PATH
    db_path = os.path.expanduser(server_constants.API_SERVER_REQUEST_DB_PATH)
    for extension in ['', '-shm', '-wal']:
        try:
            logger.debug(f'Removing database file {db_path}{extension}')
            os.remove(f'{db_path}{extension}')
        except FileNotFoundError:
            logger.debug(f'Database file {db_path}{extension} not found.')
