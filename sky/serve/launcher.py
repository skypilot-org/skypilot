"""The launcher for the service."""

import argparse
from functools import lru_cache
import multiprocessing
import os
import sys

import filelock

from sky.serve import serve_state
from sky.serve import serve_utils
from sky.serve import service
from sky.skylet import constants
from sky.utils import subprocess_utils
from sky.utils import ux_utils

_SKY_SERVE_LAUNCHER_LOCK = '~/.sky/locks/sky_serve_launcher.lock'


@lru_cache(maxsize=1)
def _get_lock_path() -> str:
    path = os.path.expanduser(_SKY_SERVE_LAUNCHER_LOCK)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def launch_service(service_name: str, task_yaml: str, job_id: int,
                   controller_log_file: str) -> None:
    with filelock.FileLock(_get_lock_path()):
        if (len(serve_state.get_services()) >=
                serve_utils.get_num_service_threshold()):
            service.cleanup_storage(task_yaml)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Max number of services reached.')

        success = serve_state.set_service_launcher_init(service_name)
        if not success:
            service.cleanup_storage(task_yaml)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(f'Service {service_name} already exists.')

        activate_python_env_cmd = (
            f'{constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV};')
        start_service_cmd = (
            f'{sys.executable} -u -m sky.serve.service '
            f'--service-name {service_name} --task-yaml {task_yaml} '
            f'--job-id {job_id};')

        # If the command line here is changed, please also update
        # serve_utils._controller_process_alive. The substring
        # `--service-name X` should be in the command.
        run_cmd = (f'{activate_python_env_cmd}'
                   f'{start_service_cmd}')

        pid = subprocess_utils.launch_new_process_tree(
            run_cmd, log_output=controller_log_file)
        serve_state.set_service_controller_pid(service_name, pid)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Sky Serve Service')
    parser.add_argument('--service-name',
                        type=str,
                        help='Name of the service',
                        required=True)
    parser.add_argument('--task-yaml',
                        type=str,
                        help='Task YAML file',
                        required=True)
    parser.add_argument('--job-id',
                        type=int,
                        help='Job id for the service job.',
                        required=True)
    parser.add_argument('--controller-log-file',
                        type=str,
                        help='Controller log file',
                        required=True)
    args = parser.parse_args()
    # We start process with 'spawn', because 'fork' could result in weird
    # behaviors; 'spawn' is also cross-platform.
    multiprocessing.set_start_method('spawn', force=True)
    launch_service(args.service_name, args.task_yaml, args.job_id,
                   args.controller_log_file)
