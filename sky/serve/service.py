"""Service: Control both the controller and load balancer."""
import argparse
import multiprocessing
import os
import pathlib
import shutil
import time
from typing import Dict, List

import filelock

from sky import authentication
from sky import exceptions
from sky import serve
from sky import task as task_lib
from sky.backends import cloud_vm_ray_backend
from sky.serve import constants
from sky.serve import controller
from sky.serve import infra_providers
from sky.serve import load_balancer
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.utils import subprocess_utils


def _handle_signal(service_name: str) -> None:
    """Handles the signal user sent to controller."""
    signal_file = pathlib.Path(constants.SIGNAL_FILE_PATH.format(service_name))
    user_signal = None
    if signal_file.exists():
        # Filelock is needed to prevent race condition with concurrent
        # signal writing.
        with filelock.FileLock(str(signal_file) + '.lock'):
            with signal_file.open(mode='r') as f:
                user_signal_text = f.read().strip()
                try:
                    user_signal = serve_utils.UserSignal(user_signal_text)
                except ValueError:
                    user_signal = None
            # Remove the signal file, after reading it.
            signal_file.unlink()
    if user_signal is None:
        return
    assert isinstance(user_signal, serve_utils.UserSignal)
    error_type = user_signal.error_type()
    raise error_type(f'User signal received: {user_signal.value}')


def _cleanup(service_name: str, task_yaml: str) -> bool:
    failed = False
    replica_infos = serve_state.get_replica_infos(service_name)
    info2proc: Dict[infra_providers.ReplicaInfo,
                    multiprocessing.Process] = dict()
    for info in replica_infos:
        p = multiprocessing.Process(target=infra_providers.terminate_cluster,
                                    args=(info.cluster_name,))
        p.start()
        info2proc[info] = p
        info.status_property.sky_launch_status = (
            infra_providers.ProcessStatus.SUCCEEDED)
        info.status_property.sky_down_status = (
            infra_providers.ProcessStatus.RUNNING)
        serve_state.add_or_update_replica(service_name, info.replica_id, info)
    for info, p in info2proc.items():
        p.join()
        if p.exitcode == 0:
            serve_state.remove_replica(service_name, info.replica_id)
        else:
            info.status_property.sky_down_status = (
                infra_providers.ProcessStatus.FAILED)
            serve_state.add_or_update_replica(service_name, info.replica_id,
                                              info)
            failed = True
    task = task_lib.Task.from_yaml(task_yaml)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.teardown_ephemeral_storage(task)
    return failed


def _start(service_name: str, service_dir: str, task_yaml: str,
           controller_port: int, load_balancer_port: int,
           controller_log_file: str, load_balancer_log_file: str):
    """Starts the service."""
    # Create the service working directory if it does not exist.
    service_dir = os.path.expanduser(service_dir)
    os.makedirs(service_dir, exist_ok=True)

    # Generate ssh key pair to avoid race condition when multiple sky.launch
    # are executed at the same time.
    authentication.get_or_generate_keys()

    service_spec = serve.SkyServiceSpec.from_yaml(task_yaml)

    controller_process = None
    load_balancer_process = None
    try:
        _handle_signal(service_name)
        # Start the controller.
        controller_process = multiprocessing.Process(
            target=serve_utils.RedirectOutputTo(controller.run_controller,
                                                controller_log_file).run,
            args=(service_name, service_spec, task_yaml, controller_port))
        controller_process.start()

        # Sleep for a while to make sure the controller is up.
        time.sleep(10)

        # TODO(tian): Support HTTPS.
        controller_addr = f'http://localhost:{controller_port}'
        replica_port = int(service_spec.replica_port)
        # Start the load balancer.
        load_balancer_process = multiprocessing.Process(
            target=serve_utils.RedirectOutputTo(load_balancer.run_load_balancer,
                                                load_balancer_log_file).run,
            args=(controller_addr, load_balancer_port, replica_port))
        load_balancer_process.start()

        while True:
            _handle_signal(service_name)
            time.sleep(1)
    except exceptions.ServeUserTerminatedError:
        serve_state.set_service_status(service_name,
                                       serve_state.ServiceStatus.SHUTTING_DOWN)
    finally:
        process_to_kill: List[multiprocessing.Process] = []
        if load_balancer_process is not None:
            process_to_kill.append(load_balancer_process)
        if controller_process is not None:
            process_to_kill.append(controller_process)
        # Kill load balancer process first since it will raise errors if failed
        # to connect to the controller. Then the controller process.
        subprocess_utils.kill_children_processes(
            [process.pid for process in process_to_kill], force=True)
        for process in process_to_kill:
            process.join()
        failed = _cleanup(service_name, task_yaml)
        if failed:
            serve_state.set_service_status(service_name,
                                           serve_state.ServiceStatus.FAILED)
        else:
            shutil.rmtree(service_dir)
            serve_state.remove_service(service_name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SkyServe Controller')
    parser.add_argument('--service-name',
                        type=str,
                        help='Name of the service',
                        required=True)
    parser.add_argument('--service-dir',
                        type=str,
                        help='Working directory of the service',
                        required=True)
    parser.add_argument('--task-yaml',
                        type=str,
                        help='Task YAML file',
                        required=True)
    parser.add_argument('--controller-port',
                        type=int,
                        help='Port to run the controller',
                        required=True)
    parser.add_argument('--load-balancer-port',
                        type=int,
                        help='Port to run the load balancer on.',
                        required=True)
    parser.add_argument('--controller-log-file',
                        type=str,
                        help='Log file path for the controller',
                        required=True)
    parser.add_argument('--load-balancer-log-file',
                        type=str,
                        help='Log file path for the load balancer',
                        required=True)
    args = parser.parse_args()
    # We start process with 'spawn', because 'fork' could result in weird
    # behaviors; 'spawn' is also cross-platform.
    multiprocessing.set_start_method('spawn', force=True)
    _start(args.service_name, args.service_dir, args.task_yaml,
           args.controller_port, args.load_balancer_port,
           args.controller_log_file, args.load_balancer_log_file)
