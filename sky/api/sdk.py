"""Python SDK for SkyPilot."""
import functools
import os
import subprocess
import time
import typing
from typing import List, Optional

import colorama
import psutil
import requests

from sky import backends
from sky import optimizer
from sky import sky_logging
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    from sky import task as task_lib

logger = sky_logging.init_logger(__name__)

DEFAULT_SERVER_URL = 'http://127.0.0.1:8000'
API_SERVER_CMD = 'uvicorn sky.api.rest:app'


def _get_server_url():
    return os.environ.get(constants.SKY_API_SERVER_URL_ENV_VAR,
                          DEFAULT_SERVER_URL)


def _start_uvicorn_in_background():
    log_path = os.path.expanduser(constants.API_SERVER_LOGS)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    # The command to run uvicorn. Adjust the app:app to your application's
    # location.
    cmd = f'{API_SERVER_CMD} &> {log_path}'

    # Start the uvicorn process in the background and don't wait for it.
    subprocess.Popen(cmd, shell=True)
    # Wait for the server to start.
    time.sleep(5)


def _handle_response(response):
    if response.status_code != 200:
        raise RuntimeError(
            f'Failed to connect to SkyPilot server at {_get_server_url()}. '
            f'Response: {response.content}')
    return response.json()


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
    task: 'task_lib.Task',
    cluster_name: Optional[str] = None,
    retry_until_up: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
    dryrun: bool = False,
    down: bool = False,
    stream_logs: bool = True,
    optimize_target: optimizer.OptimizeTarget = optimizer.OptimizeTarget.COST,
    detach_setup: bool = False,
    detach_run: bool = False,
    no_setup: bool = False,
    clone_disk_from: Optional[str] = None,
    # Internal only:
    # pylint: disable=invalid-name
    _is_launched_by_spot_controller: bool = False):

    # TODO(zhwu): For all the file_mounts, we need to handle them properly
    # similarly to how we deal with it for spot_launch.
    requests.post(
        f'{_get_server_url()}/launch',
        json={
            'task': task.to_yaml_config(),
            'cluster_name': cluster_name,
            'retry_until_up': retry_until_up,
            'idle_minutes_to_autostop': idle_minutes_to_autostop,
            'dryrun': dryrun,
            'down': down,
            'stream_logs': stream_logs,
            'optimize_target': optimize_target,
            'detach_setup': detach_setup,
            'detach_run': detach_run,
            'no_setup': no_setup,
            'clone_disk_from': clone_disk_from,
            '_is_launched_by_spot_controller': _is_launched_by_spot_controller,
        },
        timeout=5,
    )


@usage_lib.entrypoint
@_check_health
def status(cluster_names: Optional[List[str]] = None,
           refresh: bool = False) -> List[dict]:
    # TODO(zhwu): this does not stream the logs output by logger back to the
    # user
    response = requests.get(f'{_get_server_url()}/status',
                            json={
                                'cluster_names': cluster_names,
                                'refresh': refresh,
                            },
                            timeout=30)
    logger.debug(f'Request ID: {response.headers.get("X-Request-ID")}')
    clusters = _handle_response(response)
    for cluster in clusters:
        cluster['handle'] = backends.CloudVmRayResourceHandle.from_config(
            cluster['handle'])
        cluster['status'] = status_lib.ClusterStatus(cluster['status'])

    return clusters


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
