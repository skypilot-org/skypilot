"""Common data structures and constants used in the API."""

import dataclasses
import enum
import functools
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
import pydantic
import requests

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.data import data_utils
from sky.server import constants as server_constants
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import rich_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import dag as dag_lib

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

SKY_CLIENT_TOO_OLD_WARNING = (
    f'{colorama.Fore.YELLOW}Your SkyPilot client is too old: '
    f'v{{client_version}} (server version is v{{server_version}}). '
    'Please refer to the following link to upgrade your client:\n'
    f'{colorama.Style.RESET_ALL}'
    f'{colorama.Style.DIM}'
    'https://docs.skypilot.co/en/latest/getting-started/installation.html'
    f'{colorama.Style.RESET_ALL}')
SKY_SERVER_TOO_OLD_WARNING = (
    f'{colorama.Fore.YELLOW}SkyPilot API server is too old: '
    f'v{{server_version}} (client version is v{{client_version}}). {{hint}}'
    f'{colorama.Style.RESET_ALL}')
RESTART_LOCAL_API_SERVER_HINT = ('Please restart the SkyPilot API server with: '
                                 'sky api stop; sky api start')
UPGRADE_REMOTE_SERVER_HINT = (
    'Please refer to the following link to upgrade your server:\n'
    f'{colorama.Style.RESET_ALL}'
    f'{colorama.Style.DIM}'
    'https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html' # pylint: disable=line-too-long
    f'{colorama.Style.RESET_ALL}')
RequestId = str

logger = sky_logging.init_logger(__name__)


@dataclasses.dataclass
@functools.total_ordering
class ApiVersion:
    '''API version of SkyPilot client/server.

    SkyPilot processes may use different API versions. This class is used to
    compare the API versions and check if they are compatible. Note that not
    only the version number but also the scheme of the version string might
    evolve. So exception should be explicitly handled when parsing a remote
    version.

    Raises:
        ValueError: If the given version string is using unknown scheme of
        current process.
    '''
    major: int

    def __init__(self, version: str):
        self.major = int(version)


    def __lt__(self, other: 'ApiVersion') -> bool:
        return self.major < other.major

    def __eq__(self, other: 'ApiVersion') -> bool:
        return self.major == other.major
    
    def compabile_with(self, other: 'ApiVersion') -> bool:
        return self == other


# Current API version of the local running process.
_local_version = ApiVersion(server_constants.API_VERSION)


class ApiServerStatus(enum.Enum):
    HEALTHY = 'healthy'
    UNHEALTHY = 'unhealthy'
    VERSION_MISMATCH = 'version_mismatch'

@dataclasses.dataclass
class ApiServerInfo:
    status: ApiServerStatus
    api_version: Optional[ApiVersion]


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
            response = requests.get(f'{server_url}/api/health', timeout=2.5)
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
                    try:
                        server_version = ApiVersion(api_version)
                    except ValueError:
                        logger.warning('API server response unknown version '
                                       f'info {api_version}. Upgrade SkyPilot '
                                       'client and retry.')
                        return ApiServerInfo(status=ApiServerStatus.VERSION_MISMATCH,
                                             api_version=None)
                    if server_version.compabile_with(_local_version):
                        return ApiServerInfo(status=ApiServerStatus.HEALTHY,
                                             api_version=api_version)
                    return ApiServerInfo(
                        status=ApiServerStatus.VERSION_MISMATCH,
                        api_version=server_version)
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


def handle_request_error(response: requests.Response) -> None:
    if response.status_code != 200:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                'Failed to process response from SkyPilot API server at '
                f'{get_server_url()}. '
                f'Response: {response.status_code} '
                f'{response.text}')


def get_request_id(response: requests.Response) -> RequestId:
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
    with rich_utils.client_status('Starting SkyPilot API server'):
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
        subprocess.Popen(cmd, shell=True, start_new_session=True)

        # Wait for the server to start until timeout.
        # Conservative upper time bound for starting the server based on
        # profiling.
        timeout_sec = 12
        start_time = time.time()
        while True:
            api_server_info = get_api_server_status()
            assert api_server_info.status != ApiServerStatus.VERSION_MISMATCH, (
                f'API server version mismatch when starting the server. '
                f'Server version: {api_server_info.api_version} '
                f'Client version: {server_constants.API_VERSION}')
            if api_server_info.status == ApiServerStatus.HEALTHY:
                break
            elif time.time() - start_time >= timeout_sec:
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
        assert api_server_info.api_version is not None, 'API server version is None'
        try:
            remote_version = ApiVersion()
        try:
            server_is_older = int(sv) < int(cv)
        except ValueError:
            # Raised when the server version is not a numeric. A safe
            # assumption is that our client is older since we never used
            # a non-numeric API version before.
            server_is_older = False
        if server_is_older:
            if is_api_server_local():
                hint = RESTART_LOCAL_API_SERVER_HINT
            else:
                hint = UPGRADE_REMOTE_SERVER_HINT
            msg = SKY_SERVER_TOO_OLD_WARNING.format(client_version=cv,
                                                    server_version=sv,
                                                    hint=hint)
        else:
            msg = SKY_CLIENT_TOO_OLD_WARNING.format(client_version=cv,
                                                    server_version=sv)
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(msg)
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


def request_body_to_params(body: pydantic.BaseModel) -> Dict[str, Any]:
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
