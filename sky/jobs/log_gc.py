"""Log garbage collection for managed jobs."""

from datetime import datetime
import os
import pathlib
import shutil
import threading
import time

import filelock

from sky import sky_logging
from sky import skypilot_config
from sky.jobs import constants as managed_job_constants
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants as skylet_constants
from sky.utils import context

logger = sky_logging.init_logger(__name__)

# Filelock for garbage collector leader election.
_JOB_CONTROLLER_GC_LOCK_PATH = os.path.expanduser(
    '~/.sky/locks/job_controller_gc.lock')

_DEFAULT_TASK_LOGS_GC_RETENTION_HOURS = 24 * 7
_DEFAULT_CONTROLLER_LOGS_GC_RETENTION_HOURS = 24 * 7
_DEFAULT_STAGING_FILES_GC_RETENTION_HOURS = 24  # 1 day

_LEAST_FREQUENT_GC_INTERVAL_SECONDS = 3600
_MOST_FREQUENT_GC_INTERVAL_SECONDS = 30


def _next_gc_interval(retention_seconds: int) -> int:
    """Get the next GC interval."""
    # Run the GC at least per hour to ensure hourly accuracy and
    # at most per 30 seconds (when retention_seconds is small) to
    # avoid too frequent cleanup.
    return max(min(retention_seconds, _LEAST_FREQUENT_GC_INTERVAL_SECONDS),
               _MOST_FREQUENT_GC_INTERVAL_SECONDS)


def gc_controller_logs_for_job():
    """Garbage collect job and controller logs."""
    while True:
        skypilot_config.reload_config()
        controller_logs_retention = skypilot_config.get_nested(
            ('jobs', 'controller', 'controller_logs_gc_retention_hours'),
            _DEFAULT_CONTROLLER_LOGS_GC_RETENTION_HOURS) * 3600
        # Negative value disables the GC
        if controller_logs_retention >= 0:
            logger.info(f'GC controller logs for job: retention '
                        f'{controller_logs_retention} seconds')
            try:
                finished = False
                while not finished:
                    finished = _clean_controller_logs_with_retention(
                        controller_logs_retention)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error GC controller logs for job: {e}',
                             exc_info=True)
        else:
            logger.info('Controller logs GC is disabled')

        interval = _next_gc_interval(controller_logs_retention)
        logger.info('Next controller logs GC is scheduled after '
                    f'{interval} seconds')
        time.sleep(interval)


def gc_task_logs_for_job():
    """Garbage collect task logs for job."""
    while True:
        skypilot_config.reload_config()
        task_logs_retention = skypilot_config.get_nested(
            ('jobs', 'controller', 'task_logs_gc_retention_hours'),
            _DEFAULT_TASK_LOGS_GC_RETENTION_HOURS) * 3600
        # Negative value disables the GC
        if task_logs_retention >= 0:
            logger.info('GC task logs for job: '
                        f'retention {task_logs_retention} seconds')
            try:
                finished = False
                while not finished:
                    finished = _clean_task_logs_with_retention(
                        task_logs_retention)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error GC task logs for job: {e}', exc_info=True)
        else:
            logger.info('Controller logs GC is disabled')

        interval = _next_gc_interval(task_logs_retention)
        logger.info(f'Next task logs GC is scheduled after {interval} seconds')
        time.sleep(_next_gc_interval(task_logs_retention))


def _clean_controller_logs_with_retention(retention_seconds: int,
                                          batch_size: int = 100):
    """Clean controller logs with retention.

    Returns:
        Whether the GC of this round has finished, False means there might
        still be more controller logs to clean.
    """
    assert batch_size > 0, 'Batch size must be positive'
    jobs = managed_job_state.get_controller_logs_to_clean(retention_seconds,
                                                          batch_size=batch_size)
    job_ids_to_update = []
    for job in jobs:
        job_ids_to_update.append(job['job_id'])
        log_file = managed_job_utils.controller_log_file_for_job(job['job_id'])
        cleaned_at = time.time()
        if os.path.exists(log_file):
            ts_str = datetime.fromtimestamp(cleaned_at).strftime(
                '%Y-%m-%d %H:%M:%S')
            msg = f'Controller log has been cleaned at {ts_str}.'
            # Sync down logs will reference to this file directly, so we
            # keep the file and delete the content.
            # TODO(aylei): refactor sync down logs if the inode usage
            # becomes an issue.
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(msg + '\n')
    # Batch the update, the timestamp will be not accurate but it's okay.
    managed_job_state.set_controller_logs_cleaned(job_ids=job_ids_to_update,
                                                  logs_cleaned_at=time.time())
    complete = len(jobs) < batch_size
    logger.info(f'Cleaned {len(jobs)} controller logs with retention '
                f'{retention_seconds} seconds, complete: {complete}')
    return complete


