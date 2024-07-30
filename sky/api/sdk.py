"""Python SDK for SkyPilot.

All the functions will return a future that can be awaited on with the `get`
method. For example:

.. code-block:: python

    request_id = sky.status()
    statuses = sky.get(request_id)

"""
import functools
import os
import subprocess
import tempfile
import time
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

import click
import colorama
import psutil
import requests

from sky import backends
from sky import optimizer
from sky import sky_logging
from sky.api.requests import tasks
from sky.backends import backend_utils
from sky.data import data_utils
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import dag_utils
from sky.utils import env_options
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import sky

logger = sky_logging.init_logger(__name__)

DEFAULT_SERVER_URL = 'http://127.0.0.1:8000'
API_SERVER_CMD = 'python -m sky.api.rest'


@functools.lru_cache()
def _get_server_url():
    return os.environ.get(constants.SKY_API_SERVER_URL_ENV_VAR,
                          DEFAULT_SERVER_URL)


@functools.lru_cache()
def _is_api_server_local():
    return _get_server_url() == DEFAULT_SERVER_URL


def _start_uvicorn_in_background(reload: bool = False):
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
    # Wait for the server to start.
    retry_cnt = 0
    while True:
        try:
            # TODO: Should check the process is running as well.
            requests.get(f'{_get_server_url()}/health', timeout=1)
            break
        except requests.exceptions.ConnectionError:
            if retry_cnt < 20:
                retry_cnt += 1
            else:
                raise RuntimeError(
                    f'Failed to connect to SkyPilot server at {_get_server_url()}. '
                    'Please check the logs for more information: '
                    f'tail -f {constants.API_SERVER_LOGS}')
            time.sleep(0.1)


def _get_request_id(response) -> str:
    if response.status_code != 200:
        raise RuntimeError(
            f'Failed to connect to SkyPilot server at {_get_server_url()}. '
            f'Response: {response.content}')
    request_id = response.headers.get('X-Request-ID')
    return request_id


def _check_health(func):

    @functools.wraps(func)
    def wrapper(*args, api_server_reload: bool = False, **kwargs):
        server_url = _get_server_url()

        try:
            response = requests.get(f'{_get_server_url()}/health', timeout=5)
        except requests.exceptions.ConnectionError:
            response = None
        if (response is None or response.status_code != 200):
            if server_url == DEFAULT_SERVER_URL:
                logger.info('Failed to connect to SkyPilot API server at '
                            f'{server_url}. Starting a local server.')
                # Automatically start a SkyPilot server locally
                _start_uvicorn_in_background(reload=api_server_reload)
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


def _add_env_vars_to_body(body: Dict[str, Any]):
    env_vars = {}
    for env_var in os.environ:
        if env_var.startswith('SKYPILOT_'):
            env_vars[env_var] = os.environ[env_var]
    env_vars[constants.USER_ID_ENV_VAR] = common_utils.get_user_hash()
    body['env_vars'] = env_vars


@usage_lib.entrypoint
@_check_health
def optimize(dag: 'sky.Dag') -> str:
    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()

    body = {
        'dag': dag_str,
    }
    _add_env_vars_to_body(body)
    response = requests.get(f'{_get_server_url()}/optimize', json=body)
    return response.headers['X-Request-ID']


