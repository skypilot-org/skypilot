"""Utilities for managing managed job file content.

The helpers in this module fetch job file content (DAG YAML/env files) from the
database-first storage added for managed jobs, transparently falling back to
legacy on-disk paths when needed. Consumers should prefer the string-based
helpers so controllers never have to rely on local disk state.
"""

import os
from typing import Optional

from sky import sky_logging
from sky.jobs import state as managed_job_state

logger = sky_logging.init_logger(__name__)


def get_job_dag_content(job_id: int) -> Optional[str]:
    """Get DAG YAML content for a job from database or disk.

    Args:
        job_id: The job ID

    Returns:
        DAG YAML content as string, or None if not found
    """
    file_info = managed_job_state.get_job_file_contents(job_id)

    # Prefer content stored in the database
    if file_info['dag_yaml_content'] is not None:
        return file_info['dag_yaml_content']

    # Fallback to disk path for backward compatibility
    dag_yaml_path = file_info.get('dag_yaml_path')
    if dag_yaml_path and os.path.exists(dag_yaml_path):
        try:
            with open(dag_yaml_path, 'r', encoding='utf-8') as f:
                content = f.read()
                logger.debug('Loaded DAG YAML from disk for job %s: %s', job_id,
                             dag_yaml_path)
                return content
        except (FileNotFoundError, IOError, OSError) as e:
            logger.warning(
                f'Failed to read DAG YAML from disk {dag_yaml_path}: {e}')

    logger.warning(f'DAG YAML content not found for job {job_id}')
    return None


def get_job_env_content(job_id: int) -> Optional[str]:
    """Get environment file content for a job from database or disk.

    Args:
        job_id: The job ID

    Returns:
        Environment file content as string, or None if not found
    """
    file_info = managed_job_state.get_job_file_contents(job_id)

    # Prefer content stored in the database
    if file_info['env_file_content'] is not None:
        return file_info['env_file_content']

    # Fallback to disk path for backward compatibility
    env_file_path = file_info.get('env_file_path')
    if env_file_path and os.path.exists(env_file_path):
        try:
            with open(env_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                logger.debug('Loaded environment file from disk for job %s: %s',
                             job_id, env_file_path)
                return content
        except (FileNotFoundError, IOError, OSError) as e:
            logger.warning(
                f'Failed to read environment file from disk {env_file_path}: '
                f'{e}')

    # Environment file is optional, so don't warn if not found
    return None
