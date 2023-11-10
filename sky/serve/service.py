"""Main entrypoint to start a service.

This including the controller and load balancer.
"""
import argparse
import multiprocessing
import os
import pathlib
import shutil
import time
import traceback
from typing import Dict, List

import filelock
import yaml

from sky import authentication
from sky import exceptions
from sky import resources
from sky import serve
from sky import sky_logging
from sky import task as task_lib
from sky.backends import cloud_vm_ray_backend
from sky.serve import constants
from sky.serve import controller
from sky.serve import load_balancer
from sky.serve import replica_managers
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

# Use the explicit logger name so that the logger is under the
# `sky.serve.service` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.serve.service')


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
                    logger.info(f'User signal received: {user_signal}')
                except ValueError:
                    logger.warning(
                        f'Unknown signal received: {user_signal}. Ignoring.')
                    user_signal = None
            # Remove the signal file, after reading it.
            signal_file.unlink()
    if user_signal is None:
        return
    assert isinstance(user_signal, serve_utils.UserSignal)
    error_type = user_signal.error_type()
    raise error_type(f'User signal received: {user_signal.value}')


def _cleanup(service_name: str, task_yaml: str) -> bool:
    """Clean up the sky serve replicas, storage, and service record."""
    failed = False
    replica_infos = serve_state.get_replica_infos(service_name)
    info2proc: Dict[replica_managers.ReplicaInfo,
                    multiprocessing.Process] = dict()
    for info in replica_infos:
        p = multiprocessing.Process(target=replica_managers.terminate_cluster,
                                    args=(info.cluster_name,))
        p.start()
        info2proc[info] = p
        # Set replica status to `SHUTTING_DOWN`
        info.status_property.sky_launch_status = (
            replica_managers.ProcessStatus.SUCCEEDED)
        info.status_property.sky_down_status = (
            replica_managers.ProcessStatus.RUNNING)
        serve_state.add_or_update_replica(service_name, info.replica_id, info)
        logger.info(f'Terminating replica {info.replica_id} ...')
    for info, p in info2proc.items():
        p.join()
        if p.exitcode == 0:
            serve_state.remove_replica(service_name, info.replica_id)
            logger.info(f'Replica {info.replica_id} terminated successfully.')
        else:
            # Set replica status to `FAILED_CLEANUP`
            info.status_property.sky_down_status = (
                replica_managers.ProcessStatus.FAILED)
            serve_state.add_or_update_replica(service_name, info.replica_id,
                                              info)
            failed = True
            logger.error(f'Replica {info.replica_id} failed to terminate.')
    try:
        task = task_lib.Task.from_yaml(task_yaml)
        backend = cloud_vm_ray_backend.CloudVmRayBackend()
        backend.teardown_ephemeral_storage(task)
    except Exception as e:  # pylint: disable=broad-except
        logger.error('Failed to clean up storage: '
                     f'{common_utils.format_exception(e)}')
        with ux_utils.enable_traceback():
            logger.error(f'  Traceback: {traceback.format_exc()}')
        failed = True
    return failed


def _start(service_name: str, task_yaml: str, job_id: int):
    """Starts the service."""
    # Generate log file name.
    load_balancer_log_file = os.path.expanduser(
        serve_utils.generate_remote_load_balancer_log_file_name(service_name))

    # Create the service working directory.
    service_dir = os.path.expanduser(
        serve_utils.generate_remote_service_dir_name(service_name))
    os.makedirs(service_dir, exist_ok=True)

    # Generate ssh key pair to avoid race condition when multiple sky.launch
    # are executed at the same time.
    authentication.get_or_generate_keys()

    # Initialize database record for the service.
    service_spec = serve.SkyServiceSpec.from_yaml(task_yaml)
    with open(task_yaml, 'r') as f:
        config = yaml.safe_load(f)
    resources_config = None
    if isinstance(config, dict):
        resources_config = config.get('resources')
    requested_resources = resources.Resources.from_yaml_config(resources_config)
    status = serve_state.ServiceStatus.CONTROLLER_INIT
    if len(serve_state.get_services()) >= serve_utils.NUM_SERVICE_THRESHOLD:
        status = serve_state.ServiceStatus.PENDING
    # Here, the service record might already registered in the database if the
    # controller is UP, but also might not if the controller is STOPPED or not
    # created yet before this service. So we use add_or_update_service here.
    # See sky.execution._register_service_name for more details.
    serve_state.add_or_update_service(service_name,
                                      controller_job_id=job_id,
                                      policy=service_spec.policy_str(),
                                      auto_restart=service_spec.auto_restart,
                                      requested_resources=requested_resources,
                                      status=status)

    controller_process = None
    load_balancer_process = None
    try:
        # Wait until there is a service slot available.
        while True:
            _handle_signal(service_name)
            # Use <= here since we already add this service to database.
            if (len(serve_state.get_services()) <=
                    serve_utils.NUM_SERVICE_THRESHOLD):
                serve_state.set_service_status(
                    service_name, serve_state.ServiceStatus.CONTROLLER_INIT)
                break
            time.sleep(1)

        with filelock.FileLock(
                os.path.expanduser(constants.PORT_SELECTION_FILE_LOCK_PATH)):
            controller_port = common_utils.find_free_port(
                constants.CONTROLLER_PORT_START)
            # Start the controller.
            controller_process = multiprocessing.Process(
                target=controller.run_controller,
                args=(service_name, service_spec, task_yaml, controller_port))
            controller_process.start()
            serve_state.set_service_controller_port(service_name,
                                                    controller_port)

            # TODO(tian): Support HTTPS.
            controller_addr = f'http://localhost:{controller_port}'
            replica_port = int(service_spec.replica_port)
            load_balancer_port = common_utils.find_free_port(
                constants.LOAD_BALANCER_PORT_START)

            # Start the load balancer.
            # TODO(tian): Probably we could enable multiple ports specified in
            # service spec and we could start multiple load balancers.
            # After that, we will have a mapping from replica port to endpoint.
            load_balancer_process = multiprocessing.Process(
                target=ux_utils.RedirectOutputForProcess(
                    load_balancer.run_load_balancer,
                    load_balancer_log_file).run,
                args=(controller_addr, load_balancer_port, replica_port))
            load_balancer_process.start()
            serve_state.set_service_load_balancer_port(service_name,
                                                       load_balancer_port)

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
            serve_state.set_service_status(
                service_name, serve_state.ServiceStatus.FAILED_CLEANUP)
            logger.error(f'Service {service_name} failed to clean up.')
        else:
            shutil.rmtree(service_dir)
            serve_state.remove_service(service_name)
            logger.info(f'Service {service_name} terminated successfully.')


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
                        required=True,
                        type=int,
                        help='Job id for the service job.')
    args = parser.parse_args()
    # We start process with 'spawn', because 'fork' could result in weird
    # behaviors; 'spawn' is also cross-platform.
    multiprocessing.set_start_method('spawn', force=True)
    _start(args.service_name, args.task_yaml, args.job_id)