@usage_lib.entrypoint
@_check_health
def launch(
    task: Union['sky.Task', 'sky.Dag'],
    cluster_name: Optional[str] = None,
    retry_until_up: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
    dryrun: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    backend: Optional[backends.Backend] = None,
    optimize_target: optimizer.OptimizeTarget = optimizer.OptimizeTarget.COST,
    detach_setup: bool = False,
    detach_run: bool = False,
    no_setup: bool = False,
    clone_disk_from: Optional[str] = None,
    need_confirmation: bool = False,
    # Internal only:
    # pylint: disable=invalid-name
    _is_launched_by_jobs_controller: bool = False,
    _is_launched_by_sky_serve_controller: bool = False,
    _disable_controller_check: bool = False,
) -> str:

    if cluster_name is None:
        cluster_name = backend_utils.generate_cluster_name()

    # clone_source_str = ''
    # if clone_disk_from is not None:
    #     clone_source_str = f' from the disk of {clone_disk_from!r}'
    #     task, _ = backend_utils.check_can_clone_disk_and_override_task(
    #         clone_disk_from, cluster_name, task)

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
                    file_mounts_mapping[storage_source] = _full_path(storage_source)
        task_.file_mounts_mapping = file_mounts_mapping

    logger.info('Uploading files to API server...')
    with tempfile.NamedTemporaryFile('wb+', suffix='.zip') as f:
        common_utils.zip_files_and_folders(upload_list, f)
        f.seek(0)
        files = {'file': (f.name, f)}
        # Send the POST request with the file
        response = requests.post(
            f'{_get_server_url()}/upload?user_hash={common_utils.get_user_hash()}',
            files=files)
        if response.status_code != 200:
            raise RuntimeError(f'Failed to upload files: {response.content}')

    cluster_status = None
    request_id = status([cluster_name])
    clusters = get(request_id)
    if not clusters:
        # Show the optimize log before the prompt if the cluster does not exist.
        request_id = optimize(dag)
        stream_and_get(request_id)
    else:
        cluster_record = clusters[0]
        cluster_status = cluster_record['status']

    confirm_shown = False
    if need_confirmation:
        # Prompt if (1) --cluster is None, or (2) cluster doesn't exist, or (3)
        # it exists but is STOPPED.
        prompt = None
        if cluster_status is None:
            prompt = (
                f'Launching a new cluster{cluster_name!r}. '
                # '{clone_source_str}. '
                'Proceed?')
        elif cluster_status == status_lib.ClusterStatus.STOPPED:
            prompt = f'Restarting the stopped cluster {cluster_name!r}. Proceed?'
        if prompt is not None:
            confirm_shown = True
            click.confirm(prompt, default=True, abort=True, show_default=True)

    if not confirm_shown:
        click.secho(f'Running task on cluster {cluster_name}...', fg='yellow')

    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()

    body = {
        'task': dag_str,
        'cluster_name': cluster_name,
        'retry_until_up': retry_until_up,
        'idle_minutes_to_autostop': idle_minutes_to_autostop,
        'dryrun': dryrun,
        'down': down,
        'backend': backend.NAME if backend else None,
        'optimize_target': optimize_target.value,
        'detach_setup': detach_setup,
        'detach_run': detach_run,
        'no_setup': no_setup,
        'clone_disk_from': clone_disk_from,
        # For internal use
        'quiet_optimizer': need_confirmation,
        'is_launched_by_jobs_controller': _is_launched_by_jobs_controller,
        'is_launched_by_sky_serve_controller': _is_launched_by_sky_serve_controller,
        'disable_controller_check': _disable_controller_check,
    }
    _add_env_vars_to_body(body)
    response = requests.post(
        f'{_get_server_url()}/launch',
        json=body,
        timeout=5,
    )
    return response.headers['X-Request-ID']


# @usage_lib.entrypoint
# @_check_health
# def exec(task)


@usage_lib.entrypoint
@_check_health
def stop(cluster_name: str, purge: bool = False) -> str:
    """Stop a cluster.

    Data on attached disks is not lost when a cluster is stopped.  Billing for
    the instances will stop, while the disks will still be charged.  Those
    disks will be reattached when restarting the cluster.

    Currently, spot instance clusters cannot be stopped (except for GCP, which
    does allow disk contents to be preserved when stopping spot VMs).

    Args:
        cluster_name: name of the cluster to stop.
        purge: (Advanced) Forcefully mark the cluster as stopped in SkyPilot's
            cluster table, even if the actual cluster stop operation failed on
            the cloud. WARNING: This flag should only be set sparingly in
            certain manual troubleshooting scenarios; with it set, it is the
            user's responsibility to ensure there are no leaked instances and
            related resources.
    """
    response = requests.post(
        f'{_get_server_url()}/stop',
        json={
            'cluster_name': cluster_name,
            'purge': purge,
        },
        timeout=5,
    )
    return response.headers['X-Request-ID']


@usage_lib.entrypoint
@_check_health
def tail_logs(cluster_name: str, job_id: int, follow: bool) -> str:
    response = requests.get(f'{_get_server_url()}/tail_logs',
                            json={
                                'cluster_name': cluster_name,
                                'job_id': job_id,
                                'follow': follow
                            })
    return _get_request_id(response)


