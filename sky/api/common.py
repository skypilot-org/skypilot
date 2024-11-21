"""Common data structures and constants used in the API."""
import functools
import os
import pathlib
import subprocess
import tempfile
import time
import typing
from typing import Any, Dict, Optional, Union

import colorama
import filelock
import pydantic
import requests

from sky import sky_logging
from sky import skypilot_config
from sky.data import data_utils
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import rich_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import sky
    from sky import dag as dag_lib

API_SERVER_REQUEST_DB_PATH = '~/.sky/api_server/tasks.db'
DEFAULT_SERVER_URL = 'http://0.0.0.0:46580'
API_SERVER_CMD = 'python -m sky.api.rest'
CLIENT_DIR = pathlib.Path('~/.sky/clients')

logger = sky_logging.init_logger(__name__)


@functools.lru_cache()
def get_server_url():
    url = os.environ.get(
        constants.SKY_API_SERVER_URL_ENV_VAR,
        skypilot_config.get_nested(('api_server', 'endpoint'),
                                   DEFAULT_SERVER_URL))
    return url.rstrip('/')


@functools.lru_cache()
def is_api_server_local():
    return get_server_url() == DEFAULT_SERVER_URL


def is_api_server_running() -> bool:
    try:
        response = requests.get(f'{get_server_url()}/health', timeout=5)
    except requests.exceptions.ConnectionError:
        return False
    return response.status_code == 200


def start_uvicorn_in_background(reload: bool = False, deploy: bool = False):
    log_path = os.path.expanduser(constants.API_SERVER_LOGS)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    # The command to run uvicorn. Adjust the app:app to your application's
    # location.
    api_server_cmd = API_SERVER_CMD
    if reload:
        api_server_cmd += ' --reload'
    if deploy:
        api_server_cmd += ' --deploy'
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
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        'Failed to connect to SkyPilot server at '
                        f'{server_url}. '
                        f'\nView logs at: {constants.API_SERVER_LOGS}') from e
            time.sleep(0.5)


def handle_request_error(response):
    if response.status_code != 200:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                'Failed to process response from SkyPilot server at '
                f'{get_server_url()}. '
                f'Response: {response.status_code} '
                f'{response.text}')


def get_request_id(response) -> str:
    handle_request_error(response)
    return response.headers.get('X-Request-ID')


def check_health(func):

    @functools.wraps(func)
    def wrapper(*args,
                api_server_reload: bool = False,
                deploy: bool = False,
                **kwargs):
        if is_api_server_running():
            return func(*args, **kwargs)
        server_url = get_server_url()

        # Automatically start a SkyPilot server locally.
        # Lock to prevent multiple processes from starting the server at the
        # same time, causing issues with database initialization.
        with filelock.FileLock(
                os.path.expanduser(constants.API_SERVER_CREATION_LOCK_PATH)):
            if not is_api_server_running():
                with rich_utils.client_status('Starting API server'):
                    if server_url == DEFAULT_SERVER_URL:
                        logger.info(f'{colorama.Style.DIM}Failed to connect to '
                                    f'SkyPilot API server at {server_url}. '
                                    'Starting a local server.'
                                    f'{colorama.Style.RESET_ALL}')
                        start_uvicorn_in_background(reload=api_server_reload,
                                                    deploy=deploy)
                        logger.info(
                            ux_utils.finishing_message(
                                'SkyPilot API server started.'))
                    else:
                        with ux_utils.print_exception_no_traceback():
                            raise RuntimeError(
                                'Could not connect to SkyPilot server at '
                                f'{server_url}. Please ensure that the server '
                                'is running and '
                                f'{constants.SKY_API_SERVER_URL_ENV_VAR} '
                                'environment variable is set correctly. Try: '
                                f'curl {server_url}/health')
        return func(*args, **kwargs)

    return wrapper


