"""Common data structures and constants used in the API."""

import dataclasses
import enum
import functools
from http.cookiejar import MozillaCookieJar
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import typing
from typing import Any, Dict, Optional
from urllib import parse
import uuid

import colorama
import filelock

import sky
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.data import data_utils
from sky.server import constants as server_constants
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import rich_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import pydantic
    import requests

    from sky import dag as dag_lib
else:
    pydantic = adaptors_common.LazyImport('pydantic')
    requests = adaptors_common.LazyImport('requests')

DEFAULT_SERVER_URL = 'http://127.0.0.1:46580'
AVAILBLE_LOCAL_API_SERVER_HOSTS = ['0.0.0.0', 'localhost', '127.0.0.1']
AVAILABLE_LOCAL_API_SERVER_URLS = [
    f'http://{host}:46580' for host in AVAILBLE_LOCAL_API_SERVER_HOSTS
]

API_SERVER_CMD = '-m sky.server.server'
# The client dir on the API server for storing user-specific data, such as file
# mounts, logs, etc. This dir is empheral and will be cleaned up when the API
# server is restarted.
API_SERVER_CLIENT_DIR = pathlib.Path('~/.sky/api_server/clients')
RETRY_COUNT_ON_TIMEOUT = 3

# The maximum time to wait for the API server to start, set to a conservative
# value that unlikely to reach since the server might be just starting slowly
# (e.g. in high contention env) and we will exit eagerly if server exit.
WAIT_APISERVER_START_TIMEOUT_SEC = 60

_VERSION_INFO = (
    f'{colorama.Style.RESET_ALL}'
    f'{colorama.Style.DIM}'
    'client version: v{client_version} (API version: v{client_api_version})\n'
    'server version: v{server_version} (API version: v{server_api_version})'
    f'{colorama.Style.RESET_ALL}')
_LOCAL_API_SERVER_RESTART_HINT = (
    f'{colorama.Fore.YELLOW}Please restart the SkyPilot API server with:\n'
    f'{colorama.Style.BRIGHT}sky api stop; sky api start'
    f'{colorama.Style.RESET_ALL}')
_LOCAL_SERVER_VERSION_MISMATCH_WARNING = (
    f'{colorama.Fore.YELLOW}Client and local API server version mismatch:\n'
    '{version_info}\n'
    f'{_LOCAL_API_SERVER_RESTART_HINT}'
    f'{colorama.Style.RESET_ALL}')
_CLIENT_TOO_OLD_WARNING = (
    f'{colorama.Fore.YELLOW}Your SkyPilot client is too old:\n'
    '{version_info}\n'
    f'{colorama.Fore.YELLOW}Upgrade your client with:\n'
    '{command}'
    f'{colorama.Style.RESET_ALL}')
