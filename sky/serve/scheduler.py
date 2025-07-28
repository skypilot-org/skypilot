"""Serve controller scheduler for consolidation mode."""
import os
import sys
from typing import Dict, Optional

from sky import sky_logging
from sky.serve import constants as serve_constants
from sky.serve import serve_utils
from sky.skylet import constants
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)


def start_controller(service_name: str, task_yaml_path: str,
                     service_id: Optional[int], envs: Optional[Dict[str, str]] = None) -> None:
    """Start the serve controller process in consolidation mode."""
    if not serve_utils.is_consolidation_mode():
        return

    activate_python_env_cmd = f'{constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV};'
    
    env_cmds = []
    if envs:
        env_cmds = [f'export {k}={v!r}' for k, v in envs.items()]
    
    run_controller_cmd = (f'{sys.executable} -u -m sky.serve.service '
                          f'--service-name {service_name} '
                          f'--task-yaml {task_yaml_path} '
                          f'--job-id {service_id}')

    run_cmd = ';'.join([activate_python_env_cmd] + env_cmds + [run_controller_cmd])

    logs_dir = os.path.expanduser(serve_constants.SKYSERVE_METADATA_DIR)
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f'{service_name}_controller.log')

    pid = subprocess_utils.launch_new_process_tree(run_cmd, log_output=log_path)
    
    logger.debug(f'Service {service_name} controller started with pid {pid}')