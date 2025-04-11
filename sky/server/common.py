"""Common data structures and constants used in the API."""

import dataclasses
import enum
import functools
from http.cookiejar import MozillaCookieJar
import json
import os
import pathlib
import subprocess
import sys
import time
import typing
from typing import Any, Dict, Optional
import uuid

import colorama
import filelock

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

SKY_API_VERSION_WARNING = (
    f'{colorama.Fore.YELLOW}SkyPilot API server is too old: '
    f'v{{server_version}} (client version is v{{client_version}}). '
    'Please restart the SkyPilot API server with: '
    'sky api stop; sky api start'
    f'{colorama.Style.RESET_ALL}')
RequestId = str
ApiVersion = Optional[str]

logger = sky_logging.init_logger(__name__)


class ApiServerStatus(enum.Enum):
    HEALTHY = 'healthy'
    UNHEALTHY = 'unhealthy'
    VERSION_MISMATCH = 'version_mismatch'


@dataclasses.dataclass
class ApiServerInfo:
    status: ApiServerStatus
    api_version: ApiVersion


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
                    if api_version is None:
                        logger.warning(f'API server response missing '
                                       f'version info. {server_url} may '
                                       f'not be running SkyPilot API server.')
                        return ApiServerInfo(status=ApiServerStatus.UNHEALTHY,
                                             api_version=None)
                    if api_version == server_constants.API_VERSION:
                        return ApiServerInfo(status=ApiServerStatus.HEALTHY,
                                             api_version=api_version)
                    return ApiServerInfo(
                        status=ApiServerStatus.VERSION_MISMATCH,
                        api_version=api_version)
                except (json.JSONDecodeError, AttributeError) as e:
                    logger.warning('Failed to parse API server response: '
                                   f'{str(e)}')
                    return ApiServerInfo(status=ApiServerStatus.UNHEALTHY,
                                         api_version=None)
            else:
                return ApiServerInfo(status=ApiServerStatus.UNHEALTHY,
                                     api_version=None)
        except requests.exceptions.Timeout:
            if time_out_try_count == RETRY_COUNT_ON_TIMEOUT:
                return ApiServerInfo(status=ApiServerStatus.UNHEALTHY,
                                     api_version=None)
            time_out_try_count += 1
            continue
        except requests.exceptions.ConnectionError:
            return ApiServerInfo(status=ApiServerStatus.UNHEALTHY,
                                 api_version=None)

    return ApiServerInfo(status=ApiServerStatus.UNHEALTHY, api_version=None)


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
            os.execvp(args[0], args)

        log_path = os.path.expanduser(constants.API_SERVER_LOGS)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        cmd = f'{" ".join(args)} > {log_path} 2>&1 < /dev/null'

        # Start the API server process in the background and don't wait for it.
        # If this is called from a CLI invocation, we need
        # start_new_session=True so that SIGINT on the CLI will not also kill
        # the API server.
        proc = subprocess.Popen(cmd, shell=True, start_new_session=True)

        start_time = time.time()
        while True:
            # Check if process has exited
            if proc.poll() is not None:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        'SkyPilot API server process exited unexpectedly.\n'
                        f'View logs at: {constants.API_SERVER_LOGS}')
            api_server_info = get_api_server_status()
            assert api_server_info.status != ApiServerStatus.VERSION_MISMATCH, (
                f'API server version mismatch when starting the server. '
                f'Server version: {api_server_info.api_version} '
                f'Client version: {server_constants.API_VERSION}')
            if api_server_info.status == ApiServerStatus.HEALTHY:
                break
            elif time.time() - start_time >= WAIT_APISERVER_START_TIMEOUT_SEC:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        'Failed to start SkyPilot API server at '
                        f'{get_server_url(host)}'
                        f'\nView logs at: {constants.API_SERVER_LOGS}')
            time.sleep(0.5)
        logger.info(ux_utils.finishing_message('SkyPilot API server started.'))


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
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                SKY_API_VERSION_WARNING.format(
                    server_version=api_server_info.api_version,
                    client_version=server_constants.API_VERSION))
    elif api_server_status == ApiServerStatus.UNHEALTHY:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ApiServerConnectionError(endpoint)


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

    # Make sure the logger takes the new environment variables. This is
    # necessary because the logger is initialized before the environment
    # variables are set, such as SKYPILOT_DEBUG.
    sky_logging.reload_logger()


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