_REMOTE_SERVER_TOO_OLD_WARNING = (
    f'{colorama.Fore.YELLOW}SkyPilot API server is too old:\n'
    '{version_info}\n'
    f'{colorama.Fore.YELLOW}Contact your administrator to upgrade the '
    'remote API server or downgrade your local client with:\n'
    '{command}\n'
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
# Parse local API version eargly to catch version format errors.
_LOCAL_API_VERSION: int = int(server_constants.API_VERSION)
# SkyPilot dev version.
_DEV_VERSION = '1.0.0-dev0'

RequestId = str
ApiVersion = Optional[str]

logger = sky_logging.init_logger(__name__)

hinted_for_server_install_version_mismatch = False


class ApiServerStatus(enum.Enum):
    HEALTHY = 'healthy'
    UNHEALTHY = 'unhealthy'
    VERSION_MISMATCH = 'version_mismatch'


@dataclasses.dataclass
class ApiServerInfo:
    status: ApiServerStatus
    api_version: ApiVersion = None
    version: Optional[str] = None
    version_on_disk: Optional[str] = None
    commit: Optional[str] = None


def get_api_cookie_jar() -> requests.cookies.RequestsCookieJar:
    """Returns the cookie jar used by the client to access the API server."""
    cookie_file = os.environ.get(server_constants.API_COOKIE_FILE_ENV_VAR)
    cookie_jar = requests.cookies.RequestsCookieJar()
    if cookie_file and os.path.exists(cookie_file):
        cookie_path = pathlib.Path(cookie_file).expanduser().resolve()
        file_cookie_jar = MozillaCookieJar(cookie_path)
        file_cookie_jar.load()
        cookie_jar.update(file_cookie_jar)
    return cookie_jar


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
def get_dashboard_url(server_url: str) -> str:
    # The server_url may include username or password with the
    # format of https://username:password@example.com:8080/path
    # We need to remove the username and password and only
    # return `https://example.com:8080/path`
    parsed = parse.urlparse(server_url)
    # Reconstruct the URL without credentials but keeping the scheme
    dashboard_url = f'{parsed.scheme}://{parsed.hostname}'
    if parsed.port:
        dashboard_url = f'{dashboard_url}:{parsed.port}'
    if parsed.path:
        dashboard_url = f'{dashboard_url}{parsed.path}'
    dashboard_url = dashboard_url.rstrip('/')
    return f'{dashboard_url}/dashboard'


@annotations.lru_cache(scope='global')
def is_api_server_local():
    return get_server_url() in AVAILABLE_LOCAL_API_SERVER_URLS


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
            response = requests.get(f'{server_url}/api/health',
                                    timeout=2.5,
                                    cookies=get_api_cookie_jar())
            if response.status_code == 200:
                try:
                    result = response.json()
                    api_version = result.get('api_version')
                    version = result.get('version')
                    version_on_disk = result.get('version_on_disk')
                    commit = result.get('commit')
                    server_info = ApiServerInfo(status=ApiServerStatus.HEALTHY,
                                                api_version=api_version,
                                                version=version,
                                                version_on_disk=version_on_disk,
                                                commit=commit)
                    if api_version is None or version is None or commit is None:
                        logger.warning(f'API server response missing '
                                       f'version info. {server_url} may '
                                       f'not be running SkyPilot API server.')
                        server_info.status = ApiServerStatus.UNHEALTHY
                    elif api_version != server_constants.API_VERSION:
                        server_info.status = ApiServerStatus.VERSION_MISMATCH
                    return server_info
                except (json.JSONDecodeError, AttributeError) as e:
                    logger.warning('Failed to parse API server response: '
                                   f'{str(e)}')
                    return ApiServerInfo(status=ApiServerStatus.UNHEALTHY)
            else:
                return ApiServerInfo(status=ApiServerStatus.UNHEALTHY)
        except requests.exceptions.Timeout:
            if time_out_try_count == RETRY_COUNT_ON_TIMEOUT:
                return ApiServerInfo(status=ApiServerStatus.UNHEALTHY)
            time_out_try_count += 1
            continue
        except requests.exceptions.ConnectionError:
            return ApiServerInfo(status=ApiServerStatus.UNHEALTHY)

    return ApiServerInfo(status=ApiServerStatus.UNHEALTHY)


def handle_request_error(response: 'requests.Response') -> None:
    if response.status_code != 200:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                'Failed to process response from SkyPilot API server at '
                f'{get_server_url()}. '
                f'Response: {response.status_code} '
                f'{response.text}')


def get_request_id(response: 'requests.Response') -> RequestId:
    handle_request_error(response)
    request_id = response.headers.get('X-Skypilot-Request-ID')
    if request_id is None:
        request_id = response.headers.get('X-Request-ID')
    if request_id is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                'Failed to get request ID from SkyPilot API server at '
                f'{get_server_url()}. Response: {response.status_code} '
                f'{response.text}')
    return request_id