def upload_mounts_to_api_server(task: Union['sky.Task', 'sky.Dag'],
                                workdir_only: bool = False) -> 'dag_lib.Dag':
    from sky.utils import dag_utils  # pylint: disable=import-outside-toplevel

    # TODO(zhwu): upload user config file at `~/.sky/config.yaml`
    # TODO(zhwu): Handle .skyignore file.

    dag = dag_utils.convert_entrypoint_to_dag(task)

    if is_api_server_local():
        return dag

    def _full_path(src: str) -> str:
        return os.path.abspath(os.path.expanduser(src))

    upload_list = []
    for task_ in dag.tasks:
        task_.file_mounts_mapping = {}
        task_.file_mounts_mapping = {}
        if task_.workdir:
            workdir = task_.workdir
            upload_list.append(_full_path(workdir))
            task_.file_mounts_mapping[workdir] = _full_path(workdir)
        if workdir_only:
            continue
        if task_.file_mounts is not None:
            for src in task_.file_mounts.values():
                if not data_utils.is_cloud_store_url(src):
                    upload_list.append(_full_path(src))
                    task_.file_mounts_mapping[src] = _full_path(src)
                    task_.file_mounts_mapping[src] = _full_path(src)
                if src == constants.LOCAL_SKYPILOT_CONFIG_PATH_PLACEHOLDER:
                    # The placeholder for the local skypilot config path is in
                    # file mounts for controllers. It will be replaced with the
                    # real path for config file on API server.
                    pass
        if task_.storage_mounts is not None:
            for storage in task_.storage_mounts.values():
                storage_source = storage.source
                is_cloud_store_url = (
                    isinstance(storage_source, str) and
                    data_utils.is_cloud_store_url(storage_source))
                if (storage_source is not None and not is_cloud_store_url):
                    if isinstance(storage_source, str):
                        storage_source = [storage_source]
                    for src in storage_source:
                        upload_list.append(_full_path(src))
                        task_.file_mounts_mapping[src] = _full_path(src)
                        task_.file_mounts_mapping[src] = _full_path(src)

    server_url = get_server_url()
    if upload_list:
        with rich_utils.client_status(
                ux_utils.spinner_message('Uploading files to API server')):
            with tempfile.NamedTemporaryFile('wb+', suffix='.zip') as f:
                common_utils.zip_files_and_folders(upload_list, f)
                f.seek(0)
                files = {'file': (f.name, f)}
                # Send the POST request with the file
                response = requests.post(
                    f'{server_url}/upload?'
                    f'user_hash={common_utils.get_user_hash()}',
                    files=files)
                if response.status_code != 200:
                    err_msg = response.content.decode('utf-8')
                    raise RuntimeError(f'Failed to upload files: {err_msg}')

    return dag


def process_mounts_in_task(task: str, env_vars: Dict[str, str],
                           workdir_only: bool) -> 'dag_lib.Dag':
    from sky.utils import dag_utils  # pylint: disable=import-outside-toplevel

    user_hash = env_vars.get(constants.USER_ID_ENV_VAR, 'unknown')

    timestamp = str(int(time.time()))
    client_dir = (CLIENT_DIR.expanduser().resolve() / user_hash)
    client_task_dir = client_dir / 'tasks'
    client_task_dir.mkdir(parents=True, exist_ok=True)

    client_task_path = client_task_dir / f'{timestamp}.yaml'
    client_task_path.write_text(task)

    client_file_mounts_dir = client_dir / 'file_mounts'

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

    translated_client_task_path = client_dir / f'{timestamp}_translated.yaml'
    common_utils.dump_yaml(str(translated_client_task_path), task_configs)

    dag = dag_utils.load_chain_dag_from_yaml(str(translated_client_task_path))
    return dag


def api_server_logs_dir_prefix(user_hash: Optional[str] = None) -> pathlib.Path:
    if user_hash is None:
        user_hash = common_utils.get_user_hash()
    return CLIENT_DIR / user_hash / 'sky_logs'


def request_body_to_params(body: pydantic.BaseModel) -> Dict[str, Any]:
    return {
        k: v for k, v in body.model_dump(mode='json').items() if v is not None
    }
