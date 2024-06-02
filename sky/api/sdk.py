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

import colorama
import psutil
import requests

from sky import backends
from sky import optimizer
from sky import sky_logging
from sky.api.requests import tasks
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import dag_utils

if typing.TYPE_CHECKING:
    import sky

logger = sky_logging.init_logger(__name__)

DEFAULT_SERVER_URL = 'http://127.0.0.1:8000'
API_SERVER_CMD = 'python -m sky.api.rest'


def _get_server_url():
    return os.environ.get(constants.SKY_API_SERVER_URL_ENV_VAR,
                          DEFAULT_SERVER_URL)


def _start_uvicorn_in_background():
    log_path = os.path.expanduser(constants.API_SERVER_LOGS)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    # The command to run uvicorn. Adjust the app:app to your application's
    # location.
    cmd = f'{API_SERVER_CMD} > {log_path} 2>&1'

    # Start the uvicorn process in the background and don't wait for it.
    subprocess.Popen(cmd, shell=True)
    # Wait for the server to start.
    while True:
        try:
            requests.get(f'{_get_server_url()}/health', timeout=1)
            break
        except requests.exceptions.ConnectionError:
            time.sleep(0.1)


def _handle_response(response) -> Tuple[str, Dict[str, Any]]:
    if response.status_code != 200:
        raise RuntimeError(
            f'Failed to connect to SkyPilot server at {_get_server_url()}. '
            f'Response: {response.content}')
    request_id = response.headers.get('X-Request-ID')
    return request_id, response.json()


def _check_health(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
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
                _start_uvicorn_in_background()
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
    # Internal only:
    # pylint: disable=invalid-name
    _is_launched_by_jobs_controller: bool = False,
    _is_launched_by_sky_serve_controller: bool = False,
    _disable_controller_check: bool = False,
) -> str:

    dag = dag_utils.convert_entrypoint_to_dag(task)

    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()

    # TODO(zhwu): For all the file_mounts, we need to handle them properly
    # similarly to how we deal with it for spot_launch.
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
        '_is_launched_by_jobs_controller': _is_launched_by_jobs_controller,
        '_is_launched_by_sky_serve_controller': _is_launched_by_sky_serve_controller,
        '_disable_controller_check': _disable_controller_check,
    }
    response = requests.post(
        f'{_get_server_url()}/launch',
        json=body,
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
    request_id, _ = _handle_response(response)
    return request_id


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
    request_id, _ = _handle_response(response)
    return request_id


@usage_lib.entrypoint
@_check_health
def get(request_id: str) -> Any:
    response = requests.get(f'{_get_server_url()}/get',
                            json={'request_id': request_id},
                            timeout=300)
    _, return_value = _handle_response(response)
    request_task = tasks.RequestTask(**return_value)
    if request_task.error:
        # TODO(zhwu): we should have a better way to handle errors.
        # Is it possible to raise the original exception?
        raise RuntimeError(request_task.error)
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
            print(line.decode('utf-8'))
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
    _handle_response(response)
    request_id = response.headers.get('X-Request-ID')
    assert request_id is not None, response
    logger.debug(f'Tearing down {cluster_name!r} with request ID: '
                 f'{request_id}')
    return request_id


# === API server management ===
@usage_lib.entrypoint
@_check_health
def api_start():
    """Start the API server."""
    logger.info(f'SkyPilot API server: {_get_server_url()}')


@usage_lib.entrypoint
def api_stop():
    """Kill the API server."""
    # Kill the uvicorn process by name: uvicorn sky.api.rest:app
    server_url = _get_server_url()
    if server_url != DEFAULT_SERVER_URL:
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
    subprocess.run(['tail', *tail_args, log_path], check=False)