def _start_api_server(deploy: bool = False,
                      host: str = '127.0.0.1',
                      foreground: bool = False):
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
        avail_mem_size_gb: float = common_utils.get_mem_size_gb()
        if avail_mem_size_gb <= server_constants.MIN_AVAIL_MEM_GB:
            logger.warning(
                f'{colorama.Fore.YELLOW}Your SkyPilot API server machine only '
                f'has {avail_mem_size_gb:.1f}GB memory available. '
                f'At least {server_constants.MIN_AVAIL_MEM_GB}GB is '
                'recommended to support higher load with better performance.'
                f'{colorama.Style.RESET_ALL}')

        args = [sys.executable, *API_SERVER_CMD.split()]
        if deploy:
            args += ['--deploy']
        if host is not None:
            args += [f'--host={host}']

        if foreground:
            # Replaces the current process with the API server
            os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
            os.execvp(args[0], args)

        log_path = os.path.expanduser(constants.API_SERVER_LOGS)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        cmd = f'{" ".join(args)} > {log_path} 2>&1 < /dev/null'

        # Start the API server process in the background and don't wait for it.
        # If this is called from a CLI invocation, we need
        # start_new_session=True so that SIGINT on the CLI will not also kill
        # the API server.
        server_env = os.environ.copy()
        server_env[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
        proc = subprocess.Popen(cmd,
                                shell=True,
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
        if api_server_info.version == _DEV_VERSION:
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
                    '&& npm --prefix sky/dashboard run build\n')
            dashboard_msg += (
                f'{ux_utils.INDENT_LAST_SYMBOL}{colorama.Fore.GREEN}'
                f'Dashboard: {get_dashboard_url(server_url)}')
            dashboard_msg += f'{colorama.Style.RESET_ALL}'
        logger.info(
            ux_utils.finishing_message(
                f'SkyPilot API server started. {dashboard_msg}'))


def check_server_healthy(endpoint: Optional[str] = None,) -> None:
    """Check if the API server is healthy.

    Args:
        endpoint (Optional[str]): The endpoint of the API server.
        If None, the default endpoint will be used.

    Raises:
        RuntimeError: If the server is not healthy or the client version does
            not match the server version.
    """
    endpoint = endpoint if endpoint is not None else get_server_url()
    api_server_info = get_api_server_status(endpoint)
    api_server_status = api_server_info.status
    if api_server_status == ApiServerStatus.VERSION_MISMATCH:
        sv = api_server_info.api_version
        assert sv is not None, 'Server API version is None'
        try:
            server_is_older = int(sv) < _LOCAL_API_VERSION
        except ValueError:
            # Raised when the server version using an unknown scheme.
            # Version compatibility checking is expected to handle all legacy
            # cases so we safely assume the server is newer when the version
            # scheme is unknown.
            logger.debug('API server version using unknown scheme: %s', sv)
            server_is_older = False
        version_info = _get_version_info_hint(api_server_info)
        if is_api_server_local():
            # For local server, just hint user to restart the server to get
            # a consistent version.
            msg = _LOCAL_SERVER_VERSION_MISMATCH_WARNING.format(
                version_info=version_info)
        else:
            assert api_server_info.version is not None, 'Server version is None'
            if server_is_older:
                msg = _REMOTE_SERVER_TOO_OLD_WARNING.format(
                    version_info=version_info,
                    command=_install_server_version_command(api_server_info))
            else:
                msg = _CLIENT_TOO_OLD_WARNING.format(
                    version_info=version_info,
                    command=_install_server_version_command(api_server_info))
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


def _get_version_info_hint(server_info: ApiServerInfo) -> str:
    assert server_info.version is not None, 'Server version is None'
    # version_on_disk may be None if the server is older
    assert server_info.commit is not None, 'Server commit is None'
    sv = server_info.version
    cv = sky.__version__
    if server_info.version == _DEV_VERSION:
        sv = f'{sv} with commit {server_info.commit}'
    if cv == _DEV_VERSION:
        cv = f'{cv} with commit {sky.__commit__}'
    return _VERSION_INFO.format(client_version=cv,
                                server_version=sv,
                                client_api_version=server_constants.API_VERSION,
                                server_api_version=server_info.api_version)


def _install_server_version_command(server_info: ApiServerInfo) -> str:
    assert server_info.version is not None, 'Server version is None'
    assert server_info.commit is not None, 'Server commit is None'
    if server_info.version == _DEV_VERSION:
        # Dev build without valid version.
        return ('pip install git+https://github.com/skypilot-org/skypilot@'
                f'{server_info.commit}')
    elif 'dev' in server_info.version:
        # Nightly version.
        return f'pip install -U "skypilot-nightly=={server_info.version}"'
    else:
        # Stable version.
        return f'pip install -U "skypilot=={server_info.version}"'


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
                                     foreground: bool = False):
    try:
        check_server_healthy()
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
            api_server_info = get_api_server_status(endpoint)
            if api_server_info.status == ApiServerStatus.UNHEALTHY:
                _start_api_server(deploy, host, foreground)


def check_server_healthy_or_start(func):

    @functools.wraps(func)
    def wrapper(*args, deploy: bool = False, host: str = '127.0.0.1', **kwargs):
        check_server_healthy_or_start_fn(deploy, host)
        return func(*args, **kwargs)

    return wrapper


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

    task_configs = common_utils.read_yaml_all(str(client_task_path))
    for task_config in task_configs:
        if task_config is None:
            continue
        file_mounts_mapping = task_config.get('file_mounts_mapping', {})
        if not file_mounts_mapping:
            # We did not mount any files to new paths on the remote server
            # so no need to resolve filepaths.
            continue
        if 'workdir' in task_config:
            workdir = task_config['workdir']
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
    common_utils.dump_yaml(str(translated_client_task_path), task_configs)

    dag = dag_utils.load_chain_dag_from_yaml(str(translated_client_task_path))
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
                           using_remote_api_server: bool):
    """Reload modules, global variables, and usage message for a new request."""
    # This should be called first to make sure the logger is up-to-date.
    sky_logging.reload_logger()

    # Reload the skypilot config to make sure the latest config is used.
    skypilot_config.safe_reload_config()

    # Reset the client entrypoint and command for the usage message.
    common_utils.set_client_status(
        client_entrypoint=client_entrypoint,
        client_command=client_command,
        using_remote_api_server=using_remote_api_server,
    )

    # Clear cache should be called before reload_logger and usage reset,
    # otherwise, the latest env var will not be used.
    for func in annotations.FUNCTIONS_NEED_RELOAD_CACHE:
        func.cache_clear()

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
            os.remove(f'{db_path}{extension}')
        except FileNotFoundError:
            logger.debug(f'Database file {db_path}{extension} not found.')