@usage_lib.entrypoint
@_check_health
def status(cluster_names: Optional[List[str]] = None,
           refresh: bool = False) -> str:
    # TODO(zhwu): this does not stream the logs output by logger back to the
    # user
    response = requests.get(f'{_get_server_url()}/status',
                            json={
                                'cluster_names': cluster_names,
                                'refresh': refresh,
                            })
    return _get_request_id(response)


@usage_lib.entrypoint
@_check_health
def get(request_id: str) -> Any:
    response = requests.get(f'{_get_server_url()}/get',
                            json={'request_id': request_id},
                            timeout=300)
    request_task = tasks.RequestTask.decode(
        tasks.RequestTaskPayload(**response.json()))
    error = request_task.get_error()
    if error is not None:
        error_obj = error['object']
        if env_options.Options.SHOW_DEBUG_INFO.get():
            logger.error(
                f'=== Traceback on SkyPilot API Server ===\n{error_obj.stacktrace}'
            )
        with ux_utils.print_exception_no_traceback():
            raise error_obj
    return request_task.get_return_value()


@usage_lib.entrypoint
@_check_health
def stream_and_get(request_id: str) -> Any:
    response = requests.get(f'{_get_server_url()}/stream',
                            json={'request_id': request_id},
                            timeout=300,
                            stream=True)

    if response.status_code != 200:
        return get(request_id)
    for line in response.iter_lines():
        if line:
            msg = line.decode('utf-8')
            msg = rich_utils.decode_rich_status(msg)
            if msg is not None:
                print(msg)
    return get(request_id)


@usage_lib.entrypoint
@_check_health
def down(cluster_name: str, purge: bool = False) -> str:
    """Tear down a cluster.

    Tearing down a cluster will delete all associated resources (all billing
    stops), and any data on the attached disks will be lost.  Accelerators
    (e.g., TPUs) that are part of the cluster will be deleted too.

    For local on-prem clusters, this function does not terminate the local
    cluster, but instead removes the cluster from the status table and
    terminates the calling user's running jobs.

    Args:
        cluster_names: names of clusters to down.
        purge: whether to ignore cloud provider errors (if any).

    Returns:
        A dictionary mapping cluster names to request IDs.
    """
    response = requests.post(
        f'{_get_server_url()}/down',
        json={
            'cluster_name': cluster_name,
            'purge': purge,
        },
        timeout=5,
    )
    return _get_request_id(response)


# === API server management ===
@usage_lib.entrypoint
@_check_health
def api_start():
    """Start the API server."""
    logger.info(f'SkyPilot API server: {_get_server_url()}')
    if _is_api_server_local():
        logger.info(
            f'Check API server logs: tail -f {constants.API_SERVER_LOGS}')
        return


@usage_lib.entrypoint
def api_stop():
    """Kill the API server."""
    # Kill the uvicorn process by name: uvicorn sky.api.rest:app
    server_url = _get_server_url()
    if not _is_api_server_local():
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Cannot kill the API server at {server_url} because it is not '
                f'the default SkyPilot API server started locally.')

    found = False
    for process in psutil.process_iter(attrs=['pid', 'cmdline']):
        cmdline = process.info['cmdline']
        if cmdline and API_SERVER_CMD in ' '.join(cmdline):
            process.terminate()
            cnt = 0
            while cnt < 5:
                if not process.is_running():
                    break
                cnt += 1
                time.sleep(1)
            else:
                process.kill()
            found = True

    if found:
        logger.info(f'{colorama.Fore.GREEN}SkyPilot API server stopped.'
                    f'{colorama.Style.RESET_ALL}')
    else:
        logger.info('SkyPilot API server is not running.')


# Use the same args as `docker logs`
@usage_lib.entrypoint
def api_logs(follow: bool = True, tail: str = 'all'):
    """Stream the API server logs."""
    server_url = _get_server_url()
    if server_url != DEFAULT_SERVER_URL:
        raise RuntimeError(
            f'Cannot kill the API server at {server_url} because it is not '
            f'the default SkyPilot API server started locally.')

    tail_args = ['-f'] if follow else []
    if tail == 'all':
        tail_args.extend(['-n', '+1'])
    else:
        try:
            tail_args.extend(['-n', f'{int(tail)}'])
        except ValueError as e:
            raise ValueError(f'Invalid tail argument: {tail}') from e
    log_path = os.path.expanduser(constants.API_SERVER_LOGS)
    subprocess.run(['tail', *tail_args, f'{log_path}'], check=False)
