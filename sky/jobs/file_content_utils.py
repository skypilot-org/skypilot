"""Utilities for managing managed job file content.

The helpers in this module fetch job file content (DAG YAML/env files) from the
database-first storage added for managed jobs, transparently falling back to
legacy on-disk paths when needed. Consumers should prefer the string-based
helpers so controllers never have to rely on local disk state.
"""

import os
from typing import Optional

from sky import sky_logging
from sky import skypilot_config
from sky.jobs import state as managed_job_state
from sky.utils import common_utils

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


def restore_job_config_file(job_id: int) -> None:
    """Restore the config file from the database if SKYPILOT_CONFIG is set.

    The file named by the SKYPILOT_CONFIG environment variable is written at
    submission. Its content is also stored in the database, so that a job
    can run on a controller whose disk no longer has the file. This function
    writes the file from the database only when it is missing; an existing
    file is left untouched.

    Replacing an existing file would be unsafe. Jobs submitted as one
    ``--num-jobs`` batch share a single config path, and every job's
    controller runs this restore when its job loop starts. The API server
    reloads the file at the start of every request those jobs send. When the
    file lives on a shared filesystem (several API server replicas on NFS),
    replacing it from one host, even atomically with a rename, makes a reader
    on another host fail: it holds a handle to the old file (``ESTALE``), or
    its stat of the path fails and the file looks missing. Never replacing an
    existing file removes that failure. The file was written from the same
    content the database holds, so nothing is lost by keeping it. A missing
    file is created exclusively, so several jobs recreating it at once
    cannot replace each other's copy either.

    For backward compatibility with jobs submitted before config persistence
    was implemented, a file that exists on disk is used even when the
    database has no content.

    Args:
        job_id: The job ID
    """
    config_path = os.environ.get(skypilot_config.ENV_VAR_SKYPILOT_CONFIG)
    if not config_path:
        # No config file for this job
        return

    # Expand ~ in config path
    config_path_expanded = os.path.expanduser(config_path)

    if os.path.exists(config_path_expanded):
        # The file is already on disk (written at submission, or restored by
        # another job of the same batch). Do not replace it; see the
        # docstring.
        logger.debug(f'Config file for job {job_id} already exists at '
                     f'{config_path_expanded}; not restoring it')
        return

    file_info = managed_job_state.get_job_file_contents(job_id)
    config_content = file_info['config_file_content']

    if config_content is not None:
        # Config content is in database - restore it
        # Ensure the directory exists
        os.makedirs(os.path.dirname(config_path_expanded), exist_ok=True)
        # Several jobs of one batch can restore the same missing path at
        # once, and all of them can pass the check above. The write must be
        # atomic (an O_TRUNC write lets a concurrent reader load a zero-byte
        # file as an empty config) and must not replace a file another job
        # created in between (see the docstring), so it creates the file
        # exclusively: the first job's write wins, the others leave it.
        created = common_utils.atomic_create_text(config_path_expanded,
                                                  config_content,
                                                  mode=0o600)
        if created:
            logger.info(f'Restored config file for job {job_id} to '
                        f'{config_path_expanded} ({len(config_content)} '
                        'bytes)')
        else:
            logger.debug(f'Config file for job {job_id} was restored to '
                         f'{config_path_expanded} by another job meanwhile')
    else:
        # Config should exist but doesn't - warn about it
        logger.warning(
            f'SKYPILOT_CONFIG is set to {config_path} but config content not '
            f'found in database or on disk for job {job_id}. The job may fail '
            f'if it relies on custom config settings.')
