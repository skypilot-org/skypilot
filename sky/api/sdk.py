import functools
import os
import subprocess
import time
from typing import List, Optional

import requests

from sky import backends
from sky import optimizer
from sky import sky_logging
from sky import task as task_lib
from sky.skylet import constants
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

DEFAULT_SERVER_URL = 'http://127.0.0.1:8000'


def get_server_url():
    return os.environ.get('SKYPILOT_SERVER_URL', DEFAULT_SERVER_URL)


def _start_uvicorn_in_background():
    log_path = os.path.expanduser(constants.SKY_SERVER_LOGS)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    # The command to run uvicorn. Adjust the app:app to your application's location.
    cmd = f'uvicorn sky.api.rest:app --reload &> {log_path}'

    # Start the uvicorn process in the background and don't wait for it.
    subprocess.Popen(cmd, shell=True)
    time.sleep(1)


def _handle_response(response):
    if response.status_code != 200:
        raise RuntimeError(
            f'Failed to connect to SkyPilot server at {get_server_url()}. '
            f'Response: {response.content}')
    return response.json()


def _check_health(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        server_url = get_server_url()

        try:
            response = requests.get(f'{get_server_url()}/health', timeout=3)
        except requests.exceptions.ConnectionError:
            response = None
        if (response is None or response.status_code != 200):
            if server_url == DEFAULT_SERVER_URL:
                logger.info('Failed to connect to SkyPilot server at '
                            f'{server_url}. Starting a local server.')
                # Automatically start a SkyPilot server locally
                _start_uvicorn_in_background()
            else:
                raise RuntimeError(
                    f'Could not connect to SkyPilot server at {server_url}. '
                    'Please ensure that the server is running and that the '
                    'SKYPILOT_SERVER_URL environment variable is set correctly.'
                )
        return func(*args, **kwargs)

    return wrapper


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

    response = requests.post(
        f'{get_server_url()}/launch',
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
    )


@_check_health
def status(cluster_names: Optional[List[str]] = None,
           refresh: bool = False) -> List[dict]:
    # TODO(zhwu): this does not stream the logs output by logger back to the
    # user
    response = requests.get(f'{get_server_url()}/status',
                            json={
                                'cluster_names': cluster_names,
                                'refresh': refresh,
                            })
    clusters = _handle_response(response)
    for cluster in clusters:
        cluster['handle'] = backends.CloudVmRayResourceHandle.from_config(
            cluster['handle'])
        cluster['status'] = status_lib.ClusterStatus(cluster['status'])

    return clusters
