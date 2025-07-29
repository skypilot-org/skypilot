"""Launcher for serve controller in consolidation mode.

This module is responsible for starting the serve controller process
in consolidation mode. It's similar to sky.jobs.scheduler but simpler
since serve doesn't need scheduling logic.
"""
import argparse
import os
import sys

from sky import sky_logging
from sky.serve import constants
from sky.serve import serve_state
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)


def start_controller(service_name: str, task_yaml_path: str,
                     job_id: int) -> None:
    """Start the serve controller process.
    
    Args:
        service_name: Name of the service
        task_yaml_path: Path to the task yaml file
        job_id: Job ID for the controller (always 0 in consolidation mode)
    """
    # Build the controller command
    run_controller_cmd = (f'{sys.executable} -u -m sky.serve.service '
                          f'--service-name {service_name} '
                          f'--task-yaml {task_yaml_path} '
                          f'--job-id {job_id}')

    logs_dir = os.path.expanduser(constants.SKYSERVE_METADATA_DIR)
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f'{service_name}_controller.log')

    pid = subprocess_utils.launch_new_process_tree(run_controller_cmd,
                                                   log_output=log_path)

    serve_state.set_service_controller_pid(service_name, pid)

    pid_file = os.path.join(logs_dir, f'{service_name}_controller.pid')
    with open(pid_file, 'w') as f:
        f.write(str(pid))

    logger.info(f'Serve controller for {service_name} started with PID {pid}')


def main():
    """Main entry point for the launcher."""
    parser = argparse.ArgumentParser(
        description='Launch serve controller in consolidation mode')
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

    args = parser.parse_args()

    try:
        start_controller(args.service_name, args.task_yaml, args.job_id)
        logger.info(f'Successfully launched controller for {args.service_name}')
    except Exception as e:
        logger.error(f'Failed to launch controller: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
