"""Common data structures and constants used in the API."""
import functools
import os
import subprocess
import tempfile
import time
from typing import Union

import colorama
import requests

import sky
from sky import dag as dag_lib
from sky import sky_logging
from sky import skypilot_config
from sky.data import data_utils
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import dag_utils

API_SERVER_REQUEST_DB_PATH = '~/.sky/api_server/tasks.db'
DEFAULT_SERVER_URL = 'http://0.0.0.0:8000'
API_SERVER_CMD = 'python -m sky.api.rest'

logger = sky_logging.init_logger(__name__)


@functools.lru_cache()
def get_server_url():
    return os.environ.get(
        constants.SKY_API_SERVER_URL_ENV_VAR,
        skypilot_config.get_nested(('api_server', 'endpoint'),
                                   DEFAULT_SERVER_URL))


@functools.lru_cache()
def is_api_server_local():
    return get_server_url() == DEFAULT_SERVER_URL


def start_uvicorn_in_background(reload: bool = False):
    log_path = os.path.expanduser(constants.API_SERVER_LOGS)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    # The command to run uvicorn. Adjust the app:app to your application's
    # location.
    api_server_cmd = API_SERVER_CMD
    if reload:
        api_server_cmd += ' --reload'
    cmd = f'{api_server_cmd} > {log_path} 2>&1'

    # Start the uvicorn process in the background and don't wait for it.
    subprocess.Popen(cmd, shell=True)
    server_url = get_server_url()
    # Wait for the server to start.
    retry_cnt = 0
    while True:
        try:
            # TODO: Should check the process is running as well.
            requests.get(f'{server_url}/health', timeout=1)
            break
        except requests.exceptions.ConnectionError as e:
            if retry_cnt < 20:
                retry_cnt += 1
            else:
                raise RuntimeError(
                    f'Failed to connect to SkyPilot server at {server_url}. '
                    'Please check the logs for more information: '
                    f'tail -f {constants.API_SERVER_LOGS}') from e
            time.sleep(0.5)


def get_request_id(response) -> str:
    if response.status_code != 200:
        raise RuntimeError(
            f'Failed to connect to SkyPilot server at {get_server_url()}. '
            f'Response: {response.content}')
    request_id = response.headers.get('X-Request-ID')
    return request_id


def check_health(func):

    @functools.wraps(func)
    def wrapper(*args, api_server_reload: bool = False, **kwargs):
        server_url = get_server_url()

        try:
            response = requests.get(f'{get_server_url()}/health', timeout=5)
        except requests.exceptions.ConnectionError:
            response = None
        if (response is None or response.status_code != 200):
            if server_url == DEFAULT_SERVER_URL:
                logger.info('Failed to connect to SkyPilot API server at '
                            f'{server_url}. Starting a local server.')
                # Automatically start a SkyPilot server locally
                start_uvicorn_in_background(reload=api_server_reload)
                logger.info(f'{colorama.Fore.GREEN}SkyPilot API server started.'
                            f'{colorama.Style.RESET_ALL}')
            else:
                raise RuntimeError(
                    f'Could not connect to SkyPilot server at {server_url}. '
                    'Please ensure that the server is running and that the '
                    f'{constants.SKY_API_SERVER_URL_ENV_VAR} environment '
                    f'variable is set correctly. Try: curl {server_url}/health')
        return func(*args, **kwargs)

    return wrapper


@functools.lru_cache()
def request_body_env_vars() -> dict:
    env_vars = {}
    for env_var in os.environ:
        if env_var.startswith('SKYPILOT_'):
            env_vars[env_var] = os.environ[env_var]
    env_vars[constants.USER_ID_ENV_VAR] = common_utils.get_user_hash()
    return env_vars


def upload_mounts_to_api_server(
        task: Union['sky.Task', 'sky.Dag']) -> 'dag_lib.Dag':
    dag = dag_utils.convert_entrypoint_to_dag(task)

    def _full_path(src: str) -> str:
        return os.path.abspath(os.path.expanduser(src))

    upload_list = []
    # if not _is_api_server_local():
    for task_ in dag.tasks:
        file_mounts_mapping = {}
        if task_.workdir:
            workdir = task_.workdir
            upload_list.append(_full_path(workdir))
            file_mounts_mapping[workdir] = _full_path(workdir)
        if task_.file_mounts is not None:
            for src in task_.file_mounts.values():
                if not data_utils.is_cloud_store_url(src):
                    upload_list.append(_full_path(src))
                    file_mounts_mapping[src] = _full_path(src)
        if task_.storage_mounts is not None:
            for storage in task_.storage_mounts.values():
                storage_source = storage.source
                if not data_utils.is_cloud_store_url(storage_source):
                    upload_list.append(_full_path(storage_source))
                    file_mounts_mapping[storage_source] = _full_path(
                        storage_source)
        task_.file_mounts_mapping = file_mounts_mapping

    server_url = get_server_url()
    if upload_list:
        logger.info('Uploading files to API server...')
        with tempfile.NamedTemporaryFile('wb+', suffix='.zip') as f:
            common_utils.zip_files_and_folders(upload_list, f)
            f.seek(0)
            files = {'file': (f.name, f)}
            # Send the POST request with the file
            response = requests.post(
                f'{server_url}/upload?user_hash={common_utils.get_user_hash()}',
                files=files)
            if response.status_code != 200:
                err_msg = response.content.decode('utf-8')
                raise RuntimeError(f'Failed to upload files: {err_msg}')

    return dag
