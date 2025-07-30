"""Launcher for serve controller.

This module is responsible for starting the serve controller process.
In consolidation mode, it starts the controller and exits immediately.
In non-consolidation mode, it starts the controller and waits for it to finish.
"""
import argparse
import os
import subprocess
import sys

from sky import sky_logging
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)


def start_controller(service_name: str, task_yaml_path: str,
                     job_id: int, consolidation_mode: bool) -> None:
    """Start the serve controller process.
    
    Args:
        service_name: Name of the service
        task_yaml_path: Path to the task yaml file
        job_id: Job ID for the controller (0 in consolidation mode, actual job id otherwise)
        consolidation_mode: Whether running in consolidation mode
    """
    # Build the controller command
    run_controller_cmd = (f'{sys.executable} -u -m sky.serve.service '
                          f'--service-name {service_name} '
                          f'--task-yaml {task_yaml_path} '
                          f'--job-id {job_id}')

    # Use the standard controller log path for consistency
    log_path = os.path.expanduser(
        serve_utils.generate_remote_controller_log_file_name(service_name))

    # Ensure log directory exists
    log_dir = os.path.dirname(log_path)
    os.makedirs(log_dir, exist_ok=True)
    
    if consolidation_mode:
        # In consolidation mode, launch as a separate process tree and exit
        pid = subprocess_utils.launch_new_process_tree(run_controller_cmd,
                                                       log_output=log_path)
        serve_state.set_service_controller_pid(service_name, pid)
        logger.info(f'Serve controller for {service_name} started with PID {pid}')
    else:
        # In non-consolidation mode, run the controller directly
        # This keeps the Ray job alive
        # Open log file for writing
        with open(log_path, 'w') as log_file:
            process = subprocess.Popen(run_controller_cmd,
                                       shell=True,
                                       stdout=log_file,
                                       stderr=subprocess.STDOUT,
                                       universal_newlines=True,
                                       bufsize=1)
            
            # Save the PID immediately
            serve_state.set_service_controller_pid(service_name, process.pid)
            logger.info(f'Serve controller for {service_name} started with PID {process.pid}')
            
            # Wait for the process to complete
            # This blocks until the controller exits, keeping the Ray job alive
            process.wait()


def main():
    """Main entry point for the launcher."""
    parser = argparse.ArgumentParser(
        description='Launch serve controller')
    parser.add_argument('--task-yaml',
                        required=True,
                        help='Path to the task yaml file')
    parser.add_argument('--service-name',
                        required=True,
                        help='Name of the service')
    parser.add_argument('--job-id',
                        type=int,
                        required=True,
                        help='Job ID for the controller')
    parser.add_argument('--consolidation-mode',
                        action='store_true',
                        help='Whether running in consolidation mode')

    args = parser.parse_args()

    try:
        start_controller(args.service_name, args.task_yaml, args.job_id,
                         args.consolidation_mode)
        if args.consolidation_mode:
            logger.info(f'Successfully launched controller for {args.service_name}')
        else:
            logger.info(f'Controller for {args.service_name} has exited')
    except Exception as e:
        logger.error(f'Failed to launch controller: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