def _clean_task_logs_with_retention(retention_seconds: int,
                                    batch_size: int = 100):
    """Clean task logs with retention.

    Returns:
        Whether the GC of this round has finished, False means there might
        still be more task logs to clean.
    """
    assert batch_size > 0, 'Batch size must be positive'
    tasks = managed_job_state.get_task_logs_to_clean(retention_seconds,
                                                     batch_size=batch_size)
    tasks_to_update = []
    for task in tasks:
        local_log_file = pathlib.Path(task['local_log_file'])
        # We assume the log directory has the following layout:
        # task-id/
        #   - run.log
        #   - tasks/
        #     - run.log
        # and also remove the tasks directory on cleanup.
        task_log_dir = local_log_file.parent.joinpath('tasks')
        local_log_file.unlink(missing_ok=True)
        shutil.rmtree(task_log_dir, ignore_errors=True)
        # We have at least once semantic guarantee for the cleanup here.
        tasks_to_update.append((task['job_id'], task['task_id']))
    managed_job_state.set_task_logs_cleaned(tasks=list(tasks_to_update),
                                            logs_cleaned_at=time.time())
    complete = len(tasks) < batch_size
    logger.info(f'Cleaned {len(tasks)} task logs with retention '
                f'{retention_seconds} seconds, complete: {complete}')
    return complete


def gc_staging_files():
    """Garbage collect staging files for managed jobs.

    In consolidation mode, file mounts are staged to ~/.sky/tmp/controller/
    before being synced to job clusters. These directories are not automatically
    cleaned up after job completion, causing disk space issues over time.

    This GC cleans up staging directories based on their modification time.
    """
    while True:
        skypilot_config.reload_config()
        staging_files_retention = skypilot_config.get_nested(
            ('jobs', 'controller', 'staging_files_gc_retention_hours'),
            _DEFAULT_STAGING_FILES_GC_RETENTION_HOURS) * 3600
        # Negative value disables the GC
        if staging_files_retention >= 0:
            logger.info('GC staging files: '
                        f'retention {staging_files_retention} seconds')
            try:
                _clean_staging_files_with_retention(staging_files_retention)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error GC staging files: {e}', exc_info=True)
        else:
            logger.info('Staging files GC is disabled')

        interval = _next_gc_interval(staging_files_retention)
        logger.info(
            f'Next staging files GC is scheduled after {interval} seconds')
        time.sleep(interval)


def _clean_staging_files_with_retention(retention_seconds: int):
    """Clean staging files with retention.

    Staging files are stored in ~/.sky/tmp/controller/{run_id}/ directories.
    We clean up directories whose modification time is older than the retention
    period.
    """
    staging_base_dir = pathlib.Path(
        skylet_constants.FILE_MOUNTS_CONTROLLER_TMP_BASE_PATH).expanduser()

    if not staging_base_dir.exists():
        logger.debug(f'Staging directory {staging_base_dir} does not exist, '
                     'nothing to clean')
        return

    current_time = time.time()
    cleaned_count = 0
    total_size_freed = 0

    # Iterate through all run_id directories
    for run_id_dir in staging_base_dir.iterdir():
        if not run_id_dir.is_dir():
            continue

        try:
            # Use modification time of the directory
            mtime = run_id_dir.stat().st_mtime
            age_seconds = current_time - mtime

            if age_seconds > retention_seconds:
                # Calculate size before removal for logging
                dir_size = sum(f.stat().st_size
                               for f in run_id_dir.rglob('*')
                               if f.is_file())
                shutil.rmtree(run_id_dir, ignore_errors=True)
                cleaned_count += 1
                total_size_freed += dir_size
                logger.debug(f'Cleaned staging directory: {run_id_dir}')
        except (OSError, IOError) as e:
            # Directory might have been removed by another process
            logger.debug(f'Error processing {run_id_dir}: {e}')
            continue

    if cleaned_count > 0:
        size_mb = total_size_freed / (1024 * 1024)
        logger.info(f'Cleaned {cleaned_count} staging directories, '
                    f'freed {size_mb:.2f} MB')


@context.contextual
def run_log_gc():
    """Run the log garbage collector."""
    log_dir = os.path.expanduser(managed_job_constants.JOBS_CONTROLLER_LOGS_DIR)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'garbage_collector.log')
    # Remove previous log file
    pathlib.Path(log_path).unlink(missing_ok=True)
    ctx = context.get()
    assert ctx is not None, 'Context is not initialized'
    ctx.redirect_log(pathlib.Path(log_path))
    tasks = []
    tasks.append(
        threading.Thread(target=gc_controller_logs_for_job, daemon=True))
    tasks.append(threading.Thread(target=gc_task_logs_for_job, daemon=True))
    tasks.append(threading.Thread(target=gc_staging_files, daemon=True))
    for task in tasks:
        task.start()
    for task in tasks:
        task.join()


def elect_for_log_gc():
    """Use filelock to elect for the log garbage collector.

    The log garbage collector runs in the controller process to avoid the
    overhead of launching a new process and the lifecycle management, the
    threads that does not elected as the log garbage collector just wait.
    on the filelock and bring trivial overhead.
    """
    with filelock.FileLock(_JOB_CONTROLLER_GC_LOCK_PATH):
        run_log_gc()
