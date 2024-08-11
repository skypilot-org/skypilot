"""Common data structures and constants used in the API."""
import functools
import os
import pathlib
import subprocess
import tempfile
import time
import typing
from typing import Dict, Union

import colorama
import filelock
import requests

from sky import sky_logging
from sky import skypilot_config
from sky.data import data_utils
from sky.skylet import constants
from sky.utils import common_utils

if typing.TYPE_CHECKING:
    import sky
    from sky import dag as dag_lib

API_SERVER_REQUEST_DB_PATH = '~/.sky/api_server/tasks.db'
DEFAULT_SERVER_URL = 'http://0.0.0.0:8000'
API_SERVER_CMD = 'python -m sky.api.rest'
CLIENT_DIR = pathlib.Path('~/.sky/clients')

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
                if server_url == DEFAULT_SERVER_URL:
                    logger.info('Failed to connect to SkyPilot API server at '
                                f'{server_url}. Starting a local server.')
                    start_uvicorn_in_background(reload=api_server_reload,
                                                deploy=deploy)
                    logger.info(
                        f'{colorama.Fore.GREEN}SkyPilot API server started.'
                        f'{colorama.Style.RESET_ALL}')
                else:
                    raise RuntimeError(
                        f'Could not connect to SkyPilot server at {server_url}.'
                        ' Please ensure that the server is running and that the'
                        f' {constants.SKY_API_SERVER_URL_ENV_VAR} environment '
                        'variable is set correctly. Try: '
                        f'curl {server_url}/health')
        return func(*args, **kwargs)

    return wrapper


def upload_mounts_to_api_server(
        task: Union['sky.Task', 'sky.Dag']) -> 'dag_lib.Dag':
    from sky.utils import dag_utils  # pylint: disable=import-outside-toplevel

    # TODO(zhwu): upload user config file at `~/.sky/config.yaml`

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
                if src == constants.LOCAL_SKYPILOT_CONFIG_PATH_PLACEHOLDER:
                    # The placeholder for the local skypilot config path is in
                    # file mounts for controllers. It will be replaced with the
                    # real path for config file on API server.
                    pass
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


def process_mounts_in_task(task: str, env_vars: Dict[str,
                                                     str], cluster_name: str,
                           workdir_only: bool) -> 'dag_lib.Dag':
    from sky.utils import dag_utils  # pylint: disable=import-outside-toplevel

    user_hash = env_vars.get(constants.USER_ID_ENV_VAR, 'unknown')

    timestamp = str(int(time.time()))
    client_dir = (CLIENT_DIR.expanduser().resolve() / user_hash)
    client_task_dir = client_dir / 'tasks'
    client_task_dir.mkdir(parents=True, exist_ok=True)

    client_task_path = client_task_dir / f'{cluster_name}-{timestamp}.yaml'
    client_task_path.write_text(task)

    client_file_mounts_dir = client_dir / 'file_mounts'

    task_configs = common_utils.read_yaml_all(str(client_task_path))
    for task_config in task_configs:
        if task_config is None:
            continue
        file_mounts_mapping = task_config.get('file_mounts_mapping', {})
        if not file_mounts_mapping:
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
                        file_mounts[dst] = str(
                            client_file_mounts_dir /
                            file_mounts_mapping[src].lstrip('/'))
                elif isinstance(src, dict):
                    if 'source' in src:
                        source = src['source']
                        if not data_utils.is_cloud_store_url(source):
                            src['source'] = str(
                                client_file_mounts_dir /
                                file_mounts_mapping[source].lstrip('/'))
                else:
                    raise ValueError(f'Unexpected file_mounts value: {src}')

    translated_client_task_path = client_dir / f'{timestamp}_translated.yaml'
    common_utils.dump_yaml(translated_client_task_path, task_configs)

    dag = dag_utils.load_chain_dag_from_yaml(str(translated_client_task_path))
    for task in dag.tasks:
        if task.storage_mounts is not None:
            for storage in task.storage_mounts.values():
                storage.construct()
    return dag
